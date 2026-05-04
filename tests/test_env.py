"""Tests for src/_env.py — the .env loader.

Behaviours under test:
* Reads the first existing file in (here/.env, here/../.env,
  ~/.config/tapo-onvif/.env) and stops there.
* Skips blank lines and comments.
* Strips matching surrounding quotes.
* `os.environ` only overrides keys that came from the file (not
  arbitrary new keys), matching the comment in `_env.py`.
"""
import os
import textwrap

from _env import load_dotenv


def test_reads_basic_kv(tmp_path):
    (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")
    out = load_dotenv(str(tmp_path))
    assert out == {"FOO": "bar", "BAZ": "qux"}


def test_skips_blank_and_comment_lines(tmp_path):
    (tmp_path / ".env").write_text(textwrap.dedent("""\
        # this is a comment
        FOO=bar

        # another
        BAZ=qux
    """))
    out = load_dotenv(str(tmp_path))
    assert out == {"FOO": "bar", "BAZ": "qux"}


def test_strips_double_and_single_quotes(tmp_path):
    (tmp_path / ".env").write_text('A="hello"\nB=\'world\'\nC=raw\n')
    out = load_dotenv(str(tmp_path))
    assert out == {"A": "hello", "B": "world", "C": "raw"}


def test_first_match_wins(tmp_path):
    # `here/.env` should take precedence over `here/../.env`.
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / ".env").write_text("WHO=here\n")
    (tmp_path / ".env").write_text("WHO=parent\n")
    assert load_dotenv(str(sub)) == {"WHO": "here"}


def test_falls_back_to_parent(tmp_path):
    sub = tmp_path / "src"
    sub.mkdir()
    (tmp_path / ".env").write_text("WHO=parent\n")
    assert load_dotenv(str(sub)) == {"WHO": "parent"}


def test_no_env_file_returns_empty(tmp_path, monkeypatch):
    # Stub out the home-dir fallback so a developer's real config
    # can't leak in.
    sub = tmp_path / "src"
    sub.mkdir()
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(tmp_path / "no-such-home"))
    assert load_dotenv(str(sub)) == {}


def test_environ_overrides_existing_file_keys(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("FOO=from-file\nBAR=keep\n")
    monkeypatch.setenv("FOO", "from-environ")
    monkeypatch.setenv("UNRELATED", "ignored")
    out = load_dotenv(str(tmp_path))
    assert out["FOO"] == "from-environ"
    assert out["BAR"] == "keep"
    # Keys not present in the file are NOT pulled in from os.environ.
    assert "UNRELATED" not in out


def test_ignores_lines_without_equals(tmp_path):
    (tmp_path / ".env").write_text("FOO=bar\ngarbage line\nBAZ=qux\n")
    out = load_dotenv(str(tmp_path))
    assert out == {"FOO": "bar", "BAZ": "qux"}
