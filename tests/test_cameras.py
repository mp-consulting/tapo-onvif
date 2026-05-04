"""Validation tests for src/_cameras.py.

`load_cameras` calls `sys.exit(...)` on any malformed input — pytest
catches that as `SystemExit`. Each negative case asserts the exit message
mentions the field at fault, so accidental rewording of an error doesn't
silently turn a strict check into a permissive one.
"""
import pytest

import _cameras
from _cameras import MODELS, find_camera, load_cameras


def write_yml(path, body: str):
    path.write_text(body)


def patch_config(monkeypatch, path):
    monkeypatch.setattr(_cameras, "_config_path", lambda here: str(path))


def test_load_cameras_happy_path(repo_fixture, monkeypatch):
    patch_config(monkeypatch, repo_fixture["cameras_yml"])
    cams = load_cameras(str(repo_fixture["src"]))

    assert [c["name"] for c in cams] == ["garden", "front"]
    garden = cams[0]
    assert garden["model"] == "c675d"
    assert garden["ip"] == "192.168.1.101"
    assert [l["kind"] for l in garden["lenses"]] == ["wide", "tele"]
    wide, tele = garden["lenses"]
    assert wide["stream_path"] == "garden_wide"
    assert wide["snap_path"]   == "/garden_wide"
    assert wide["onvif_port"]  == 8081
    assert tele["stream_path"] == "garden_tele"
    assert tele["onvif_port"]  == 8082

    front = cams[1]
    assert [l["kind"] for l in front["lenses"]] == ["main"]
    assert front["lenses"][0]["onvif_port"] == 8083


def test_models_table_exposes_known_models():
    # Guards against accidental deletion / renaming of supported models.
    assert MODELS["c675d"] == ["wide", "tele"]
    assert MODELS["c200"] == ["main"]


def test_find_camera_returns_match(repo_fixture, monkeypatch):
    patch_config(monkeypatch, repo_fixture["cameras_yml"])
    cams = load_cameras(str(repo_fixture["src"]))
    assert find_camera(cams, "garden")["model"] == "c675d"


def test_find_camera_unknown_exits(repo_fixture, monkeypatch):
    patch_config(monkeypatch, repo_fixture["cameras_yml"])
    cams = load_cameras(str(repo_fixture["src"]))
    with pytest.raises(SystemExit) as exc:
        find_camera(cams, "nope")
    assert "nope" in str(exc.value)


def test_missing_config_file_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _cameras, "_config_path",
        lambda here: (_ for _ in ()).throw(SystemExit("ERROR: cameras.yml not found")),
    )
    with pytest.raises(SystemExit):
        load_cameras(str(tmp_path))


def test_empty_cameras_list_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, "cameras: []\n")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "no `cameras:`" in str(exc.value)


def test_missing_cameras_key_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, "other: stuff\n")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit):
        load_cameras(str(tmp_path))


def test_invalid_name_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: "has space"
    model: c675d
    ip: 1.2.3.4
    onvif_ports: {wide: 8081, tele: 8082}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "alphanumeric" in str(exc.value)


def test_duplicate_name_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c200
    ip: 1.2.3.4
    onvif_ports: {main: 9001}
  - name: a
    model: c200
    ip: 1.2.3.5
    onvif_ports: {main: 9002}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "duplicate" in str(exc.value)


def test_unknown_model_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c9999x
    ip: 1.2.3.4
    onvif_ports: {main: 9001}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "unsupported model" in str(exc.value)


def test_missing_ip_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c200
    onvif_ports: {main: 9001}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "missing `ip`" in str(exc.value)


def test_onvif_ports_missing_lens_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c675d
    ip: 1.2.3.4
    onvif_ports: {wide: 8081}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "missing=['tele']" in str(exc.value) or "tele" in str(exc.value)


def test_onvif_ports_extra_lens_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c200
    ip: 1.2.3.4
    onvif_ports: {main: 9001, bonus: 9002}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "bonus" in str(exc.value)


def test_onvif_ports_must_be_int(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c200
    ip: 1.2.3.4
    onvif_ports: {main: "not-an-int"}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "must be an int" in str(exc.value)


def test_onvif_ports_must_be_mapping(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c200
    ip: 1.2.3.4
    onvif_ports: [9001]
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "must be a mapping" in str(exc.value)


def test_duplicate_onvif_port_across_cameras_exits(tmp_path, monkeypatch):
    p = tmp_path / "cameras.yml"
    write_yml(p, """\
cameras:
  - name: a
    model: c200
    ip: 1.2.3.4
    onvif_ports: {main: 9001}
  - name: b
    model: c200
    ip: 1.2.3.5
    onvif_ports: {main: 9001}
""")
    patch_config(monkeypatch, p)
    with pytest.raises(SystemExit) as exc:
        load_cameras(str(tmp_path))
    assert "reused" in str(exc.value) or "9001" in str(exc.value)
