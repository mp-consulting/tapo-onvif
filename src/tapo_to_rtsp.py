#!/usr/bin/env python3
"""Tapo → RTSP bridge (one camera per process).

Pulls HEVC video from a Tapo camera over its proprietary Streamd protocol
(port 8800, HTTP-Digest + AES multipart MPEG-TS), transcodes to H.264 via
Apple Silicon's videotoolbox, publishes the camera's lens(es) as RTSP to
a local mediamtx server.

Today only the C675D model is implemented end-to-end (dual-lens: wide
+ tele). Other models recognized in cameras.yml will need their own
ffmpeg pipeline here.

Usage:
    tapo_to_rtsp.py --camera <name>

Config:
    config/cameras.yml   per-camera IP, model, ONVIF ports
    .env                 CAM_USER, CAM_PASS (shared TP-Link cloud account),
                         PUBLISH_USER/PUBLISH_PASS, RTSP_HOST/RTSP_PORT
"""
import argparse
import asyncio
import logging
import os
import sys
import signal
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _env import load_dotenv
from _cameras import load_cameras, find_camera


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--camera", required=True,
                   help="camera name (must match an entry in cameras.yml)")
    return p.parse_args()


ARGS = parse_args()
CAM = find_camera(load_cameras(HERE), ARGS.camera)

logging.basicConfig(level=logging.INFO,
                    format=f"%(asctime)s %(levelname)s [{CAM['name']}] %(message)s")
logging.getLogger("pytapo.media_stream.session").setLevel(logging.ERROR)
log = logging.getLogger("tapo-rtsp")

ENV = load_dotenv(HERE)
CAM_USER      = ENV.get("CAM_USER",      "")
CAM_PASS      = ENV.get("CAM_PASS",      "")
PUBLISH_USER  = ENV.get("PUBLISH_USER",  "publish")
PUBLISH_PASS  = ENV.get("PUBLISH_PASS",  "publish")
RTSP_HOST     = ENV.get("RTSP_HOST",     "127.0.0.1")
RTSP_PORT     = int(ENV.get("RTSP_PORT", "8555"))

if not CAM_PASS:
    sys.exit("ERROR: .env missing CAM_PASS (your TP-Link cloud password)")
if not CAM_USER:
    sys.exit("ERROR: .env missing CAM_USER (your TP-Link cloud email)")

if CAM["model"] != "c675d":
    sys.exit(f"ERROR: bridge only implements model 'c675d' today; "
             f"camera {CAM['name']!r} is model {CAM['model']!r}.")

RTSP_BASE = f"rtsp://{PUBLISH_USER}:{PUBLISH_PASS}@{RTSP_HOST}:{RTSP_PORT}"
RTSP_BY_KIND = {l["kind"]: f"{RTSP_BASE}/{l['stream_path']}" for l in CAM["lenses"]}
WATCHDOG_PATH = CAM["lenses"][0]["stream_path"]


def make_tapo():
    """Create the Tapo client synchronously — pytapo runs its own event
    loop for the auth handshake; mixing with asyncio.run() raises."""
    from pytapo import Tapo
    log.info(f"connecting to {CAM['ip']}…")
    tapo = Tapo(CAM["ip"], CAM_USER, CAM_PASS, cloudPassword=CAM_PASS)
    name = tapo.getBasicInfo()["device_info"]["basic_info"]["device_alias"]
    log.info(f"cam alias on device: {name}")
    return tapo


