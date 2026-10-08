"""Sealed Codex runs — the tool-less mode that may serve a restricted request.

The isolation itself was measured against the real CLI with canary files and a
canary environment variable (docs/codex-migration.md). What these tests pin is
the part that could quietly regress in Loki: the flags that do the sealing, the
environment allowlist, and the routing rule that only uses a sealed run when
the owner opted in.
"""
import json

import pytest

from loki.core import config, providers
from loki.core.providers import base, claude, codex, gemini


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(config, "PROVIDER", "claude")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "fallback")
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))
    return tmp_path


class _FakeProc:
    def __init__(self, out=""):
        self.pid, self.returncode, self._out = 1, 0, out

    def communicate(self, input=None, timeout=None):
        return self._out, ""


def _capture(monkeypatch, out='{"type":"thread.started","thread_id":"t1"}'):
    seen = {}

    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, env=kw.get("env", {}), cwd=kw.get("cwd"))
        return _FakeProc(out)

    monkeypatch.setattr(base.subprocess, "Popen", fake_popen)
    return seen


# ── the seal ────────────────────────────────────────────────────────────────
def test_sealed_flags_drop_user_config_and_every_tool_feature():
    flags = codex.sealed_flags()
    assert "--ignore-user-config" in flags and "--ignore-rules" in flags
    disabled = {flags[i + 1] for i, f in enumerate(flags) if f == "--disable"}
    # apps = the ChatGPT connectors, which survive --ignore-user-config;
    # code_mode_host = the exec tool that otherwise remains
    for feature in ("shell_tool", "unified_exec", "apps", "plugins",
                    "code_mode_host", "view_image", "memories", "hooks",
                    "computer_use", "browser_use", "multi_agent"):
        assert feature in disabled
    assert 'sandbox_mode="read-only"' in flags
    assert 'shell_environment_policy.inherit="none"' in flags
    assert "--dangerously-bypass-approvals-and-sandbox" not in flags


def test_sealed_env_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-leak")
    monkeypatch.setenv("JIRA_API_TOKEN", "leak")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "leak")
    monkeypatch.setenv("SOME_FUTURE_SECRET", "leak")
    env = codex.sealed_env()
    assert "leak" not in json.dumps(env) and "xoxb" not in json.dumps(env)
    assert "PATH" in {k.upper() for k in env}


def test_run_sealed_spawns_with_the_seal(clean, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-leak")
    seen = _capture(monkeypatch)
    codex.run_sealed("hi", None, cwd=str(clean))
    cmd = seen["cmd"]
    assert cmd[1] == "exec" and "--ignore-user-config" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "SLACK_BOT_TOKEN" not in seen["env"]
    # the caller's folder is ignored — a guest's loki dir may hold an AGENTS.md
    assert seen["cwd"] == codex.sealed_cwd() != str(clean)


def test_run_sealed_resume_keeps_the_seal(clean, monkeypatch):
    seen = _capture(monkeypatch)
    codex.run_sealed("hi", "t1")
    assert seen["cmd"][1:4] == ["exec", "resume", "t1"]
    assert "--ignore-user-config" in seen["cmd"]


def test_images_ride_on_the_prompt(clean, monkeypatch):
    seen = _capture(monkeypatch)
    codex.run_sealed("hi", None, images=("C:/a.png",))
    i = seen["cmd"].index("--image")
    assert seen["cmd"][i + 1] == "C:/a.png" and seen["cmd"][-1] == "-"


def test_the_owner_dm_path_is_unchanged(clean, monkeypatch):
    seen = _capture(monkeypatch)
    codex.run("hi", None, "bypassPermissions")
    assert "--dangerously-bypass-approvals-and-sandbox" in seen["cmd"]
    assert "--ignore-user-config" not in seen["cmd"]


def test_out_of_credits_is_a_quota_not_a_crash():
    """The exact message a ChatGPT workspace returned when its credits ran out
    (2026-10-01, mid tc-team preflight) — it must reach users as 'quota'."""
    assert base.quota_hit("Your workspace is out of credits. Ask your workspace "
                          "owner to refill in order to continue.")


# ── the routing rule ────────────────────────────────────────────────────────
def test_restricted_requests_still_fall_back_by_default(clean, monkeypatch):
    providers.set_current("codex")
    monkeypatch.setattr(codex, "run_sealed",
                        lambda *a, **k: pytest.fail("sealed without opt-in"))
    monkeypatch.setattr(claude, "run", lambda *a, **k: {"provider": "claude"})
    res = providers.run("hi", None, "plan", settings_file="C:/deny.json")
    assert res["provider"] == "claude"


def test_opt_in_serves_restricted_requests_sealed(clean, monkeypatch):
    providers.set_current("codex")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "sealed")
    called = {}
    monkeypatch.setattr(codex, "run_sealed",
                        lambda p, r, cwd=None, job=None, images=():
                        called.update(p=p) or {"provider": "codex"})
    monkeypatch.setattr(claude, "run", lambda *a, **k: pytest.fail("fell back"))
    res = providers.run("hi", None, "plan", settings_file="C:/deny.json")
    assert res["provider"] == "codex" and called["p"] == "hi"


def test_opt_in_does_not_seal_a_provider_without_a_sealed_mode(clean, monkeypatch):
    providers.set_current("gemini")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "sealed")
    monkeypatch.setattr(claude, "run", lambda *a, **k: {"provider": "claude"})
    assert not providers.sealable(gemini)
    res = providers.run("hi", None, "plan", settings_file="C:/deny.json")
    assert res["provider"] == "claude"


def test_rollback_to_claude_ignores_the_opt_in(clean, monkeypatch):
    """`!provider claude` must put every request back on Claude's deny rules."""
    monkeypatch.setattr(config, "RESTRICTED_MODE", "sealed")
    providers.set_current("codex")
    providers.set_current("claude")
    seen = _capture(monkeypatch, out='{"result":"ok","session_id":"s"}')
    res = providers.run("hi", None, "plan", settings_file="C:/deny.json")
    assert res["provider"] == "claude"
    assert "--settings" in seen["cmd"]
