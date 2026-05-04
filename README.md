# tapo-bridge

A Mac-hosted bridge that pulls live HEVC video from a **TP-Link Tapo C675D
dual-lens battery camera** over its proprietary Streamd protocol,
transcodes to H.264, and re-publishes both lenses as **RTSP** and as
**virtual ONVIF cameras** so they can be adopted by UniFi Protect,
Scrypted, Homebridge, generic NVRs, etc.

The C675D has no native RTSP/ONVIF — it speaks an HTTPS-encrypted Streamd
protocol that only the official Tapo apps and a few open-source clients
([pytapo]) can decode. This bridge is the missing translation layer.

```
                       ┌──── ffmpeg snapshot 1 ─► /tmp/tapo_snaps/wide.jpg ─┐
                       │     (1 fps, atomic write, scale 1280×720)         │
                       │                                                    │
   cam :8800 (Streamd) │     ┌───────────┐    ┌──────────┐                 │
        │              │     │ mediamtx  │    │ Python    │                │
        │  pytapo      │     │  :8555    │    │ ONVIF     │  HTTP JPEG     │
        │  HttpMedia-  ├────►│  (RTSP)   │    │ server    │ ◄──────────────┤
        │  Session     │     │           │    │  :8081/82 │                │
        │              │     └─────▲─────┘    └─────┬─────┘                │
        ▼              │           │                │                      │
  tapo_to_rtsp.py      │   ffmpeg  │ RTSP push      │ GetStreamUri →       │
  ───────────────►─────┘   (transcode HEVC→H.264    │ rtsp://…/c675d_wide  │
   pulls MPEG-TS              + scale + add silent  │                      │
   from cam, feeds              AAC if no audio)    │ GetSnapshotUri →     │
   ffmpeg via stdin                                 │ http://…:8683/wide   │
                       ┌── ffmpeg snapshot 2 ─► /tmp/tapo_snaps/tele.jpg ──┘
                       │
                       └──► snapshot_server.py (HTTP :8683 → serves JPEGs)
```

## Quick start (macOS)

```sh
git clone https://github.com/<you>/tapo-bridge.git
cd tapo-bridge
./install.sh
$EDITOR src/.env       # add CAM_IP, CAM_USER, CAM_PASS
src/run_bridge.sh      # foreground test
```

When everything's up:
- VLC: `rtsp://publish:publish@<this-host>:8555/c675d_wide`
- UniFi Protect Advanced Adoption: IP `<this-host>:8081` / user `publish` / pass `publish`
- Scrypted: add the same RTSP URL via the RTSP plugin

For auto-start on macOS, see `config/com.tapo.bridge.plist.example`.

## Layout

```
tapo-bridge/
├── README.md
├── LICENSE
├── install.sh                 # brew install + venv + .env scaffold
├── .env.example               # config template (copy to src/.env)
├── config/
│   ├── mediamtx.yml
│   └── com.tapo.bridge.plist.example     # launchd template
├── src/
│   ├── tapo_to_rtsp.py        # cam → ffmpeg → RTSP publish (with watchdog)
│   ├── snapshot_server.py     # HTTP :8683 → JPEG snapshots
│   ├── onvif_server.py        # virtual ONVIF Profile-S camera(s)
│   └── run_bridge.sh          # spawns the above + mediamtx
└── docs/                      # historical reverse-engineering notes
    ├── BCCP_protocol.md
    ├── KEYCHAIN_SEARCH.md
    └── PACKETLOGGER_CAPTURE.md
```

## Configuration (`.env`)

| Var | Default | Notes |
|---|---|---|
| `CAM_IP` | — | LAN IP of the camera |
| `CAM_USER` | — | TP-Link cloud account email (the one that owns the cam) |
| `CAM_PASS` | — | TP-Link cloud password |
| `RTSP_HOST` | `127.0.0.1` | where mediamtx binds |
| `RTSP_PORT` | `8555` | |
| `PUBLIC_HOST` | `127.0.0.1` | the IP advertised to ONVIF clients (must be reachable by them — usually your LAN IP) |
| `PUBLISH_USER` | `publish` | RTSP read/publish credentials |
| `PUBLISH_PASS` | `publish` | |
| `SNAPSHOT_PORT` | `8683` | |
| `ONVIF_WIDE_PORT` | `8081` | |
| `ONVIF_TELE_PORT` | `8082` | |
| `PYTHON_BIN` | `/tmp/tapo_venv/bin/python` | python with `pytapo` installed |
| `FFMPEG_BIN` | `/opt/homebrew/bin/ffmpeg` | |
| `MEDIAMTX_BIN` | `/opt/homebrew/bin/mediamtx` | |

## Encoder settings

Inside `src/tapo_to_rtsp.py`, the working ffmpeg config is:

```
-c:v h264_videotoolbox  -profile:v main  -b:v 3M -maxrate 4M -bufsize 6M
-pix_fmt yuv420p  -g 30  +  silent AAC 48 kHz stereo
```

These exact settings are what UniFi Protect's third-party adoption
accepts. Every "improvement" we tried (downscale to 1080p, libx264
baseline, dump_extra bsf, real cam-mic audio) broke playback in some
client. **If you change them and adoption stops working, revert.**

