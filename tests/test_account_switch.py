"""`!account on|off` — which of two Claude accounts a spawn runs as.

The token in `.env` decides the account; this decides whether to use it. The
switch has to reach *every* path that spawns `claude`, and it must never touch
the token itself — `on` has to have something to go back to.
"""
import pytest

from loki.core import account, brain, commands, config, sessions


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(account, "STATE_FILE", tmp_path / "account.json")
    monkeypatch.setattr(config, "CLAUDE_OAUTH_TOKEN", "sk-ant-oat01-ABC123XYZ789")
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(sessions, "_state", {})
    return tmp_path


class _FakeProc:
    def __init__(self):
        self.pid = 1
        self.returncode = 0

    def communicate(self, input=None, timeout=None):
        return ('{"result":"ok","session_id":"s","is_error":false}', "")


def _spawn_env(monkeypatch) -> dict:
    captured = {}

    def fake_popen(cmd, **kw):
        captured["env"] = kw.get("env", {})
        return _FakeProc()

    monkeypatch.setattr(brain.subprocess, "Popen", fake_popen)
    brain.run_claude("hi", None)
    return captured["env"]


# ── default and state ────────────────────────────────────────────────────────
def test_default_is_on_so_upgrades_do_not_change_account(pinned, monkeypatch):
    """No state file = an install that never touched the switch. It must keep
    doing exactly what its .env says."""
    assert not account.STATE_FILE.exists()
    assert account.is_on() is True
    assert _spawn_env(monkeypatch)["CLAUDE_CODE_OAUTH_TOKEN"].startswith("sk-ant-oat01")


def test_off_falls_back_to_the_config_dir_login(pinned, monkeypatch):
    account.set_on(False)
    assert account.token() == ""
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in _spawn_env(monkeypatch)


def test_on_restores_the_same_token(pinned, monkeypatch):
    account.set_on(False)
    account.set_on(True)
    assert _spawn_env(monkeypatch)["CLAUDE_CODE_OAUTH_TOKEN"] == config.CLAUDE_OAUTH_TOKEN


def test_off_never_touches_the_token(pinned):
    account.set_on(False)
    assert config.CLAUDE_OAUTH_TOKEN                      # still configured
    assert account.configured() is True
    assert "sk-ant-oat01-ABC123XYZ789" not in account.STATE_FILE.read_text("utf-8")


def test_state_survives_a_restart(pinned):
    account.set_on(False)
    assert account.is_on() is False                       # re-read from disk


def test_set_on_reports_no_change(pinned):
    assert account.set_on(False) is True
    assert account.set_on(False) is False


# ── the command ──────────────────────────────────────────────────────────────
def test_command_reports_status(pinned):
    reply = commands.account_cmd("")
    assert "ABC123" not in reply                          # never the whole token
    assert "789" in reply                                 # but enough to tell apart


def test_command_turns_it_off_and_on(pinned):
    off = commands.account_cmd("off")
    assert account.is_on() is False and off
    on = commands.account_cmd("on")
    assert account.is_on() is True and on


def test_korean_spellings(pinned):
    commands.account_cmd("끄기")
    assert account.is_on() is False
    commands.account_cmd("켜기")
    assert account.is_on() is True


def test_junk_argument_gets_help_not_a_flip(pinned):
    reply = commands.account_cmd("maybe")
    assert account.is_on() is True
    assert "!account" in reply


def test_flip_clears_remembered_sessions(pinned):
    """A resumed conversation would replay under the other account's login and
    quota, so the switch drops them rather than carrying them across."""
    sessions.remember("D0OWNER", "session-abc")
    assert sessions.active() == 1
    commands.account_cmd("off")
    assert sessions.active() == 0


def test_no_token_configured_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_OAUTH_TOKEN", "")
    monkeypatch.setattr(account, "STATE_FILE", tmp_path / "account.json")
    reply = commands.account_cmd("off")
    assert not account.STATE_FILE.exists()                # nothing to switch
    assert "CLAUDE_CODE_OAUTH_TOKEN" in reply             # and it says what to set


def test_fingerprint_hides_the_token(pinned):
    fp = account.fingerprint()
    assert fp == "…XYZ789"
    assert config.CLAUDE_OAUTH_TOKEN not in fp


# ── through the real dispatch ────────────────────────────────────────────────
def test_owner_can_flip_it_from_a_dm(adapter, pinned):
    """End to end: the message reaches the router and the state actually moves."""
    from tests.conftest import event
    adapter._dispatch({"event_id": "ac1"}, event(text="!account off"),
                      is_mention=False)
    assert account.is_on() is False
    assert adapter.submitted == []                     # answered, not queued
    assert adapter.app.client.texts()


def test_a_guest_cannot_flip_it(adapter, pinned):
    """`!account` is owner-gated in `_builtin`, like every built-in. A guest's
    message must fall through to the brain as text, not change the account the
    owner's subscription spends from."""
    from tests.conftest import event
    adapter._dispatch({"event_id": "ac2"},
                      event(text="!account off", user="UGUEST", channel="C0PUB"),
                      is_mention=True)
    assert account.is_on() is True                     # untouched
    assert not account.STATE_FILE.exists()
    # and it really did travel the whole dispatch — it reached the brain as
    # ordinary text, rather than stopping short for some unrelated reason.
    assert adapter.submitted and adapter.submitted[0]["kind"] == "guest"
