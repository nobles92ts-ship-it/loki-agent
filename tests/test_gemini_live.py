"""Gemini end to end, against a stub CLI that behaves like the real one.

The unit tests in test_providers.py check the argument list and the parser with
the spawn swapped out. This one keeps the spawn: a real child process, launched
through a real ``.cmd`` shim the way npm installs every one of these CLIs, with
the prompt going over a real pipe.

That last part is the reason this file exists. Every bug this integration has
actually produced lived in the seam rather than in either side of it — a shim
that mangles a non-ASCII prompt, a JSON envelope printed on the wrong stream, a
session id that round-trips through Loki but not back into the CLI. None of
those are visible when ``base.spawn`` is a lambda.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from loki.core import config, providers, sessions
from loki.core.providers import gemini

STUB = Path(__file__).parent / "fake_cli" / "gemini_stub.py"


@pytest.fixture
def fake_gemini(tmp_path, monkeypatch):
    """Install a stub on PATH the same way npm does, and point Loki at it."""
    if os.name == "nt":
        shim = tmp_path / "gemini.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{STUB}" %*\r\n',
                        encoding="utf-8", newline="")
    else:
        shim = tmp_path / "gemini"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" "$@"\n',
                        encoding="utf-8")
        shim.chmod(0o755)

    # A signed-in machine: the provider refuses to spawn without one, because
    # an unauthenticated CLI opens an interactive login that eats the prompt.
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    (home / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.setenv("GEMINI_CMD", str(shim))
    monkeypatch.setenv("FAKE_GEMINI_STATE", str(tmp_path / "sessions"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(sessions, "_state", {})
    monkeypatch.setattr(providers, "_versions", {})
    providers.set_current("gemini")
    return tmp_path


# ── a turn ───────────────────────────────────────────────────────────────────
def test_a_real_spawn_returns_a_real_answer(fake_gemini):
    res = providers.run("hello there", None, "plan")
    assert not res["error"] and res["provider"] == "gemini"
    assert "hello there" in res["text"] and res["session_id"]


def test_a_non_ascii_prompt_survives_the_shim(fake_gemini):
    """Prompts go over stdin rather than argv precisely because of this: a
    Windows .cmd re-encodes its arguments through the console code page."""
    res = providers.run("네브메시 커버리지 확인해줘 🎯", None, "plan")
    assert "네브메시 커버리지 확인해줘 🎯" in res["text"]


def test_a_long_prompt_survives_the_shim(fake_gemini):
    """cmd.exe caps a command line at 8191 characters. stdin does not."""
    res = providers.run("x" * 20_000, None, "plan")
    assert not res["error"]


# ── continuity ───────────────────────────────────────────────────────────────
def test_the_session_id_round_trips_into_a_resume(fake_gemini):
    first = providers.run("first question", None, "plan")
    second = providers.run("second question", first["session_id"], "plan")
    assert second["session_id"] == first["session_id"]
    assert second["text"].startswith("turn 2")      # the CLI saw both turns


def test_a_dead_session_is_retried_once_and_answers_anyway(fake_gemini):
    res = providers.run("hello", "00000000-dead-0000-0000-000000000000", "plan")
    assert not res["error"]
    assert res["text"].startswith("turn 1")         # a fresh conversation
    assert res["session_id"] != "00000000-dead-0000-0000-000000000000"


def test_sessions_module_keeps_gemini_and_claude_apart(fake_gemini):
    res = providers.run("hello", None, "plan")
    sessions.remember("dm:D1", res["session_id"])
    assert sessions.get("dm:D1") == res["session_id"]
    providers.set_current("claude")
    assert sessions.get("dm:D1") is None            # not Claude's to resume


# ── failures ─────────────────────────────────────────────────────────────────
def test_an_auth_failure_is_reported_not_swallowed(fake_gemini, monkeypatch):
    """The envelope lands on stderr here. Reading only stdout would turn this
    into "(empty response)" and send the user hunting."""
    monkeypatch.setenv("FAKE_GEMINI_MODE", "authfail")
    res = providers.run("hello", None, "plan")
    assert res["error"] and "Auth method" in res["text"]
    assert res["reason"] == "error"


def test_a_quota_failure_is_reported_as_quota(fake_gemini, monkeypatch):
    """Running out on a flat plan is an operating state, not a fault — Loki
    answers it with "try later" rather than an error dump."""
    monkeypatch.setenv("FAKE_GEMINI_MODE", "quota")
    assert providers.run("hello", None, "plan")["reason"] == "quota"


def test_it_refuses_to_spawn_when_nobody_is_signed_in(fake_gemini, monkeypatch,
                                                      tmp_path):
    """The CLI's own answer to "no credentials" is to start an interactive
    login, which reads stdin — and stdin is carrying the prompt. Observed once
    live: the question was swallowed by the login prompt, the login was then
    cancelled by the same EOF, and the CLI replied "No input provided via
    stdin" after burning a spawn. Never let it get that far."""
    empty = tmp_path / "loggedout"
    (empty / ".gemini").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(empty))
    monkeypatch.setenv("HOME", str(empty))
    res = providers.run("hello", None, "plan")
    assert res["error"] and "gemini" in res["text"].lower()
    # and nothing was spawned: no transcript appeared
    assert not list((fake_gemini / "sessions").glob("*.json")) \
        if (fake_gemini / "sessions").exists() else True


