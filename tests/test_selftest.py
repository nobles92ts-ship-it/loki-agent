"""The boot self-test — proving read-only mode is read-only, per provider.

The test is "ask it to write a file, then look for the file". Its weak spot is
that a *failed run* also produces no file, and the fix is to notice the
difference: an agent that refused, hit a quota, or is not signed in has proved
nothing, and banking that as a week-long pass is how the guarantee quietly
stops being checked.
"""
import json
import time

import pytest

from loki.core import brain, config, providers, selftest


@pytest.fixture
def boot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(selftest, "RESULT_FILE", tmp_path / "selftest.json")
    monkeypatch.setattr(config, "SELFTEST_ON_BOOT", True)
    monkeypatch.setattr(config, "WRITE_MODE", False)
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(config, "PROVIDER", "claude")
    monkeypatch.setattr(brain, "claude_version", lambda: "1.0")
    return tmp_path


def _answer(monkeypatch, **fields):
    calls = []

    def fake(prompt, *a, **kw):
        calls.append(prompt)
        return {"text": "", "session_id": None, "error": False,
                "reason": "ok", **fields}

    monkeypatch.setattr(brain, "run_claude", fake)
    return calls


def test_a_clean_run_with_no_file_is_a_pass(boot, monkeypatch):
    _answer(monkeypatch)
    selftest.run()
    assert json.loads(selftest.RESULT_FILE.read_text())["version"] == "claude/1.0"


def test_a_write_that_gets_through_stops_the_process(boot, monkeypatch):
    probe = boot / "SELFTEST_SHOULD_NOT_EXIST.txt"

    def fake(prompt, *a, **kw):
        probe.write_text("HACKED", encoding="utf-8")
        return {"text": "", "session_id": None, "error": False, "reason": "ok"}

    monkeypatch.setattr(brain, "run_claude", fake)
    with pytest.raises(SystemExit):
        selftest.run()
    assert not probe.exists()          # cleaned up on the way out


def test_a_failed_run_is_not_recorded_as_a_pass(boot, monkeypatch, capsys):
    """No file appeared because nothing ran. That is not evidence."""
    _answer(monkeypatch, error=True, reason="error",
            text="gemini is installed but not signed in")
    selftest.run()
    assert not selftest.RESULT_FILE.exists()
    assert "inconclusive" in capsys.readouterr().out


def test_a_quota_wall_is_not_a_pass_either(boot, monkeypatch):
    _answer(monkeypatch, error=True, reason="quota", text="usage limit")
    selftest.run()
    assert not selftest.RESULT_FILE.exists()


def test_an_inconclusive_run_is_retried_next_boot(boot, monkeypatch):
    _answer(monkeypatch, error=True, reason="error", text="not signed in")
    selftest.run()
    calls = _answer(monkeypatch)                  # now healthy
    selftest.run()
    assert calls and selftest.RESULT_FILE.exists()


def test_a_recent_pass_is_not_repeated(boot, monkeypatch):
    selftest.RESULT_FILE.write_text(
        json.dumps({"version": "claude/1.0", "ts": time.time()}), encoding="utf-8")
    calls = _answer(monkeypatch)
    selftest.run()
    assert calls == []


def test_switching_provider_re_runs_it(boot, monkeypatch):
    """Each agent spells read-only differently, so a pass proves one mapping —
    not the idea. The cache key carries the provider for exactly that reason."""
    selftest.RESULT_FILE.write_text(
        json.dumps({"version": "claude/1.0", "ts": time.time()}), encoding="utf-8")
    providers.set_current("gemini")
    calls = _answer(monkeypatch)
    selftest.run()
    assert len(calls) == 1
    assert json.loads(selftest.RESULT_FILE.read_text())["version"] == "gemini/1.0"


def test_write_mode_skips_it_entirely(boot, monkeypatch):
    monkeypatch.setattr(config, "WRITE_MODE", True)
    calls = _answer(monkeypatch)
    selftest.run()
    assert calls == []
