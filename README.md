# tapo-onvif

**Make TP-Link Tapo cameras adoptable by UniFi Protect.**

UniFi Protect's *Advanced Adoption* will accept any RTSP/ONVIF
Profile-S source — but Tapo cameras don't speak either. They use an
HTTPS-encrypted "Streamd" protocol that only the official Tapo apps
and a handful of open-source clients ([pytapo]) can decode. This
bridge is the translation layer: it pulls the Streamd feed off each
Tapo camera, transcodes when necessary, and re-publishes every lens
as **RTSP** + a **virtual ONVIF Profile-S camera** that UniFi will
adopt as if it were a native cam.

Scrypted, Homebridge, VLC, and any other generic RTSP/ONVIF NVR work
too — UniFi is just the design target because it's the strictest
adopter of the bunch (specific encoder profile, ONVIF op coverage,
loopback-only publish creds). If it works in UniFi, it works
everywhere.

Multiple cameras are supported: list them in [config/cameras.yml](config/cameras.yml.example),
and the launcher spawns one bridge process per camera. Each lens
becomes its own RTSP stream, snapshot endpoint, and ONVIF endpoint
(e.g. wide + tele for a dual-lens model, main for a single-lens one).

**Supported models today**
- **C675D** — dual-lens battery (wide + tele). End-to-end pipeline
  implemented and battle-tested.
- *Other Tapo models* — config schema, ONVIF, and snapshot servers
  treat them generically; only the camera→ffmpeg pipeline in
  [src/tapo_to_rtsp.py](src/tapo_to_rtsp.py) is C675D-specific. Adding
  another model is a localized change (see *Adding a model* below).

> **Account caveat for battery cameras.** Battery models like the C675D
> are bound to a single TP-Link cloud account and don't support
> creating a separate Tapo sub-account or "shared user" you could
> hand to the bridge — the device only authenticates the primary
> owner. So `CAM_USER` / `CAM_PASS` in `.env` are your **real**
> TP-Link cloud credentials. Treat the `.env` file accordingly: it's
> already gitignored, but consider locking it down (`chmod 600`) and
> using a dedicated TP-Link account that owns *only* the cameras you
> want to bridge. Always-on cameras (C200, C320WS, C400, …) often
> support shared/family accounts in the Tapo app and are safer in
> that respect.

```mermaid
flowchart LR
    subgraph LAN["LAN"]
        cam["Tapo camera<br/>:8800 (Streamd, AES)"]
        client["NVR / app<br/>(UniFi Protect,<br/>Scrypted, VLC, …)"]
    end

    subgraph host["Bridge host (Mac / Linux)"]
        bridge["tapo_to_rtsp.py<br/>(one per camera)"]
        ffmpeg["ffmpeg<br/>transcode + silent AAC"]
        mediamtx["mediamtx<br/>RTSP :8555<br/>publish loopback-only"]
        snap_ff["ffmpeg snapshot loops<br/>(1 fps, atomic JPEG)"]
        snap_srv["snapshot_server.py<br/>HTTP :8683"]
        onvif["onvif_server.py<br/>(SOAP, one HTTP server<br/>per lens, port from<br/>cameras.yml)"]
    end

    cam -- "pytapo HttpMediaSession<br/>(MPEG-TS / HEVC)" --> bridge
    bridge -- "stdin pipe" --> ffmpeg
    ffmpeg -- "RTSP push<br/>publish/publish@127.0.0.1" --> mediamtx
    mediamtx -- "RTSP read<br/>READ_USER/READ_PASS" --> client
    mediamtx -- "RTSP read<br/>(loopback)" --> snap_ff
    snap_ff -- "/tmp/tapo_snaps/<name>_<kind>.jpg" --> snap_srv
    snap_srv -- "HTTP JPEG" --> client
    onvif -- "GetStreamUri →<br/>rtsp://…/<name>_<kind><br/>GetSnapshotUri →<br/>http://…:8683/<name>_<kind>" --> client
```

## Quick start (macOS)

