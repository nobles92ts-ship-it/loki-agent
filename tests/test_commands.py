"""Shared `!` command router — routing, owner gate, and the platform contract.

These lock in the vocabulary every adapter inherits from core.commands, so a
new platform gets identical behaviour instead of a near-miss reimplementation.
"""
import pytest

from loki.core import (autolisten, blocked, commands, config, learn, orgs,
                       scheduler, scope, sessions, usage)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate every piece of state a command can touch."""
    work = tmp_path / "work"
    (work / "shared").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "STATE", state)
    monkeypatch.setattr(config, "SESSION_IDLE_MIN", 120)
    monkeypatch.setattr(blocked, "_FILE", state / "blocked.json")
    monkeypatch.setattr(blocked, "_state", set())
    monkeypatch.setattr(autolisten, "_FILE", state / "al.json")
    monkeypatch.setattr(autolisten, "_state",
                        {"channels": set(), "threads": set()})
    monkeypatch.setattr(sessions, "_FILE", state / "sessions.json")
    monkeypatch.setattr(sessions, "_state", {})
    monkeypatch.setattr(scheduler, "SCHED_FILE", state / "sched.json")
    monkeypatch.setattr(usage, "USAGE_FILE", state / "usage.jsonl")
    monkeypatch.setattr(learn, "LEARN_FILE", state / "learnings.md")
    monkeypatch.setattr(orgs, "_cache",
                        {"stamp": None, "orgs": {}, "member_index": {},
                         "channel_index": {}})
    scope.ensure_manifest()
    return state


USER = "U012ABCDEF"          # realistic Slack ids — length is validated
CHAN = "C012ABCDEF"
DISCORD_USER = "123456789012345678"      # snowflake, for the portability check
DM_KEY = "dm:D012ABCDEF"


def ctx(is_owner=True, channel="D012ABCDEF", thread=None, is_dm=True,
        session_key="dm:D012ABCDEF", user_ids=()):
    return {
        "channel": channel, "thread": thread, "session_key": session_key,
        "is_dm": is_dm, "is_owner": is_owner,
        "name_of": lambda uid: {USER: "alice"}.get(uid, uid),
        "user_ids": list(user_ids),
        "is_user_id": lambda tk: tk.startswith("U") or tk.isdigit(),
        "is_channel_id": lambda tk: tk.startswith("C") or tk.isdigit(),
    }


# ── owner gate ──────────────────────────────────────────────────────────────
def test_guests_reach_no_command(env):
    """The gate lives in the router, so an adapter cannot forget it."""
    for text in ("!jobs", "!usage", "!stop", "!block C9", "!org list",
                 "!new", "!listen", "!schedule list", "!learn note"):
        assert commands.handle(text, ctx(is_owner=False)) is None


def test_plain_chat_is_not_a_command(env):
    for text in ("hello", "what is !jobs", "", "  ", "!nonsense"):
        assert commands.handle(text, ctx()) is None


def test_surrounding_whitespace_tolerated(env):
    assert commands.handle("  !jobs  ", ctx()) == commands.fmt_jobs(str)


# ── block / unblock ─────────────────────────────────────────────────────────
def test_block_and_unblock(env):
    assert "C9" in commands.handle("!block C9", ctx())
    assert blocked.is_blocked("C9") is True
    assert "C9" in commands.handle("!unblock C9", ctx())
    assert blocked.is_blocked("C9") is False


def test_korean_aliases(env):
    commands.handle("!차단 C9", ctx())
    assert blocked.is_blocked("C9") is True
    commands.handle("!차단해제 C9", ctx())
    assert blocked.is_blocked("C9") is False


# ── sessions (!new) ─────────────────────────────────────────────────────────
def test_new_drops_the_session(env):
    sessions.remember(DM_KEY, "sess-abc")
    assert commands.handle("!new", ctx()) == config.t("session_reset")
    assert sessions.get(DM_KEY) is None


def test_new_when_already_fresh(env):
    assert commands.handle("!new", ctx()) == config.t("session_reset_none")


def test_new_only_touches_this_conversation(env):
    sessions.remember(DM_KEY, "a")
    sessions.remember("thread:C1:9", "b")
    commands.handle("!new", ctx(session_key=DM_KEY))
    assert sessions.get("thread:C1:9") == "b"


def test_new_korean_aliases(env):
    for alias in ("!새대화", "!리셋"):
        sessions.remember(DM_KEY, "sess")
        assert commands.handle(alias, ctx()) == config.t("session_reset")


# ── listen zones ────────────────────────────────────────────────────────────
def test_listen_registers_channel_then_thread(env):
    assert commands.handle("!listen", ctx(channel=CHAN, is_dm=False))
    assert autolisten.is_zone(CHAN, None) is True
    assert commands.handle("!unlisten", ctx(channel=CHAN, is_dm=False))
    assert autolisten.is_zone(CHAN, None) is False


def test_listen_inside_a_thread_scopes_to_it(env):
    commands.handle("!listen", ctx(channel=CHAN, thread="9.9", is_dm=False))
    assert autolisten.is_zone(CHAN, "9.9") is True
    assert autolisten.is_zone(CHAN, None) is False


def test_listening_lists_zones(env):
    commands.handle("!listen", ctx(channel=CHAN, is_dm=False))
    assert CHAN in commands.handle("!listening", ctx())


# ── jobs / cancel / stop ────────────────────────────────────────────────────
def test_cancel_unknown_job(env):
    assert "j9999" in commands.handle("!cancel j9999", ctx())


def test_bare_cancel_is_stop_not_job_cancel(env):
    """`!cancel j1` cancels one job; bare `!cancel` stops everything."""
    assert commands.handle("!cancel", ctx()) == config.t("nothing_running")


def test_stop_words(env):
    for word in ("!stop", "중지", "!중지"):
        assert commands.handle(word, ctx()) == config.t("nothing_running")


def test_jobs_empty(env):
    assert commands.handle("!jobs", ctx()) == config.t("jobs_none")


# ── usage ───────────────────────────────────────────────────────────────────
def test_usage_empty(env):
    assert commands.handle("!usage", ctx()) == config.t("usage_empty")


def test_usage_accepts_a_day_count(env):
    assert commands.handle("!usage 30", ctx()) == config.t("usage_empty")


# ── schedules ───────────────────────────────────────────────────────────────
def test_schedule_list_empty(env):
    assert commands.handle("!schedule list", ctx()) == config.t("sched_empty")


def test_schedule_add_then_list_then_remove(env):
    added = commands.handle("!schedule daily 09:00 stand-up", ctx())
    assert "s1" in added
    assert "stand-up" in commands.handle("!schedule list", ctx())
    assert commands.handle("!schedule remove s1", ctx()) == \
        config.t("sched_removed", id="s1")


def test_schedule_garbage_shows_help(env):
    assert commands.handle("!schedule wat", ctx()) == config.t("sched_help")


# ── learn ───────────────────────────────────────────────────────────────────
def test_learn_captures(env):
    assert "1" in commands.handle("!learn prefer tabs", ctx())


# ── orgs ────────────────────────────────────────────────────────────────────
def test_org_create_list_info(env):
    assert "acme" in commands.handle("!org create acme", ctx())
    assert "acme" in commands.handle("!org list", ctx())
    assert "acme" in commands.handle("!org info acme", ctx())


def test_org_unknown_name(env):
    assert commands.handle("!org info ghost", ctx()) == \
        config.t("org_not_found", name="ghost")


def test_org_bare_shows_help(env):
    assert commands.handle("!org", ctx()) == config.t("org_help")


def test_org_add_member_from_mentions(env):
    """Ids come from the adapter — mention markup differs per platform."""
    commands.handle("!org create acme", ctx())
    out = commands.handle("!org add acme", ctx(user_ids=[USER]))
    assert out == config.t("org_added", n=1, org="acme")
    assert USER in orgs.get("acme")["members"]


def test_org_add_member_from_a_bare_id(env):
    commands.handle("!org create acme", ctx())
    commands.handle(f"!org add acme {USER}", ctx())
    assert USER in orgs.get("acme")["members"]


def test_org_accepts_a_non_slack_id_shape(env):
    """Core must not assume Slack's id format, or Discord members all bounce."""
    commands.handle("!org create acme", ctx())
    commands.handle("!org add acme", ctx(user_ids=[DISCORD_USER]))
    assert DISCORD_USER in orgs.get("acme")["members"]


