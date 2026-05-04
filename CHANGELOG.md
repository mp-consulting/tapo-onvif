# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **ONVIF SOAP parser hardened.** `parse_op` no longer feeds untrusted
  request bytes to `xml.etree.ElementTree`, whose docs warn it is not
  safe against malicious input (internal-entity expansion / "billion
  laughs"). Replaced with a regex extractor — same behaviour, no XML
  parser involved.
- **ONVIF POST body capped at 64 KiB.** Previously the handler trusted
  `Content-Length` and read the full body into memory; a hostile
  client could pin RAM by claiming a huge length. Oversize requests
  now return 413 before any read.
- **Internal-error SOAP faults no longer echo the exception text.**
  Previously `<s:Reason>` carried `f"Internal error: {e}"`, which
  could leak file paths or other internals. Now logged server-side
  via `logging.exception` and returned to the client as a generic
  "Internal error".

### Changed
- `ProfileToken` lookup deduplicated into a single `_select_profile`
  helper used by `op_GetProfile` / `op_GetStreamUri` / `op_GetSnapshotUri`.
- Snapshot HTTP handler's error/header-emit branches deduplicated
  into `_send_empty` / `_send_jpeg_headers` helpers.

### Added
- **Test suite under `tests/`** (61 tests): config validation,
  `.env` loader semantics, ONVIF SOAP helpers + every registered
  handler + the new security boundaries (entity-bomb, oversize body,
  no-leak fault), snapshot helpers.
- **GitHub Actions CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml))
  running `pytest` on Python 3.11 / 3.12 / 3.13 for every push and PR.

### Investigated (not shipped)
- **Real cam-mic audio in the published RTSP streams.** Two approaches
  prototyped on the `feat/audio-support` branch and reverted:
  1. *Split-process via pytapo's `includeAudio=True` side-channel.*
     Throughput logging showed `audio recv 0.0 KiB` end-to-end —
     pytapo's session loop only returns one PES per response and
     overwrites the buffer next iteration, silently dropping most
     audio packets.
  2. *Single ffmpeg with `-c:a:0 pcm_mulaw` input override on the
     MPEG-TS audio (declared at stream type 0x91, which standard
     MPEG-TS assigns to MP3 but TP-Link uses for G.711 μ-law).*
     ffmpeg crashed at startup; cam session got stuck for minutes
     afterward.
  Notes captured in CLAUDE.md / README. Likely-cleanest path forward
  is a tiny MPEG-TS PMT-rewriter that fixes 0x91 to a recognised
  codec (e.g. 0x90 PCMA-style or a proper PCMU descriptor) before
  handing the bytes to ffmpeg. Until then: silent AAC remains.

### Fixed
- **ONVIF `Model` field** is now just the camera model (e.g. `C675D`)
  instead of `Tapo<MODEL>-<name>-<kind>`. UniFi Protect's adoption UI
  renders `<Manufacturer> <Model>`; the long form was hard to scan in
  the device list. The friendly name (used in the ONVIF scope and the
  per-(cam,lens) display name) is now `<name>_<kind>`.

### Changed
- **Project renamed `tapo-bridge` → `tapo-onvif`.** The new name
  reflects what the project actually produces (ONVIF Profile-S
  endpoints) and isn't pinned to a single NVR (UniFi remains the
  primary integration target). All in-repo references, the launchd
  plist label, and the XDG-style config dir lookups now use the new
  name.

  **Breaking for upgraders:**
  - launchd plist label is now `com.tapo.onvif`. The new
    `install.sh launchd` removes any previous `com.tapo.bridge`
    plist before installing the new one.
  - User-config dir lookup is now `~/.config/tapo-onvif/`
    (previously `~/.config/tapo-bridge/`). If you used that path,
    rename it.
  - Per-camera log files in `tmp/` are now `tapo-onvif-<name>.log`
    (previously `tapo-bridge-<name>.log`).

### Added
- Multi-camera support via `config/cameras.yml` (one entry per Tapo
  camera; lens layout derived from `model`).
- `src/_cameras.py` — camera-config loader and validator (rejects
  duplicate names, missing/extra lens kinds, port collisions).
- `src/_env.py` — shared `.env` loader used by every Python entrypoint.
- `config/cameras.yml.example` template; `install.sh` copies it on
  first run alongside `.env`.
- `config/mediamtx.yml.template` rendered to `tmp/mediamtx.yml` at
  startup with `READ_USER` / `READ_PASS` substituted from `.env`.
- Separate `READ_USER` / `READ_PASS` env vars for LAN-facing RTSP/ONVIF
  read credentials, distinct from the loopback-only `publish/publish`
  publish credentials.
- `CLAUDE.md` orientation document for AI/agent contributors.
- `CHANGELOG.md` (this file).

### Changed
- `src/tapo_to_rtsp.py` now takes `--camera <name>` and runs one
  process per camera; `run_bridge.sh` enumerates cameras from
  `cameras.yml` and spawns a bridge for each.
- `src/snapshot_server.py` and `src/onvif_server.py` iterate over every
  (camera, lens) pair from `cameras.yml`; ONVIF endpoints are spawned
  per lens on the port declared under `onvif_ports.<kind>`.
- Stream paths are now derived from camera name + lens kind
  (`<name>_<kind>`) instead of the hardcoded `c675d_wide` / `c675d_tele`.
- Snapshot HTTP routes follow the same scheme: `/<name>_<kind>`.
- README rewritten around the multi-camera workflow and the
  `.env` / `cameras.yml` split. Reframed as a UniFi-first project
  (UniFi Protect Advanced Adoption is the primary integration target;
  other NVRs work via the same RTSP/ONVIF endpoints). Made the model
  scope explicit (C675D fully implemented today; framework
  model-agnostic) and added an *Adding a model* guide. Replaced the
  ASCII architecture diagram with a Mermaid one. Documented the
  account caveat for battery cameras (no Tapo-app sub-account, so
  `CAM_USER`/`CAM_PASS` are the real account creds).

### Removed
- Tracked `config/mediamtx.yml` (now generated at startup from
  `mediamtx.yml.template` into `tmp/mediamtx.yml`, gitignored).
- Single-camera env vars `CAM_IP`, `ONVIF_WIDE_PORT`, `ONVIF_TELE_PORT`
  (replaced by per-camera entries in `cameras.yml`).

### Security
- `.gitignore` excludes `config/*.yml` so `config/cameras.yml` (which
  contains LAN IPs) is not accidentally committed; the
  `.example` / `.template` siblings remain tracked.
- Generated `tmp/mediamtx.yml` written with `0600` permissions because
  it contains the substituted `READ_PASS`.

## [0.1.0] - 2026-05-04

### Added
- Initial release. Single-camera C675D bridge: pytapo Streamd → ffmpeg
  HEVC→H.264 → mediamtx RTSP, hand-written ONVIF Profile-S server,
  HTTP JPEG snapshot server, watchdog with launchd-driven restart.
- `docs/` with reverse-engineering notes (BCCP protocol, keychain
  search, PacketLogger capture).

[Unreleased]: https://github.com/mp-consulting/tapo-onvif/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mp-consulting/tapo-onvif/releases/tag/v0.1.0
