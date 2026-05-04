#!/usr/bin/env bash
# Pipeline launcher — designed to die when any bridge dies so launchd
# (or systemd / docker / supervisor) can restart the whole stack.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Optional .env file lookup (search common locations)
for f in "$HERE/.env" "$HERE/../.env" "$HOME/.config/tapo-bridge/.env"; do
  if [ -f "$f" ]; then
    set -a; . "$f"; set +a
    break
  fi
done

PYTHON_BIN="${PYTHON_BIN:-/tmp/tapo_venv/bin/python}"
MEDIAMTX_BIN="${MEDIAMTX_BIN:-mediamtx}"
MEDIAMTX_TEMPLATE="${MEDIAMTX_TEMPLATE:-$HERE/../config/mediamtx.yml.template}"
LOG_DIR="${LOG_DIR:-$HERE/../tmp}"
mkdir -p "$LOG_DIR"

# Render mediamtx config from template, substituting the external read
# credentials from .env. mediamtx doesn't expand ${VAR} in auth fields,
# so we do it ourselves. Python (not sed) so passwords containing
# &, |, /, \ don't corrupt the substitution. The internal publish cred
# stays hardcoded (loopback-only — see template).
MEDIAMTX_CONFIG="$LOG_DIR/mediamtx.yml"
: "${READ_USER:?run_bridge.sh: READ_USER missing in .env}"
: "${READ_PASS:?run_bridge.sh: READ_PASS missing in .env}"
"$PYTHON_BIN" - "$MEDIAMTX_TEMPLATE" "$MEDIAMTX_CONFIG" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    rendered = f.read() \
        .replace("__READ_USER__", os.environ["READ_USER"]) \
        .replace("__READ_PASS__", os.environ["READ_PASS"])
with open(dst, "w") as f:
    f.write(rendered)
os.chmod(dst, 0o600)
PY

# Enumerate cameras from cameras.yml. The python helper exits non-zero
# with a clear error if the file is missing or malformed.
CAMS=$("$PYTHON_BIN" "$HERE/_cameras.py" names) || exit 1
[ -n "$CAMS" ] || { echo "→ no cameras configured in cameras.yml"; exit 1; }

PIDS=()
BRIDGE_PIDS=()
cleanup() {
  trap '' EXIT INT TERM
  echo "→ cleanup: killing all children"
  for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  sleep 1
  for pid in "${PIDS[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
  exit 1
}
trap cleanup EXIT INT TERM

echo "→ starting mediamtx ($MEDIAMTX_BIN $MEDIAMTX_CONFIG)"
"$MEDIAMTX_BIN" "$MEDIAMTX_CONFIG" >"$LOG_DIR/mediamtx.log" 2>&1 &
PIDS+=($!)
sleep 1

# One bridge process per camera; each tails into its own log file.
FIRST_CAM=""
while IFS= read -r CAM; do
  [ -z "$CAM" ] && continue
  [ -z "$FIRST_CAM" ] && FIRST_CAM="$CAM"
  echo "→ starting tapo bridge for $CAM"
  "$PYTHON_BIN" "$HERE/tapo_to_rtsp.py" --camera "$CAM" \
    >"$LOG_DIR/tapo-bridge-$CAM.log" 2>&1 &
  PID=$!
  PIDS+=($PID)
  BRIDGE_PIDS+=($PID)
done <<< "$CAMS"

echo "→ waiting for first cam stream to come up (~12 s)"
for i in $(seq 1 30); do
  if grep -q "is publishing to path '${FIRST_CAM}_" "$LOG_DIR/mediamtx.log" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "→ starting JPEG snapshot server"
"$PYTHON_BIN" "$HERE/snapshot_server.py" >"$LOG_DIR/snapshot.log" 2>&1 &
PIDS+=($!)

echo "→ starting ONVIF server"
"$PYTHON_BIN" "$HERE/onvif_server.py" >"$LOG_DIR/onvif.log" 2>&1 &
PIDS+=($!)

echo "✔ all daemons started; waiting on bridges (pids ${BRIDGE_PIDS[*]})"
# Exit as soon as ANY bridge dies, so launchd restarts the whole stack.
while true; do
  for pid in "${BRIDGE_PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "→ bridge pid $pid exited; shutting down stack"
      exit 1
    fi
  done
  sleep 2
done
