"""Codex end to end, against a stub CLI that behaves like the real one.

The piece that needs a real process is the answer handoff. Codex does not put
its final message in the event stream; it writes it to a file Loki creates and
names on the command line. Loki then reads it and deletes it. Three parties, a
temp path and an unlink — none of which a mocked spawn touches.
"""
import os
import sys
from pathlib import Path

import pytest

from loki.core import config, providers

STUB = Path(__file__).parent / "fake_cli" / "codex_stub.py"


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    if os.name == "nt":
        shim = tmp_path / "codex.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{STUB}" %*\r\n',
                        encoding="utf-8", newline="")
    else:
        shim = tmp_path / "codex"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" "$@"\n',
                        encoding="utf-8")
        shim.chmod(0o755)

    monkeypatch.setenv("CODEX_CMD", str(shim))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "threads"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(providers, "_versions", {})
    providers.set_current("codex")
    return tmp_path


def test_the_answer_comes_back_through_the_last_message_file(fake_codex):
    res = providers.run("hello there", None, "plan")
    assert not res["error"] and res["provider"] == "codex"
    assert "hello there" in res["text"] and res["session_id"]


def test_the_temp_file_does_not_survive_the_call(fake_codex):
    before = set(Path(os.environ.get("TEMP", ".")).glob("loki_codex_*"))
    providers.run("hello", None, "plan")
    after = set(Path(os.environ.get("TEMP", ".")).glob("loki_codex_*"))
    assert after <= before


def test_a_non_ascii_prompt_survives_the_shim(fake_codex):
    res = providers.run("네브메시 커버리지 🎯", None, "plan")
    assert "네브메시 커버리지 🎯" in res["text"]


def test_resume_continues_the_same_thread(fake_codex):
    first = providers.run("first", None, "plan")
    second = providers.run("second", first["session_id"], "plan")
    assert second["session_id"] == first["session_id"]
    assert second["text"].startswith("turn 2")


def test_a_stale_login_is_named_rather_than_dumped(fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "reauth")
    res = providers.run("hello", None, "plan")
    assert res["error"] and res["text"].startswith("🔑")
    assert "refresh token" in res["text"].lower()


def test_quota_is_reported_as_quota(fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "quota")
    assert providers.run("hello", None, "plan")["reason"] == "quota"


def test_the_thread_id_is_read_from_the_event_stream(fake_codex):
    res = providers.run("hello", None, "plan")
    assert (fake_codex / "threads" / f"{res['session_id']}.json").exists()
