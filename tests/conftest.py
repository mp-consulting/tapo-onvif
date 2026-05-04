"""Shared test setup.

The bridge modules (`onvif_server`, `snapshot_server`) load `.env` and
`config/cameras.yml` at import time. Tests must therefore (a) put `src/`
on sys.path and (b) make sure those loaders look at fixture data, not
the developer's real cameras.yml or the missing-in-CI config.

The `repo_fixture` fixture renders a self-contained tree under
tmp_path:

    <tmp>/src/             (placeholder — only used as `here` arg)
    <tmp>/config/cameras.yml
    <tmp>/.env

…and `server_modules` reloads `onvif_server` / `snapshot_server` against
that tree, by monkey-patching the lookup helpers in `_cameras` / `_env`
*before* the (re)import.
"""
import importlib
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC  = REPO / "src"
sys.path.insert(0, str(SRC))


FIXTURE_CAMERAS_YML = """\
cameras:
  - name: garden
    model: c675d
    ip: 192.168.1.101
    onvif_ports:
      wide: 8081
      tele: 8082
  - name: front
    model: c200
    ip: 192.168.1.102
    onvif_ports:
      main: 8083
"""

FIXTURE_ENV = """\
# fixture .env — never used against a real cam
READ_USER=unifi
READ_PASS=secretpass
PUBLIC_HOST=192.168.1.50
RTSP_HOST=127.0.0.1
RTSP_PORT=8555
SNAPSHOT_PORT=8683
PUBLISH_USER=publish
PUBLISH_PASS=publish
"""


@pytest.fixture
def repo_fixture(tmp_path):
    src = tmp_path / "src"
    cfg = tmp_path / "config"
    src.mkdir()
    cfg.mkdir()
    cameras_yml = cfg / "cameras.yml"
    cameras_yml.write_text(FIXTURE_CAMERAS_YML)
    env_file = tmp_path / ".env"
    env_file.write_text(FIXTURE_ENV)
    return {
        "root":        tmp_path,
        "src":         src,
        "cameras_yml": cameras_yml,
        "env":         env_file,
    }


@pytest.fixture
def server_modules(monkeypatch, repo_fixture, tmp_path):
    """Reload onvif_server + snapshot_server pinned to the fixture tree.

    Patches `_cameras._config_path` and `_env.load_dotenv` so the
    server modules see only fixture data — they ignore the real
    repo's cameras.yml / .env, both of which are gitignored and so
    absent in CI."""
    import _cameras
    import _env

    monkeypatch.setattr(
        _cameras, "_config_path",
        lambda here: str(repo_fixture["cameras_yml"]),
    )
    real_load = _env.load_dotenv
    monkeypatch.setattr(
        _env, "load_dotenv",
        lambda here: real_load(str(repo_fixture["src"])),
    )

    # snapshot_server makedirs(SNAP_DIR) at import — keep it inside tmp.
    snap_dir = tmp_path / "snaps"
    monkeypatch.setenv("SNAP_DIR", str(snap_dir))
    # ENV.get() reads via the patched loader; the loader's update step
    # only honours os.environ keys present in the file. Append SNAP_DIR
    # so the override path applies.
    with open(repo_fixture["env"], "a") as f:
        f.write(f"SNAP_DIR={snap_dir}\n")

    for name in ("onvif_server", "snapshot_server"):
        sys.modules.pop(name, None)
    onvif_server    = importlib.import_module("onvif_server")
    snapshot_server = importlib.import_module("snapshot_server")
    yield onvif_server, snapshot_server
    for name in ("onvif_server", "snapshot_server"):
        sys.modules.pop(name, None)
