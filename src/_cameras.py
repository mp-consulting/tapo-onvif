"""Shared camera-config loader. Reads config/cameras.yml relative to
the repo root and validates it.

Camera shape on output (one dict per camera):
    name      str    stable identifier (also stream prefix)
    model     str    e.g. "c675d", "c200"
    ip        str    LAN IP
    lenses    list of {
        kind          str   "wide" / "tele" / "main" / …
        stream_path   str   "<name>_<kind>"  (mediamtx path)
        snap_path     str   "/<name>_<kind>" (snapshot HTTP route)
        onvif_port    int   per-lens ONVIF listen port
    }

The lens kinds for each model are fixed in MODELS; the user supplies
one ONVIF port per kind via `onvif_ports:` in cameras.yml.
"""
import os
import sys

import yaml


# Lens layouts per model. Add new models here.
MODELS = {
    "c675d": ["wide", "tele"],   # dual-lens battery cam
    "c200":  ["main"],           # single-lens placeholder (bridge support TODO)
}


def _config_path(here: str) -> str:
    for path in (
        os.path.join(here, "..", "config", "cameras.yml"),
        os.path.expanduser("~/.config/tapo-bridge/cameras.yml"),
    ):
        if os.path.exists(path):
            return path
    sys.exit("ERROR: config/cameras.yml not found — copy "
             "config/cameras.yml.example and edit.")


def load_cameras(here: str) -> list[dict]:
    with open(_config_path(here)) as f:
        doc = yaml.safe_load(f) or {}
    cams = doc.get("cameras") or []
    if not cams:
        sys.exit("ERROR: cameras.yml has no `cameras:` entries.")

    seen_names: set[str] = set()
    seen_ports: set[int] = set()
    out: list[dict] = []
    for raw in cams:
        out.append(_validate(raw, seen_names, seen_ports))
    return out


def _validate(raw: dict, seen_names: set, seen_ports: set) -> dict:
    name = raw.get("name", "")
    if not name or not all(c.isalnum() or c == "_" for c in name):
        sys.exit(f"ERROR: camera name {name!r} must be alphanumeric/underscore "
                 f"(it's used as a stream path and URL fragment).")
    if name in seen_names:
        sys.exit(f"ERROR: duplicate camera name {name!r}.")
    seen_names.add(name)

    model = raw.get("model", "")
    if model not in MODELS:
        sys.exit(f"ERROR: camera {name!r}: unsupported model {model!r}. "
                 f"Supported: {sorted(MODELS)}.")

    ip = raw.get("ip", "")
    if not ip:
        sys.exit(f"ERROR: camera {name!r}: missing `ip`.")

    expected_kinds = MODELS[model]
    ports = raw.get("onvif_ports") or {}
    if not isinstance(ports, dict):
        sys.exit(f"ERROR: camera {name!r}: `onvif_ports` must be a mapping.")
    missing = [k for k in expected_kinds if k not in ports]
    extra   = [k for k in ports if k not in expected_kinds]
    if missing or extra:
        sys.exit(f"ERROR: camera {name!r}: onvif_ports keys must be exactly "
                 f"{expected_kinds} (missing={missing}, unexpected={extra}).")

    lenses = []
    for kind in expected_kinds:
        port = ports[kind]
        if not isinstance(port, int):
            sys.exit(f"ERROR: camera {name!r}: onvif_ports[{kind!r}] must be an int.")
        if port in seen_ports:
            sys.exit(f"ERROR: ONVIF port {port} reused (camera {name!r}, lens {kind!r}).")
        seen_ports.add(port)
        lenses.append({
            "kind": kind,
            "stream_path": f"{name}_{kind}",
            "snap_path":   f"/{name}_{kind}",
            "onvif_port":  port,
        })

    return {"name": name, "model": model, "ip": ip, "lenses": lenses}


def find_camera(cams: list[dict], name: str) -> dict:
    for c in cams:
        if c["name"] == name:
            return c
    sys.exit(f"ERROR: camera {name!r} not found in cameras.yml. "
             f"Known: {[c['name'] for c in cams]}.")


if __name__ == "__main__":
    # run_bridge.sh enumerates camera names without parsing YAML in bash.
    here = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) > 1 and sys.argv[1] == "names":
        for c in load_cameras(here):
            print(c["name"])
    else:
        sys.exit("usage: _cameras.py names")