The cam emits **real mu-law 16 kHz audio** via pytapo's side-channel
pipe (the Tapo app plays it just fine), but wiring it into ffmpeg
breaks the muxer — ffmpeg's HEVC decoder errors on POC reference
frames during stream startup, and the two-input muxer waits indefinitely
for both inputs to produce sync timestamps. Synthetic silent AAC keeps
HomeKit/fmp4 muxers happy without that stall.

## Watchdog & self-healing

`tapo_to_rtsp.py` polls every 5 s for three failure modes:

1. pytapo's stream task ended (cam closed the LAN session)
2. ffmpeg process exited
3. RTSP `DESCRIBE` to `127.0.0.1:8555/c675d_wide` returns 404 (cam
   silently stopped feeding bytes; both processes look "alive" but
   nothing is publishing)

When any trips → `sys.exit(1)` → `run_bridge.sh` `wait` returns →
trap kills mediamtx/snapshot_server/onvif_server → script exits 1 →
**launchd** (or whatever supervisor you use) restarts the entire stack
within ~30 s.

## Adoption recipes

### UniFi Protect (Advanced Adoption)
*Devices → Add → Advanced Adoption*
- IP Address: `<host>:8081` (wide) or `<host>:8082` (tele)
- Username / Password: `publish` / `publish` (whatever you set)

### Scrypted (RTSP plugin)
- Add Camera → RTSP → Stream URL: `rtsp://publish:publish@<host>:8555/c675d_wide`
- Snapshot URL: `http://<host>:8683/wide`
- Then enable HomeKit / UniFi Protect plugin to re-export elsewhere.

### Homebridge
- `homebridge-camera-ffmpeg` or `homebridge-unifi-protect` (the latter
  if you've adopted into UniFi). Either works; UniFi Protect's
  third-party RTSP cams aren't exposed to HomeKit Secure Video by
  Ubiquiti, so for HKSV you **must** route through Scrypted or
  Homebridge directly.

## Known limitations

- **Web UI in UniFi Protect (cloud)**: third-party RTSP cams don't
  stream via UniFi's cloud-relayed WebSocket (`*.id.ui.direct`). Use
  the **mobile app** or open Protect via the local console IP.
- **Native HomeKit via UniFi**: Ubiquiti only exposes their OWN cameras
  to HomeKit Secure Video. Route through Scrypted/Homebridge for HKSV.
- **Cam mic audio**: silent AAC is a workaround; real mic audio needs
  a custom RTP muxer that bypasses ffmpeg.
- **ONVIF WS-Discovery (UDP 3702)**: not implemented. ONVIF clients
  that rely purely on multicast probe won't auto-discover — give them
  the IP+port manually via Advanced Adoption.

## Reverse-engineering history

Building this took several days of false starts before landing on
[pytapo]. The dead ends are documented in `docs/`:

- **WebRTC/cipc cloud SFU**: the `/v1/sfu/request` cloud endpoint
  exists but battery cams don't publish to the cloud SFU — only
  always-on cams (C320WS, C400, …) do. Returns `errorCode 2001`.
- **BLE pairing**: SRP-6a handshake works to round 1, but the
  `pwd_verifier` needs a per-cam secret not derivable from cloud
  credentials.
- **Modded Android Tapo APK** (Frida): installable, login works, but
  live-view crashes; would have needed Frida hooks into MetaRtc's
  native lib. Abandoned when pytapo turned out to handle Streamd
  natively.
- **daniela-hase ONVIF server** (Node.js + soap@1.1.5): bundled SOAP
  library throws `TypeError: Cannot read properties of undefined
  (reading 'description')` on any ONVIF op not in its WSDL stub.
  Patched twice; each fix moved the crash one frame deeper. Replaced
  wholesale by `src/onvif_server.py` (~300 lines of hand-written SOAP).

The breakthrough: **pytapo's `HttpMediaSession` already implements the
entire Streamd protocol**, including HTTP-Digest auth (SHA-256 + cnonce
+ encrypt_type=3) and AES-CBC multipart-mixed body decryption. We
sub-class its `Streamer` and replace the ffmpeg invocation with our own
that publishes RTSP instead of producing HLS.

## Linux / non-Mac

The Python files are portable. To run on Linux:
- Replace `h264_videotoolbox` (Apple Silicon HW encoder) with
  `libx264` in `src/tapo_to_rtsp.py`.
- Use `systemd` instead of `launchd` (a unit file is left as
  exercise; the script's exit-on-bridge-death model maps directly to
  `Restart=on-failure`).

## Contributing

PRs welcome. The code is intentionally simple Python with no exotic
deps beyond [pytapo] and [mediamtx]. Real-mic-audio support and a
proper WS-Discovery responder are the obvious next features.

## License

MIT — see [LICENSE](LICENSE).

## Credits

- [pytapo] — the Streamd protocol implementation we build on
- [mediamtx] — RTSP server we publish into
- [daniela-hase/onvif-server] — the original (now-replaced) Node.js
  ONVIF wrapper that inspired our Python rewrite

[pytapo]: https://pypi.org/project/pytapo/
[mediamtx]: https://github.com/bluenviron/mediamtx
[daniela-hase/onvif-server]: https://github.com/daniela-hase/onvif-server