```sh
git clone https://github.com/mp-consulting/tapo-onvif.git
cd tapo-onvif
./install.sh
$EDITOR .env                 # CAM_USER, CAM_PASS, READ_USER, READ_PASS, PUBLIC_HOST
$EDITOR config/cameras.yml   # one entry per Tapo camera (IP, model, ONVIF ports)
src/run_bridge.sh            # foreground test
```

Once everything is up, each lens of each camera gets its own RTSP
stream, snapshot URL, and ONVIF endpoint. Stream paths are derived as
`<name>_<kind>` — e.g. for a camera named `garden`:

- C675D (dual-lens): `garden_wide`, `garden_tele`
- Single-lens models (C200, C320WS, …): `garden_main`

Pick the right `<kind>` for your model below:

- VLC: `rtsp://<READ_USER>:<READ_PASS>@<this-host>:8555/<name>_<kind>`
- UniFi Protect *Advanced Adoption*: IP `<this-host>:<onvif_port>` (the
  port you set under `onvif_ports.<kind>` in `cameras.yml`), user/pass
  `<READ_USER>` / `<READ_PASS>`
- Scrypted: add the same RTSP URL via the RTSP plugin
- Snapshot: `http://<this-host>:8683/<name>_<kind>`

For auto-start on macOS, see [config/com.tapo.onvif.plist.example](config/com.tapo.onvif.plist.example) and run
`./install.sh launchd`.

## Layout

```
tapo-onvif/
├── README.md
├── CHANGELOG.md
├── CLAUDE.md                 # orientation for AI/agent contributors
├── LICENSE
├── install.sh                # brew install + venv + .env / cameras.yml scaffold
├── .env.example              # shared config template (copied to .env by install.sh)
├── config/
│   ├── cameras.yml.example          # per-camera config template
│   ├── mediamtx.yml.template        # rendered to tmp/mediamtx.yml at startup
│   └── com.tapo.onvif.plist.example   # launchd template
├── src/
│   ├── _env.py               # shared .env loader
│   ├── _cameras.py           # cameras.yml loader + validator
│   ├── tapo_to_rtsp.py       # cam → ffmpeg → RTSP publish (with watchdog)
│   ├── snapshot_server.py    # HTTP :8683 → JPEG snapshots (one ffmpeg per lens)
│   ├── onvif_server.py       # virtual ONVIF Profile-S camera(s)
│   └── run_bridge.sh         # spawns the above + mediamtx
├── tmp/                      # runtime artefacts (rendered config, logs); gitignored
└── docs/                     # historical reverse-engineering notes
    ├── BCCP_protocol.md
    ├── KEYCHAIN_SEARCH.md
    └── PACKETLOGGER_CAPTURE.md
```

## Configuration

There are **two** config files:

### `.env` — shared credentials and network settings

| Var | Default | Notes |
|---|---|---|
| `CAM_USER` | — | TP-Link cloud account email (the one that owns the cameras) |
| `CAM_PASS` | — | TP-Link cloud password |
| `RTSP_HOST` | `127.0.0.1` | where mediamtx binds — **keep on loopback** (the publish user is allowlisted to 127.0.0.1) |
| `RTSP_PORT` | `8555` | |
| `PUBLISH_USER` | `publish` | internal RTSP publish creds (loopback-only by IP allowlist) |
| `PUBLISH_PASS` | `publish` | |
| `READ_USER` | — | external RTSP/ONVIF read creds advertised to UniFi / Scrypted / VLC |
| `READ_PASS` | — | pick a strong one — this **is** exposed to the LAN |
| `PUBLIC_HOST` | `127.0.0.1` | IP advertised to ONVIF clients (must be reachable by them — usually your LAN IP) |
| `SNAPSHOT_PORT` | `8683` | |
| `PYTHON_BIN` | `/tmp/tapo_venv/bin/python` | python with `pytapo` and `pyyaml` installed |
| `FFMPEG_BIN` | `/opt/homebrew/bin/ffmpeg` | |
| `MEDIAMTX_BIN` | `/opt/homebrew/bin/mediamtx` | |

