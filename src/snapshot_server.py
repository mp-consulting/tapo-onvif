#!/usr/bin/env python3
"""HTTP server for JPEG snapshots of every camera-lens defined in
config/cameras.yml.

One long-running ffmpeg subprocess per (camera, lens) atomically rewrites
`<SNAP_DIR>/<name>_<kind>.jpg` at 1 fps. The HTTP handler just serves the
latest file. Files are written atomically (`.tmp` + rename) so the
handler never sees a partial JPEG.

Endpoints: /<name>_<kind>   e.g. /maison_guest_wide
"""
import http.server
import socketserver
import subprocess
import sys
import threading
import time
import os
import signal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _env import load_dotenv
from _cameras import load_cameras

ENV  = load_dotenv(HERE)
CAMS = load_cameras(HERE)
PORT          = int(ENV.get("SNAPSHOT_PORT", "8683"))
RTSP_HOST     = ENV.get("RTSP_HOST",      "127.0.0.1")
RTSP_PORT     = int(ENV.get("RTSP_PORT",  "8555"))
PUBLISH_USER  = ENV.get("PUBLISH_USER",   "publish")
PUBLISH_PASS  = ENV.get("PUBLISH_PASS",   "publish")
SNAP_DIR      = ENV.get("SNAP_DIR",       "/tmp/tapo_snaps")
FFMPEG_BIN    = ENV.get("FFMPEG_BIN",     "/opt/homebrew/bin/ffmpeg")

RTSP_BASE = f"rtsp://{PUBLISH_USER}:{PUBLISH_PASS}@{RTSP_HOST}:{RTSP_PORT}"

# stream_path → on-disk JPEG path
STREAMS: dict[str, str] = {
    lens["stream_path"]: os.path.join(SNAP_DIR, f"{lens['stream_path']}.jpg")
    for cam in CAMS for lens in cam["lenses"]
}

os.makedirs(SNAP_DIR, exist_ok=True)
_procs: dict[str, subprocess.Popen] = {}


def start_ffmpeg(stream: str, out_path: str):
    cmd = [
        FFMPEG_BIN, "-loglevel", "error",
        "-fflags", "nobuffer+discardcorrupt",
        "-rtsp_transport", "tcp",
        "-skip_frame", "nokey",
        "-i", f"{RTSP_BASE}/{stream}",
        "-an",
        "-vf", "fps=1,scale=1280:720",
        "-q:v", "4",
        "-update", "1", "-y",
        "-atomic_writing", "1",
        out_path,
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def supervisor():
    while True:
        for stream, path in STREAMS.items():
            proc = _procs.get(stream)
            if proc is None or proc.poll() is not None:
                _procs[stream] = start_ffmpeg(stream, path)
        time.sleep(5)


def is_valid_jpeg(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(2)
            f.seek(-2, 2)
            tail = f.read(2)
        return head == b"\xff\xd8" and tail == b"\xff\xd9"
    except Exception:
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.lstrip("/").split("?", 1)[0]
        out_path = STREAMS.get(path)
        if not out_path:
            self.send_response(404); self.end_headers(); return
        if not os.path.exists(out_path) or not is_valid_jpeg(out_path):
            self.send_response(503); self.end_headers(); return
        try:
            with open(out_path, "rb") as f:
                jpg = f.read()
        except Exception:
            self.send_response(503); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpg)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(jpg)

    def log_message(self, *_a, **_k):
        pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def shutdown(*_):
    for p in _procs.values():
        try: p.terminate()
        except Exception: pass
    os._exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=supervisor, daemon=True).start()
    for _ in range(30):
        if any(is_valid_jpeg(p) for p in STREAMS.values()): break
        time.sleep(1)
    print(f"snapshot server ready on :{PORT}; streams: {sorted(STREAMS)}", flush=True)
    ThreadedHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
