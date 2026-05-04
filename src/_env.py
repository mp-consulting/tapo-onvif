"""Shared .env loader. Search order matches run_bridge.sh."""
import os


def load_dotenv(here: str) -> dict:
    """Read the first .env found in the standard locations, then let
    matching keys from os.environ win (so run_bridge.sh's `set -a`
    export overrides anything stale in the file)."""
    out: dict = {}
    for path in (
        os.path.join(here, ".env"),
        os.path.join(here, "..", ".env"),
        os.path.expanduser("~/.config/tapo-onvif/.env"),
    ):
        if not os.path.exists(path):
            continue
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        break
    out.update({k: v for k, v in os.environ.items() if k in out})
    return out