ONVIF ports are **per-camera** and live in `cameras.yml` (see below) — not here.

### `config/cameras.yml` — one entry per camera

```yaml
cameras:
  - name: garden            # alphanumeric + underscore; used as stream-path prefix
    model: c675d            # c675d (dual-lens, fully implemented) or c200 (placeholder)
    ip: 192.168.1.101
    onvif_ports:
      wide: 8081            # one port per lens; must be unique across all cameras
      tele: 8082
```

For each camera, every lens kind in `MODELS[<model>]` (see [src/_cameras.py](src/_cameras.py))
must appear in `onvif_ports`, and every port must be unique across the
whole file. The launcher rejects malformed configs at startup.

Stream paths and snapshot routes are derived: `<name>_<kind>`, e.g.
`garden_wide`, `garden_tele`. The snapshot server exposes `/<name>_<kind>`.

## Encoder settings (C675D pipeline)

The C675D source is HEVC; UniFi Protect won't ingest HEVC over its
third-party-cam path, so we transcode to H.264. Inside
[src/tapo_to_rtsp.py](src/tapo_to_rtsp.py), the working ffmpeg config is:

```
-c:v h264_videotoolbox  -profile:v main  -b:v 3M -maxrate 4M -bufsize 6M
-pix_fmt yuv420p  -g 30  +  silent AAC 48 kHz stereo
```

These exact settings are what UniFi Protect's *Advanced Adoption*
accepts. Every "improvement" we tried (downscale to 1080p, libx264
baseline, dump_extra bsf, real cam-mic audio) broke playback in some
client. **If you change them and adoption stops working, revert.**

The cam emits **real mu-law 16 kHz audio** via pytapo's side-channel
pipe (the Tapo app plays it just fine), but wiring it into ffmpeg
breaks the muxer — ffmpeg's HEVC decoder errors on POC reference
frames during stream startup, and the two-input muxer waits
indefinitely for both inputs to produce sync timestamps. Synthetic
silent AAC keeps HomeKit/fmp4 muxers happy without that stall.

Other Tapo models will need their own pipeline branch — some are
already H.264 at the source (no transcode needed), some are 2K, etc.
See *Adding a model* below.

## Adding a model

The framework is generic; only the camera→ffmpeg pipeline is
model-specific. To add support for, say, a C200:

1. **Lens layout** — add an entry to `MODELS` in [src/_cameras.py](src/_cameras.py):
   ```python
   MODELS = {
       "c675d": ["wide", "tele"],
       "c200":  ["main"],          # already present as a placeholder
   }
   ```
   The keys here become the required keys under `onvif_ports` in
   `cameras.yml`.
2. **Pipeline** — branch on `CAM["model"]` in
   [src/tapo_to_rtsp.py](src/tapo_to_rtsp.py) and build an ffmpeg
   command for that model. If the source is already H.264, you can
   often drop transcoding entirely (`-c:v copy`) and just remux.
3. **Test against UniFi** — adoption is the canary. If UniFi Protect
   won't adopt or the live tile stays black, the encoder profile is
   wrong; widen the search from there to Scrypted / VLC.
4. **Update `cameras.yml.example`** with a commented sample entry for
   the new model.

ONVIF and snapshot serving are model-agnostic — they iterate every
(camera, lens) pair from `cameras.yml`, so they pick up new models
for free.

## Watchdog & self-healing

Each `tapo_to_rtsp.py` process polls every 5 s for three failure modes:

1. pytapo's stream task ended (cam closed the LAN session)
2. ffmpeg process exited
3. RTSP `DESCRIBE` to `127.0.0.1:8555/<first-lens>` returns non-200 (cam
   silently stopped feeding bytes; both processes look "alive" but
   nothing is publishing)

