"""Antigravity (`agy`) — the flat-rate Google path, end to end against a stub.

This is the provider a Google plan actually reaches after Gemini CLI closed to
individual accounts, so it carries the weight the gemini tests used to.

Two behaviours here exist only because the real binary was run first. `-p -`
does not mean "read stdin" — it means "the prompt is a hyphen". And a dead
conversation id is not an error, so there is no retry to write; there is only
the discipline of storing whatever came back.
"""
import os
import sys
from pathlib import Path

import pytest

from loki.core import config, providers, sessions
from loki.core.providers import antigravity

STUB = Path(__file__).parent / "fake_cli" / "antigravity_stub.py"


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    if os.name == "nt":
        shim = tmp_path / "agy.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{STUB}" %*\r\n',
                        encoding="utf-8", newline="")
    else:
        shim = tmp_path / "agy"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" "$@"\n',
                        encoding="utf-8")
        shim.chmod(0o755)

    monkeypatch.setenv("ANTIGRAVITY_CMD", str(shim))
    monkeypatch.setenv("FAKE_AGY_STATE", str(tmp_path / "conv"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.delenv("ANTIGRAVITY_MODEL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_EFFORT", raising=False)
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(providers, "_versions", {})
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(sessions, "_state", {})
    providers.set_current("antigravity")
    return tmp_path


# ── a turn ───────────────────────────────────────────────────────────────────
def test_a_real_spawn_returns_a_real_answer(fake_agy):
    res = providers.run("hello there", None, "plan")
    assert not res["error"] and res["provider"] == "antigravity"
    assert "hello there" in res["text"] and res["session_id"]


def test_the_prompt_goes_on_stdin_and_never_as_an_argument(fake_agy, monkeypatch):
    """⚠️ `-p -` reads like the usual stdin idiom and is not: the real CLI
    takes the `-` as the literal prompt, answers it, and discards stdin. So
    neither `-p` nor the prompt may appear on the command line at all."""
    seen = {}
    real = antigravity._build
    monkeypatch.setattr(antigravity, "_build",
                        lambda *a, **kw: seen.setdefault("cmd", real(*a, **kw)))
    providers.run("the question", None, "plan")
    assert "-p" not in seen["cmd"] and "--print" not in seen["cmd"]
    assert "the question" not in seen["cmd"] and "-" not in seen["cmd"]


def test_a_non_ascii_prompt_survives_the_shim(fake_agy):
    res = providers.run("네브메시 커버리지 🎯", None, "plan")
    assert "네브메시 커버리지 🎯" in res["text"]


def test_a_long_prompt_survives_the_shim(fake_agy):
    """Windows caps a command line; stdin does not."""
    assert not providers.run("x" * 20_000, None, "plan")["error"]


# ── continuity ───────────────────────────────────────────────────────────────
def test_the_conversation_id_round_trips_into_a_resume(fake_agy):
    first = providers.run("first question", None, "plan")
    second = providers.run("second question", first["session_id"], "plan")
    assert second["session_id"] == first["session_id"]
    assert second["text"].startswith("turn 2")


def test_a_dead_conversation_heals_itself_without_a_retry(fake_agy):
    """The CLI warns and starts fresh rather than failing, so the whole job is
    to store the id that came back instead of the one we asked for."""
    dead = "00000000-dead-0000-0000-000000000000"
    res = providers.run("hello", dead, "plan")
    assert not res["error"]
    assert res["session_id"] and res["session_id"] != dead
    assert res["text"].startswith("turn 1")


def test_a_replaced_conversation_is_what_gets_remembered(fake_agy):
    dead = "00000000-dead-0000-0000-000000000000"
    res = providers.run("hello", dead, "plan")
    sessions.remember("dm:D1", res["session_id"])
    assert sessions.get("dm:D1") == res["session_id"]      # not the dead one


# ── permission modes ─────────────────────────────────────────────────────────
def test_plan_mode_does_not_skip_permissions(fake_agy, monkeypatch):
    seen = {}
    real = antigravity._build
    monkeypatch.setattr(antigravity, "_build",
                        lambda *a, **kw: seen.setdefault("cmd", real(*a, **kw)))
    providers.run("hi", None, "plan")
    assert "--dangerously-skip-permissions" not in seen["cmd"]


def test_write_mode_skips_them(fake_agy, monkeypatch):
    seen = {}
    real = antigravity._build
    monkeypatch.setattr(antigravity, "_build",
                        lambda *a, **kw: seen.setdefault("cmd", real(*a, **kw)))
    providers.run("hi", None, "bypassPermissions")
    assert "--dangerously-skip-permissions" in seen["cmd"]


def test_the_cli_timeout_is_pinned_to_lokis_own(fake_agy, monkeypatch):
    """Left at its 5-minute default it would cut a long job short, or outlive
    one Loki had already given up on."""
    monkeypatch.setattr(config, "TIMEOUT_SEC", 900)
    cmd = antigravity._build(None, "plan")
    assert cmd[cmd.index("--print-timeout") + 1] == "900s"


# ── failures ─────────────────────────────────────────────────────────────────
def test_an_error_envelope_is_reported(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "badmodel")
    res = providers.run("hi", None, "plan")
    assert res["error"] and "not recognized" in res["text"]


def test_an_error_keeps_the_id_we_came_in_with(fake_agy, monkeypatch):
    """The error envelope carries `conversation_id: ""` — overwriting a live
    conversation with that would lose the thread."""
    monkeypatch.setenv("FAKE_AGY_MODE", "badmodel")
    res = providers.run("hi", "keep-me", "plan")
    assert res["session_id"] == "keep-me"


def test_quota_is_reported_as_quota(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "quota")
    assert providers.run("hi", None, "plan")["reason"] == "quota"


def test_a_missing_binary_is_named(fake_agy, monkeypatch):
    monkeypatch.setenv("ANTIGRAVITY_CMD", str(fake_agy / "not-installed.cmd"))
    monkeypatch.setattr(providers, "_versions", {})
    res = providers.run("hi", None, "plan")
    assert res["error"] and "install" in res["text"].lower()


def test_a_sandboxed_request_never_reaches_it(fake_agy):
    res = providers.run("hi", None, "plan", settings_file="C:/deny.json")
    assert res["provider"] == "claude"          # forced back, per the rule


# ── finding the binary ───────────────────────────────────────────────────────
def test_it_is_found_where_the_installer_puts_it(monkeypatch, tmp_path):
    """The installer adds its directory to the PATH *registry*, which a process
    that was already running — the Loki worker — will not see until it
    restarts. Without this fallback, installing agy appears to do nothing."""
    monkeypatch.delenv("ANTIGRAVITY_CMD", raising=False)
    monkeypatch.setattr(antigravity.shutil, "which", lambda _: None)
    local = tmp_path / "LocalAppData"
    binary = local / "agy" / "bin" / "agy.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(antigravity, "_DEFAULT_DIRS", (str(binary),))
    assert antigravity.command() == str(binary)
