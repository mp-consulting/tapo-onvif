"""Tests for src/snapshot_server.py — the helpers that don't need
ffmpeg or a real RTSP stream.

`is_valid_jpeg` is the gatekeeper for the snapshot HTTP handler:
returning a partially-written file as a 200 would crash UniFi's image
decoder. The checks here pin its definition of "valid" (SOI + EOI
markers) and confirm it tolerates the obvious failure cases.
"""
import os


def test_streams_dict_built_from_fixture_cameras(server_modules):
    _, snap = server_modules
    assert sorted(snap.STREAMS) == ["front_main", "garden_tele", "garden_wide"]
    for stream, path in snap.STREAMS.items():
        assert path == os.path.join(snap.SNAP_DIR, f"{stream}.jpg")


def test_is_valid_jpeg_accepts_proper_markers(server_modules, tmp_path):
    _, snap = server_modules
    p = tmp_path / "ok.jpg"
    p.write_bytes(b"\xff\xd8" + b"\x00" * 32 + b"\xff\xd9")
    assert snap.is_valid_jpeg(str(p)) is True


def test_is_valid_jpeg_rejects_missing_eoi(server_modules, tmp_path):
    _, snap = server_modules
    p = tmp_path / "truncated.jpg"
    p.write_bytes(b"\xff\xd8" + b"\x00" * 32 + b"\x00\x00")
    assert snap.is_valid_jpeg(str(p)) is False


def test_is_valid_jpeg_rejects_wrong_soi(server_modules, tmp_path):
    _, snap = server_modules
    p = tmp_path / "wrong.jpg"
    p.write_bytes(b"GIF8" + b"\x00" * 32 + b"\xff\xd9")
    assert snap.is_valid_jpeg(str(p)) is False


def test_is_valid_jpeg_handles_missing_file(server_modules, tmp_path):
    _, snap = server_modules
    assert snap.is_valid_jpeg(str(tmp_path / "no-such-file.jpg")) is False


def test_is_valid_jpeg_handles_too_short(server_modules, tmp_path):
    """File shorter than 4 bytes: the seek(-2, 2) read can't get the
    full EOI marker, so the function must return False instead of
    raising."""
    _, snap = server_modules
    p = tmp_path / "tiny.jpg"
    p.write_bytes(b"\xff")
    assert snap.is_valid_jpeg(str(p)) is False


def test_handler_resolve_unknown_path(server_modules, tmp_path):
    _, snap = server_modules
    # _resolve is an instance method but only inspects self.path; we can
    # exercise it without instantiating BaseHTTPRequestHandler by faking
    # the attribute on a bare object.
    class Fake:
        path = "/no-such-stream"
    out_path, status = snap.Handler._resolve(Fake())
    assert (out_path, status) == (None, 404)


def test_handler_resolve_known_path_no_jpeg_yet(server_modules, tmp_path):
    _, snap = server_modules

    class Fake:
        path = "/garden_wide"
    out_path, status = snap.Handler._resolve(Fake())
    # The on-disk JPEG hasn't been produced yet → 503, not 200.
    assert out_path is None
    assert status == 503


def test_handler_resolve_known_path_with_valid_jpeg(server_modules):
    _, snap = server_modules
    target = snap.STREAMS["garden_wide"]
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(b"\xff\xd8" + b"\x00" * 32 + b"\xff\xd9")
    try:
        class Fake:
            path = "/garden_wide?cachebust=1"
        out_path, status = snap.Handler._resolve(Fake())
        assert status == 200
        assert out_path == target
    finally:
        os.unlink(target)
