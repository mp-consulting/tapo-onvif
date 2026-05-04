"""Tests for src/_render_mediamtx.py.

Pins the YAML-quoting behaviour: the rendered output must parse back
to the original READ_USER / READ_PASS regardless of the special
characters in the value (':', '*', "'", leading whitespace, etc.).
A regression here was the entire point of extracting this module —
the old inline shell-heredoc version corrupted mediamtx.yml on any
password with a YAML-significant character.
"""
import os
import stat

import pytest
import yaml

import _render_mediamtx as r


TEMPLATE = """\
authMethod: internal
authInternalUsers:
  - user: publish
    pass: publish
    permissions:
      - action: publish
  - user: '__READ_USER__'
    pass: '__READ_PASS__'
    permissions:
      - action: read
"""


def parse_users(rendered: str) -> dict:
    doc = yaml.safe_load(rendered)
    return {u["user"]: u["pass"] for u in doc["authInternalUsers"]}


def test_yaml_squote_doubles_internal_apostrophes():
    assert r.yaml_squote("plain")     == "'plain'"
    assert r.yaml_squote("can't")     == "'can''t'"
    # 2 internal ' → doubled to 4, then wrapped in outer ' = 5 each side.
    assert r.yaml_squote("''nested''") == "'''''nested'''''"
    assert r.yaml_squote("")          == "''"


@pytest.mark.parametrize("password", [
    "plain",
    "with:colon",
    "with*star",
    "with&amp",
    "with|pipe",
    "with!bang",
    "  leading-spaces",
    "trailing-spaces  ",
    "embedded 'apostrophe'",
    "yes:*&!|all-the-things",
    "12345",
])
def test_render_round_trips_through_yaml(password):
    """The whole reason this module exists: render output must yaml.safe_load
    back to the literal value, no matter what's in the password."""
    out = r.render(TEMPLATE, {"READ_USER": "unifi", "READ_PASS": password})
    users = parse_users(out)
    assert users["unifi"] == password


def test_render_preserves_publish_user(tmp_path):
    out = r.render(TEMPLATE, {"READ_USER": "u", "READ_PASS": "p"})
    users = parse_users(out)
    # Publish user is NOT a placeholder; it stays exactly as written.
    assert users["publish"] == "publish"
    assert users["u"]       == "p"


def test_render_exits_on_missing_read_user():
    with pytest.raises(SystemExit) as exc:
        r.render(TEMPLATE, {"READ_PASS": "p"})
    assert "READ_USER" in str(exc.value)


def test_render_exits_on_missing_read_pass():
    with pytest.raises(SystemExit) as exc:
        r.render(TEMPLATE, {"READ_USER": "u"})
    assert "READ_PASS" in str(exc.value)


def test_main_writes_file_with_0600_perms(tmp_path, monkeypatch):
    """End-to-end via main(): permissions must be 0600 because the
    rendered file contains the LAN-facing password in plaintext."""
    src = tmp_path / "tpl.yml"
    src.write_text(TEMPLATE)
    dst = tmp_path / "out.yml"

    monkeypatch.setattr(r, "load_dotenv",
                        lambda here: {"READ_USER": "u", "READ_PASS": "p"})
    r.main(["_render_mediamtx.py", str(src), str(dst)])

    mode = stat.S_IMODE(os.stat(dst).st_mode)
    assert mode == 0o600
    users = parse_users(dst.read_text())
    assert users == {"publish": "publish", "u": "p"}


def test_main_overwrites_existing_file(tmp_path, monkeypatch):
    """Re-running run_bridge.sh must rewrite a stale mediamtx.yml,
    not error or append."""
    src = tmp_path / "tpl.yml"
    src.write_text(TEMPLATE)
    dst = tmp_path / "out.yml"
    dst.write_text("stale content from a previous run")

    monkeypatch.setattr(r, "load_dotenv",
                        lambda here: {"READ_USER": "u", "READ_PASS": "p"})
    r.main(["_render_mediamtx.py", str(src), str(dst)])

    out = dst.read_text()
    assert "stale" not in out
    assert "authInternalUsers" in out


def test_main_argv_validation(tmp_path):
    with pytest.raises(SystemExit) as exc:
        r.main(["_render_mediamtx.py"])
    assert "usage" in str(exc.value).lower()
