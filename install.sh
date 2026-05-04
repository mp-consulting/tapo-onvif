#!/usr/bin/env bash
# Bootstrap script: brew installs, Python venv, friendly launchd hint.
# Idempotent — safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

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
"$VENV/bin/pip" install --quiet pytapo

if [ ! -f "$HERE/src/.env" ]; then
  echo "→ copying .env.example → src/.env (edit it before launching!)"
  cp "$HERE/.env.example" "$HERE/src/.env"
fi

chmod +x "$HERE/src/run_bridge.sh"

cat <<EOF

✔ install complete.

Next steps:
  1. Edit $HERE/src/.env with your TP-Link cloud credentials and the cam IP.
  2. Test the bridge interactively:
       $HERE/src/run_bridge.sh
  3. To auto-start at login:
       cp $HERE/config/com.tapo.bridge.plist.example \
          ~/Library/LaunchAgents/com.tapo.bridge.plist
       # edit ~/Library/LaunchAgents/com.tapo.bridge.plist — replace /Users/YOU
       launchctl load -w ~/Library/LaunchAgents/com.tapo.bridge.plist

For Linux: drop the macOS h264_videotoolbox HW encoder for libx264, replace
launchd with systemd. The python files are portable as-is.

EOF