def test_org_still_rejects_prose(env):
    commands.handle("!org create acme", ctx())
    commands.handle("!org add acme", ctx(user_ids=["nope!", "x"]))
    assert orgs.get("acme")["members"] == []


def test_org_add_needs_someone(env):
    commands.handle("!org create acme", ctx())
    assert commands.handle("!org add acme", ctx()) == \
        config.t("org_add_none", name="acme")


def test_org_bind_uses_the_current_channel(env):
    commands.handle("!org create acme", ctx())
    commands.handle("!org bind acme", ctx(channel=CHAN, is_dm=False))
    assert CHAN in orgs.get("acme")["channels"]


def test_org_bind_from_a_dm_needs_an_explicit_id(env):
    """A DM is nobody's shared channel — binding it would be meaningless."""
    commands.handle("!org create acme", ctx())
    assert commands.handle("!org bind acme", ctx(channel="D012ABCDEF", is_dm=True)) == \
        config.t("org_bind_need_id", name="acme")
    commands.handle(f"!org bind acme {CHAN}", ctx(channel="D012ABCDEF", is_dm=True))
    assert CHAN in orgs.get("acme")["channels"]


def test_org_allow_and_deny_a_command(env):
    commands.handle("!org create acme", ctx())
    commands.handle("!org allow acme deploy", ctx())
    assert "deploy" in orgs.get("acme")["commands"]
    commands.handle("!org deny acme deploy", ctx())
    assert "deploy" not in orgs.get("acme")["commands"]


def test_org_allow_strips_a_leading_bang(env):
    commands.handle("!org create acme", ctx())
    commands.handle("!org allow acme !deploy", ctx())
    assert "deploy" in orgs.get("acme")["commands"]
