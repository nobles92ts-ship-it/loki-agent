"""`!provider` — switching brains, and what must survive the switch.

The switch is cheap to get wrong in two directions. Too eager, and a session id
issued by one agent gets handed to another's `--resume`, which fails. Too
cautious — dropping every conversation on a flip, the way `!account` has to —
and switching costs you the thread you were in the middle of.
"""
import pytest

from loki.core import alias, commands, config, providers, registry, sessions


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(config, "PROVIDER", "claude")
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(sessions, "_state", {})
    return tmp_path


# ── sessions are filed per provider ──────────────────────────────────────────
def test_two_providers_keep_separate_conversations(clean):
    sessions.remember("dm:D1", "claude-session")
    providers.set_current("gemini")
    assert sessions.get("dm:D1") is None            # not Claude's id
    sessions.remember("dm:D1", "gemini-session")
    assert sessions.get("dm:D1") == "gemini-session"
    providers.set_current("claude")
    assert sessions.get("dm:D1") == "claude-session"   # still there


def test_a_switch_does_not_drop_anything(clean):
    """Unlike `!account`: the ids stay valid, they just belong to a drawer
    nobody is looking in right now."""
    sessions.remember("dm:D1", "claude-session")
    commands.provider_cmd("gemini")
    assert sessions.active() == 1


def test_new_only_resets_the_provider_you_are_on(clean):
    sessions.remember("dm:D1", "claude-session")
    providers.set_current("gemini")
    sessions.remember("dm:D1", "gemini-session")
    sessions.reset("dm:D1")
    assert sessions.get("dm:D1") is None
    providers.set_current("claude")
    assert sessions.get("dm:D1") == "claude-session"


# ── the command ──────────────────────────────────────────────────────────────
def test_status_lists_every_provider_and_marks_the_current_one(clean):
    reply = commands.provider_cmd("")
    for name in providers.ALL:
        assert f"`{name}`" in reply
    assert "▶" in reply


def test_switching_reports_the_plan_it_moved_to(clean, monkeypatch):
    # stubbed, so the assertion is about the message and not about whether this
    # machine happens to be signed in to Gemini today
    monkeypatch.setattr(providers.ALL["gemini"], "available", lambda: (True, ""))
    reply = commands.provider_cmd("gemini")
    assert providers.name() == "gemini"
    assert providers.ALL["gemini"].PLAN.split()[0] in reply


def test_switching_to_the_same_one_says_so(clean):
    commands.provider_cmd("gemini")
    assert "gemini" in commands.provider_cmd("gemini").lower()
    assert providers.name() == "gemini"


def test_an_unknown_name_lists_the_real_ones_and_changes_nothing(clean):
    reply = commands.provider_cmd("gpt9")
    assert providers.name() == "claude"
    assert "`gemini`" in reply and "`codex`" in reply


def test_a_provider_that_is_not_ready_still_switches_but_says_why(clean, monkeypatch):
    """Refusing the switch would be worse: you would have to fix the setup
    before you could even see what is wrong with it."""
    monkeypatch.setattr(providers.ALL["groq"], "available",
                        lambda: (False, "set GROQ_API_KEY in .env"))
    reply = commands.provider_cmd("groq")
    assert providers.name() == "groq"
    assert "GROQ_API_KEY" in reply


def test_switching_to_a_provider_without_tools_says_that_too(clean, monkeypatch):
    """Groq can talk but cannot read a file. That is a different tool, not a
    slower one, and the moment of switching is when it matters."""
    monkeypatch.setattr(providers.ALL["groq"], "available", lambda: (True, ""))
    reply = commands.provider_cmd("groq")
    assert "tool" in reply.lower() or "도구" in reply


def test_korean_spellings(clean):
    commands.provider_cmd("gemini")
    from loki.core.commands import PROVIDER_RE
    assert PROVIDER_RE.match("!제공자") and PROVIDER_RE.match("!모델 codex")


# ── dispatch and name collisions ─────────────────────────────────────────────
def test_the_new_commands_are_owner_only(adapter, clean):
    from tests.conftest import event
    adapter._dispatch({"event_id": "pv1"},
                      event(text="!provider gemini", user="UGUEST",
                            channel="C0PUB"), is_mention=True)
    assert providers.name() == "claude"
    assert adapter.submitted and adapter.submitted[0]["kind"] == "guest"


def test_owner_can_switch_from_a_dm(adapter, clean):
    from tests.conftest import event
    adapter._dispatch({"event_id": "pv2"}, event(text="!provider codex"),
                      is_mention=False)
    assert providers.name() == "codex"
    assert adapter.submitted == []              # answered, not queued


def test_an_alias_cannot_shadow_the_new_commands(clean):
    """registry probes the live patterns, so a name a command answers is taken
    the moment that command exists — no hand-maintained list to forget."""
    for name in ("provider", "goal", "제공자", "목표"):
        assert registry.taken(name), name
    assert alias.add("provider", "something") .startswith("shadowed:")
