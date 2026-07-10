"""Dedicated-account passthrough — CLAUDE_CONFIG_DIR reaches the spawned claude,
and the parent's Claude auth env is stripped."""
from loki.core import brain, config


class _FakeProc:
    def __init__(self):
        self.pid = 1
        self.returncode = 0

    def communicate(self, input=None, timeout=None):
        return ('{"result":"ok","session_id":"s","is_error":false}', "")


def test_config_dir_injected_and_auth_stripped(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_CONFIG_DIR", r"C:\loki-account")
    monkeypatch.setenv("CLAUDE_CODE_SESSION", "leak")   # parent session auth
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")

    captured = {}

    def fake_popen(cmd, **kw):
        captured["env"] = kw.get("env", {})
        return _FakeProc()

    monkeypatch.setattr(brain.subprocess, "Popen", fake_popen)
    brain.run_claude("hi", None)

    env = captured["env"]
    assert env["CLAUDE_CONFIG_DIR"] == r"C:\loki-account"   # dedicated account
    assert "CLAUDE_CODE_SESSION" not in env                 # parent auth stripped
    assert "ANTHROPIC_API_KEY" not in env


def test_no_config_dir_when_unset(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_CONFIG_DIR", "")
    captured = {}

    def fake_popen(cmd, **kw):
        captured["env"] = kw.get("env", {})
        return _FakeProc()

    monkeypatch.setattr(brain.subprocess, "Popen", fake_popen)
    brain.run_claude("hi", None)
    assert "CLAUDE_CONFIG_DIR" not in captured["env"]