async def amain(tapo):
    from pytapo.media_stream.streamer import Streamer

    rtsp_wide = RTSP_BY_KIND["wide"]
    rtsp_tele = RTSP_BY_KIND["tele"]

    class RTSPStreamer(Streamer):
        """ffmpeg with two RTSP outputs (one per lens), silent AAC track,
        videotoolbox HW encoder. The cam emits real mu-law audio via
        pytapo's side-channel pipe but wiring it through ffmpeg breaks
        the muxer — silent AAC keeps HomeKit/fmp4 muxers happy."""
        async def start(self):
            self.currentAction = "FFMpeg Starting"
            video_opts = [
                "-c:v", "h264_videotoolbox",
                "-b:v", "3M", "-maxrate", "4M", "-bufsize", "6M",
                "-profile:v", "main",
                "-pix_fmt", "yuv420p",
                "-g", "30",                              # I-frame every 2 s
            ]
            audio_opts = [
                "-c:a", "aac", "-b:a", "64k",
                "-ar", "48000", "-ac", "2",
            ]
            rtsp_opts = ["-f", "rtsp", "-rtsp_transport", "tcp"]
            cmd = [
                "ffmpeg", "-loglevel", "warning",
                "-fflags", "+genpts+nobuffer",
                "-probesize", "5M", "-analyzeduration", "5M",
                "-f", "mpegts", "-i", "pipe:0",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0", "-map", "1:a:0",
                *video_opts, *audio_opts, *rtsp_opts, rtsp_wide,
                "-map", "0:v:1", "-map", "1:a:0",
                *video_opts, *audio_opts, *rtsp_opts, rtsp_tele,
            ]
            log.info(f"ffmpeg → {' '.join(cmd)}")
            self.streamProcess = await asyncio.create_subprocess_exec(
                *cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            asyncio.create_task(self._print_ffmpeg_logs(self.streamProcess.stderr))
            self.running = True
            if self.stream_task is None or self.stream_task.done():
                self.stream_task = asyncio.create_task(self._stream_to_ffmpeg())

    def ff_log(d):
        log.info(f"[ffmpeg] {d.get('ffmpegLog','')}")

    streamer = RTSPStreamer(
        tapo=tapo, quality="HD", includeAudio=False, mode="pipe",
        logLevel="info", ff_args={}, logFunction=ff_log,
    )
    await streamer.start()
    log.info(f"wide → {rtsp_wide}")
    log.info(f"tele → {rtsp_tele}")

    stop = asyncio.Event()
    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, stop.set)
    asyncio.get_event_loop().add_signal_handler(signal.SIGTERM, stop.set)

    # --- watchdog: 3 failure modes, all → exit(1) → launchd restart ----
    async def is_publishing() -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(RTSP_HOST, RTSP_PORT), timeout=2)
            import base64
            auth = base64.b64encode(f"{PUBLISH_USER}:{PUBLISH_PASS}".encode()).decode()
            writer.write(
                f"DESCRIBE {RTSP_BASE}/{WATCHDOG_PATH} RTSP/1.0\r\n"
                f"CSeq: 1\r\nAuthorization: Basic {auth}\r\n\r\n".encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=2)
            writer.close()
            try: await writer.wait_closed()
            except Exception: pass
            return b"200" in line
        except Exception:
            return False

    async def watchdog():
        start_time = time.monotonic()
        last_publish = None
        STARTUP_GRACE = 60
        STALL_LIMIT   = 30
        while not stop.is_set():
            await asyncio.sleep(5)
            if streamer.stream_task and streamer.stream_task.done():
                log.error("cam stream task ended — restart"); stop.set(); return
            if streamer.streamProcess and streamer.streamProcess.returncode is not None:
                log.error(f"ffmpeg exited rc={streamer.streamProcess.returncode}"); stop.set(); return
            if await is_publishing():
                last_publish = time.monotonic()
                continue
            now = time.monotonic()
            if last_publish is None:
                if now - start_time > STARTUP_GRACE:
                    log.error(f"never came up in {STARTUP_GRACE}s — restart"); stop.set(); return
            else:
                if now - last_publish > STALL_LIMIT:
                    log.error(f"stalled {now-last_publish:.0f}s — restart"); stop.set(); return
    asyncio.create_task(watchdog())

    await stop.wait()
    log.warning("watchdog tripped — shutting down for launchd to restart us")
    try:
        await asyncio.wait_for(streamer.stop(), timeout=3)
    except Exception:
        pass
    sys.exit(1)


if __name__ == "__main__":
    tapo = make_tapo()
    asyncio.run(amain(tapo))