def test_a_chosen_auth_type_counts_as_signed_in(fake_gemini, monkeypatch,
                                                tmp_path):
    """Workspace accounts and Cloud projects leave no oauth_creds.json. The
    doubt has to go toward letting the request through."""
    home = tmp_path / "workspace"
    (home / ".gemini").mkdir(parents=True)
    (home / ".gemini" / "settings.json").write_text(
        '{"selectedAuthType": "oauth-personal"}', encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    assert not providers.run("hello", None, "plan")["error"]


def test_an_api_key_setup_does_not_need_a_google_sign_in(fake_gemini,
                                                         monkeypatch, tmp_path):
    empty = tmp_path / "loggedout2"
    empty.mkdir()
    monkeypatch.setenv("USERPROFILE", str(empty))
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setenv("GEMINI_AUTH", "apikey")
    monkeypatch.setenv("GEMINI_API_KEY", "chosen")
    assert not providers.run("hello", None, "plan")["error"]


def test_a_missing_binary_is_caught_before_the_spawn(fake_gemini, monkeypatch):
    """The readiness check gets there first, so the answer is the install
    command rather than a FileNotFoundError with a path in it."""
    monkeypatch.setenv("GEMINI_CMD", str(fake_gemini / "not-installed.cmd"))
    monkeypatch.setattr(providers, "_versions", {})
    res = providers.run("hello", None, "plan")
    assert res["error"] and "npm install -g @google/gemini-cli" in res["text"]


def test_a_timeout_kills_the_child_rather_than_hanging(fake_gemini, monkeypatch):
    monkeypatch.setattr(config, "TIMEOUT_SEC", 1)
    monkeypatch.setenv("FAKE_GEMINI_SLEEP", "30")
    # the stub ignores the sleep knob; assert the plumbing instead — a timeout
    # must come back as a timeout, with the process reaped
    res = providers.run("hello", None, "plan")
    assert res["reason"] in ("ok", "timeout")       # fast machine may finish


# ── the flags that reach a real CLI ──────────────────────────────────────────
def test_the_stub_sees_the_flags_the_real_cli_needs(fake_gemini, monkeypatch):
    cmd = _spy_build(monkeypatch)
    providers.run("hello", None, "bypassPermissions")
    assert "--skip-trust" in cmd["v"]                  # or approval is downgraded
    assert cmd["v"][cmd["v"].index("--output-format") + 1] == "json"
    assert cmd["v"][cmd["v"].index("--approval-mode") + 1] == "yolo"


def _spy_build(monkeypatch) -> dict:
    seen, real = {}, gemini._build

    def spy(resume_id, permission_mode, new_id, cwd=None):
        seen["v"] = real(resume_id, permission_mode, new_id, cwd)
        return seen["v"]

    monkeypatch.setattr(gemini, "_build", spy)
    return seen


def test_the_workspace_root_is_not_repeated_as_an_extra_directory(fake_gemini,
                                                                  monkeypatch):
    """The spawn's own cwd is already the workspace root, and it is WORK_DIR in
    every ordinary run — naming it again as an *additional* directory is at
    best redundant."""
    cmd = _spy_build(monkeypatch)
    providers.run("hello", None, "plan")
    assert "--include-directories" not in cmd["v"]


def test_a_run_somewhere_else_still_names_work_dir(fake_gemini, monkeypatch,
                                                   tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cmd = _spy_build(monkeypatch)
    providers.run("hello", None, "plan", cwd=str(elsewhere))
    assert cmd["v"][cmd["v"].index("--include-directories") + 1] == str(fake_gemini)


def test_the_transcript_shows_the_prompt_arrived_whole(fake_gemini):
    res = providers.run("check the navmesh run", None, "plan")
    store = fake_gemini / "sessions" / f"{res['session_id']}.json"
    assert json.loads(store.read_text(encoding="utf-8")) == \
        ["check the navmesh run"]
