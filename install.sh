#!/usr/bin/env bash
# Bootstrap script: brew installs, Python venv, friendly launchd hint.
# Idempotent — safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

PLIST_SRC="$HERE/config/com.tapo.onvif.plist.example"
PLIST_DST="$HOME/Library/LaunchAgents/com.tapo.onvif.plist"
# Pre-rename plist label/path; install_launchd() unloads it if present.
PLIST_DST_LEGACY="$HOME/Library/LaunchAgents/com.tapo.bridge.plist"

install_launchd() {
  [ -f "$PLIST_SRC" ] || { echo "missing $PLIST_SRC"; exit 1; }
  mkdir -p "$(dirname "$PLIST_DST")"
  if [ -f "$PLIST_DST_LEGACY" ]; then
    echo "→ removing legacy $PLIST_DST_LEGACY"
    launchctl unload "$PLIST_DST_LEGACY" 2>/dev/null || true
    rm -f "$PLIST_DST_LEGACY"
  fi
  if [ -f "$PLIST_DST" ]; then
    echo "→ unloading existing $PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
  fi
  echo "→ writing $PLIST_DST"
  BRIDGE_DIR="$HERE" HOME_DIR="$HOME" python3 - "$PLIST_SRC" "$PLIST_DST" <<'PY'
# XML-escape the substituted paths — a $HOME containing '&', '<', or
# '>' would otherwise produce a malformed plist that launchctl rejects.
import html, os, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    rendered = f.read() \
        .replace("__BRIDGE_DIR__", html.escape(os.environ["BRIDGE_DIR"])) \
        .replace("__HOME__",       html.escape(os.environ["HOME_DIR"]))
with open(dst, "w") as f:
    f.write(rendered)
PY
  echo "→ launchctl load -w $PLIST_DST"
  launchctl load -w "$PLIST_DST"
  echo "✔ launchd agent loaded. Logs: $HERE/tmp/tapo-launchd.{log,err}"
}

if [ "${1:-}" = "launchd" ]; then
  install_launchd
  exit 0
fi

echo "→ checking prerequisites"
command -v brew >/dev/null || {
  echo "Homebrew not found — install from https://brew.sh first"; exit 1; }

echo "→ brew install ffmpeg mediamtx (skips if present)"
brew list ffmpeg   >/dev/null 2>&1 || brew install ffmpeg
brew list mediamtx >/dev/null 2>&1 || brew install mediamtx

VENV="${VENV:-/tmp/tapo_venv}"
echo "→ python venv at $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet pytapo pyyaml

if [ ! -f "$HERE/src/.env" ] && [ ! -f "$HERE/.env" ]; then
  echo "→ copying .env.example → .env (edit it before launching!)"
  cp "$HERE/.env.example" "$HERE/.env"
fi

if [ ! -f "$HERE/config/cameras.yml" ]; then
  echo "→ copying cameras.yml.example → cameras.yml (edit it before launching!)"
  cp "$HERE/config/cameras.yml.example" "$HERE/config/cameras.yml"
fi

chmod +x "$HERE/src/run_bridge.sh"

cat <<EOF

✔ install complete.

Next steps:
  1. Edit $HERE/src/.env with your TP-Link cloud credentials and the cam IP.
  2. Test the bridge interactively:
       $HERE/src/run_bridge.sh
  3. To auto-start at login:
       $HERE/install.sh launchd

For Linux: drop the macOS h264_videotoolbox HW encoder for libx264, replace
launchd with systemd. The python files are portable as-is.

EOF
