#!/usr/bin/env python3
"""Render config/mediamtx.yml.template → tmp/mediamtx.yml.

Substitutes the single-quoted placeholders '__READ_USER__' and
'__READ_PASS__' with the values from .env, properly YAML-quoted so
passwords containing ':', '*', '&', '|', '!', leading-whitespace,
or single quotes still parse cleanly. Output is chmod 0600 since
it contains the LAN-facing read credentials in plaintext.

Reads .env directly via _env.load_dotenv rather than inheriting from
the environment — that's deliberate, so run_bridge.sh doesn't need
to `set -a; . .env` (which would leak the same secrets to the env of
mediamtx, ffmpeg, and every Python child, where `ps -E` would show them).

Usage:
    _render_mediamtx.py <template> <output>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _env import load_dotenv


# Single-quoted YAML scalar: wrap in ' and double internal '.
# This form accepts any printable input without further escaping.
def yaml_squote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def render(template: str, env: dict) -> str:
    user = env.get("READ_USER", "")
    passw = env.get("READ_PASS", "")
    if not user:
        sys.exit("ERROR: .env missing READ_USER (LAN-facing RTSP read user)")
    if not passw:
        sys.exit("ERROR: .env missing READ_PASS (LAN-facing RTSP read pass)")
    return (template
            .replace("'__READ_USER__'", yaml_squote(user))
            .replace("'__READ_PASS__'", yaml_squote(passw)))


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <template> <output>")
    src, dst = argv[1], argv[2]
    env = load_dotenv(HERE)
    with open(src) as f:
        rendered = render(f.read(), env)
    # Open with 0600 from the start (mode arg + O_CREAT|O_TRUNC).
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(rendered)


if __name__ == "__main__":
    main(sys.argv)
