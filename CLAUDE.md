# CLAUDE.md

Orientation for AI/agent contributors (Claude Code, etc.) working on
this repo. Human contributors: [README.md](README.md) is the right
starting point.

## What this is

A small, self-contained bridge that exposes TP-Link Tapo C675D battery
cameras as standard RTSP + ONVIF Profile-S endpoints. The hard part —
decoding TP-Link's proprietary Streamd protocol — is delegated to
[pytapo]; the rest is plumbing (ffmpeg transcode, mediamtx publish,
hand-written ONVIF SOAP, JPEG snapshot HTTP server, watchdog).

The codebase is intentionally small (~1k LoC of Python + a launcher
shell script) and has no exotic dependencies. **Don't add frameworks,
abstractions, or layers without a strong reason.**

## Process model

```
launchd (or systemd / docker)
  └── src/run_bridge.sh
        ├── mediamtx                      (RTSP server, port 8555)
        ├── tapo_to_rtsp.py --camera A    (one per camera in cameras.yml)
        ├── tapo_to_rtsp.py --camera B
        ├── …
        ├── snapshot_server.py            (HTTP :8683, one ffmpeg per lens)
        └── onvif_server.py               (ONVIF SOAP, one HTTP server per lens)
```

`run_bridge.sh` exits as soon as **any** bridge process dies, so the
supervisor restarts the whole stack. This is deliberate — recovery
correctness beats per-camera isolation here, and one cam going stale
usually means the whole TP-Link cloud session needs re-auth.

## Config split

Two files, two purposes:

- **`.env`** — shared credentials (TP-Link cloud user/pass, RTSP read
  user/pass, public host) and binary paths. Loaded by every Python
  entrypoint via `src/_env.py`.
- **`config/cameras.yml`** — per-camera list (name, model, IP, ONVIF
  ports per lens). Loaded and validated by `src/_cameras.py`.

`cameras.yml` is the source of truth for cameras — it drives stream
paths (`<name>_<kind>`), snapshot routes, ONVIF ports, and bridge
process spawning. Adding a new camera type means:
1. Add a `model` entry to `MODELS` in [src/_cameras.py](src/_cameras.py)
   listing its lens kinds.
2. Implement an ffmpeg pipeline for that model in
   [src/tapo_to_rtsp.py](src/tapo_to_rtsp.py) (currently only `c675d`
   is implemented end-to-end).
3. Add a usage example to [config/cameras.yml.example](config/cameras.yml.example).

## Don't break these

These are well-trodden landmines from the reverse-engineering history.
Don't "improve" them without first reading the README's *Encoder
settings* section.

- **Encoder flags in `tapo_to_rtsp.py`** are exactly what UniFi
  Protect's third-party adoption accepts. Downscaling, `libx264`
  baseline, `dump_extra` bsf, real cam-mic audio — all tried, all
  broke at least one client. If adoption stops working after a
  change, revert.
- **Silent AAC track**: the cam *does* emit real mu-law audio via
  pytapo's side-channel pipe, but feeding it into the same ffmpeg
  process stalls the muxer waiting for sync timestamps. Don't add it
  back without a separate-process / RTP-mux design.
- **mediamtx `publish` user is loopback-only** by IP allowlist (in
  `config/mediamtx.yml.template`). `RTSP_HOST` must stay on
  `127.0.0.1` for the bridge to publish; pointing it at the LAN IP
  will be rejected by mediamtx. The LAN-facing creds are
  `READ_USER` / `READ_PASS`.
- **Watchdog grace periods** in `tapo_to_rtsp.py` (60 s startup, 30 s
  stall) are tuned for TP-Link cloud auth latency. Tighter values
  cause restart loops on cold start.

## Conventions

- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, …).
- **Versioning**: [Semantic Versioning](https://semver.org/). The repo is
  pre-1.0; minor bumps may include breaking config-schema changes
  until 1.0.
- **Changelog**: [Keep a Changelog](https://keepachangelog.com/) — new
  entries go under `[Unreleased]` until released; on release, rename
  the section to the version + date and start a fresh `[Unreleased]`.
- **Commit attribution**: do not add a `Co-Authored-By: Claude …`
  trailer or any "Generated with Claude Code" line. Plain commits only.
- **Code style**: stdlib-first Python; no linters/formatters wired up
  yet. Keep modules short; prefer one file per concern (`_env.py`,
  `_cameras.py`, etc.).
- **Comments**: prefer comments that explain *why* a non-obvious
  choice was made (e.g. the encoder-flag warnings, the loopback
  allowlist, the watchdog grace constants). Avoid restating what the
  code already says.

## Files worth knowing

| Path | Role |
|---|---|
| [src/_cameras.py](src/_cameras.py) | Camera-config schema lives here (`MODELS`, `_validate`). |
| [src/_env.py](src/_env.py) | `.env` lookup order: `src/.env` → `./.env` → `~/.config/tapo-onvif/.env`. |
| [src/tapo_to_rtsp.py](src/tapo_to_rtsp.py) | One bridge process per camera; subclasses pytapo's `Streamer`. |
| [src/snapshot_server.py](src/snapshot_server.py) | One ffmpeg per (camera, lens), atomically rewriting `/tmp/tapo_snaps/<stream>.jpg` at 1 fps. |
| [src/onvif_server.py](src/onvif_server.py) | Hand-written SOAP for the ~15 ONVIF ops UniFi/Scrypted actually call. |
| [src/run_bridge.sh](src/run_bridge.sh) | Renders mediamtx config, enumerates cameras, supervises the lot. |
| [config/mediamtx.yml.template](config/mediamtx.yml.template) | Substituted by `run_bridge.sh` into `tmp/mediamtx.yml`. |
| [docs/](docs/) | Historical reverse-engineering notes (BCCP, keychain, PacketLogger). |

## Runtime artefacts

Everything generated at runtime lives under `tmp/` (gitignored):

- `tmp/mediamtx.yml` — rendered config (`0600`, contains `READ_PASS`).
- `tmp/mediamtx.log`, `tmp/tapo-onvif-<name>.log`, `tmp/snapshot.log`,
  `tmp/onvif.log`, `tmp/tapo-launchd.{log,err}` — per-component logs.
- `/tmp/tapo_snaps/<stream>.jpg` — current JPEG per lens (note:
  this is the system `/tmp`, not the repo's `tmp/`).

## What "done" looks like for changes here

Changes that touch the bridge pipeline aren't done until you've
verified end-to-end on a real camera: stream is publishing in
mediamtx (`grep "is publishing" tmp/mediamtx.log`), `ffprobe
rtsp://…/<name>_wide` returns codec info, and the snapshot endpoint
serves a non-empty JPEG. Type-checks and unit tests don't exist yet
and aren't a substitute for that.

[pytapo]: https://pypi.org/project/pytapo/
