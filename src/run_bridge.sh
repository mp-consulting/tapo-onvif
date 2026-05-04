#!/usr/bin/env bash
# Pipeline launcher — designed to die when the python bridge dies so launchd
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
MEDIAMTX_CONFIG="${MEDIAMTX_CONFIG:-$HERE/../config/mediamtx.yml}"
LOG_DIR="${LOG_DIR:-/tmp}"

PIDS=()
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

echo "→ starting tapo bridge"
"$PYTHON_BIN" "$HERE/tapo_to_rtsp.py" >"$LOG_DIR/tapo-bridge.log" 2>&1 &
BRIDGE_PID=$!
PIDS+=($BRIDGE_PID)

echo "→ waiting for cam stream (~12 s)"
for i in $(seq 1 30); do
  if grep -q "is publishing to path 'c675d_wide'" "$LOG_DIR/mediamtx.log" 2>/dev/null; then
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

echo "✔ all daemons started; waiting on bridge (pid $BRIDGE_PID)"
wait "$BRIDGE_PID"
echo "→ tapo bridge exited (rc=$?); shutting down stack"
exit 1