When any trips → `sys.exit(1)`. [src/run_bridge.sh](src/run_bridge.sh) waits on every bridge
process and exits the whole stack as soon as **any** of them dies, so
launchd (or systemd / docker / supervisor) restarts everything within
~30 s. One unhealthy camera takes down and restarts the lot — simpler
than per-camera supervision and matches the failure mode of the cloud
account itself going stale.

## Adoption recipes

### UniFi Protect (Advanced Adoption) — primary target
*Devices → Add → Advanced Adoption*
- IP Address: `<host>:<onvif_port>` — use the ports you set under
  `onvif_ports` in `cameras.yml` (e.g. `8081` for `wide`, `8082` for
  `tele` on a C675D — but the port numbers are entirely up to you).
- Username / Password: your `READ_USER` / `READ_PASS` from `.env`.

Each lens is adopted as a **separate** UniFi camera; that's how UniFi
addresses single-stream sources, and it's why this bridge runs one
ONVIF endpoint per (camera, lens) pair instead of trying to expose
multi-stream profiles.

### Scrypted (RTSP plugin)
- Add Camera → RTSP → Stream URL: `rtsp://<READ_USER>:<READ_PASS>@<host>:8555/<name>_<kind>`
- Snapshot URL: `http://<host>:8683/<name>_<kind>`
- Then enable the HomeKit / UniFi Protect plugin to re-export elsewhere.

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
- **Non-C675D models**: `cameras.yml` accepts `model: c200`, the
  config layer / ONVIF / snapshot servers handle it, but
  [src/tapo_to_rtsp.py](src/tapo_to_rtsp.py) only implements the
  C675D pipeline today and will exit with an error for other models.
  See *Adding a model* above.
- **TP-Link cloud credentials**: battery models (C675D et al.) bind
  to a single primary cloud account — there's no Tapo-app sub-account
  / shared user you can dedicate to the bridge, so `CAM_USER`/`CAM_PASS`
  are the real account creds. See the account caveat at the top of
  this README.

## Reverse-engineering history

Building this took several days of false starts before landing on
[pytapo]. The dead ends are documented in [docs/](docs/):

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
  wholesale by [src/onvif_server.py](src/onvif_server.py) (~300 lines of hand-written SOAP).

The breakthrough: **pytapo's `HttpMediaSession` already implements the
entire Streamd protocol**, including HTTP-Digest auth (SHA-256 + cnonce
+ encrypt_type=3) and AES-CBC multipart-mixed body decryption. We
sub-class its `Streamer` and replace the ffmpeg invocation with our own
that publishes RTSP instead of producing HLS.

## Linux / non-Mac

The Python files are portable. To run on Linux:
- Replace `h264_videotoolbox` (Apple Silicon HW encoder) with
  `libx264` in [src/tapo_to_rtsp.py](src/tapo_to_rtsp.py).
- Use `systemd` instead of `launchd` (a unit file is left as
  exercise; the script's exit-on-bridge-death model maps directly to
  `Restart=on-failure`).

## Contributing

PRs welcome. Code is intentionally simple Python with no exotic deps
beyond [pytapo], [PyYAML], and [mediamtx]. Real-mic-audio support, a
proper WS-Discovery responder, and end-to-end support for non-C675D
models are the obvious next features.

This project follows:
- [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.
- [Semantic Versioning](https://semver.org/) for releases.
- [Keep a Changelog](https://keepachangelog.com/) for [CHANGELOG.md](CHANGELOG.md).

Project context for AI/agent contributors lives in [CLAUDE.md](CLAUDE.md).

## License

MIT — see [LICENSE](LICENSE).

## Credits

- [pytapo] — the Streamd protocol implementation we build on
- [mediamtx] — RTSP server we publish into
- [daniela-hase/onvif-server] — the original (now-replaced) Node.js
  ONVIF wrapper that inspired our Python rewrite

[pytapo]: https://pypi.org/project/pytapo/
[mediamtx]: https://github.com/bluenviron/mediamtx
[PyYAML]: https://pypi.org/project/PyYAML/
[daniela-hase/onvif-server]: https://github.com/daniela-hase/onvif-server
