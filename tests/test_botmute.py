"""`!exit` — end a bot-to-bot exchange here, and nothing else.

Two Lokis talking is the one path where nobody decides when to stop. Every
existing off-switch is the wrong size: `!stop` only cancels what runs, `!block`
shuts out people too, `!unlisten` sends humans back to @mentioning, and `!new`
just clears memory. This is the missing one.
"""
import pytest

from loki.core import botmute, commands, config, jobs
from tests.conftest import OWNER, event


@pytest.fixture
def muted(tmp_path, monkeypatch):
    monkeypatch.setattr(botmute, "_FILE", tmp_path / "bot_muted.json")
    monkeypatch.setattr(botmute, "_state", {"channels": set(), "threads": set()})
    return tmp_path


def _ctx(channel="C0SHARED", thread=None):
    return {"channel": channel, "thread": thread, "is_owner": True,
            "name_of": lambda u: u}


# ── scoping ──────────────────────────────────────────────────────────────────
def test_mutes_the_thread_it_was_typed_in(muted):
    commands.exit_cmd("", _ctx(thread="111.1"))
    assert botmute.is_muted("C0SHARED", "111.1")
    assert not botmute.is_muted("C0SHARED", "222.2")     # sibling thread is fine
    assert not botmute.is_muted("C0SHARED", None)


def test_at_channel_level_it_covers_the_channel(muted):
    commands.exit_cmd("", _ctx())
    assert botmute.is_muted("C0SHARED", None)
    assert botmute.is_muted("C0SHARED", "any.thread")    # threads included


def test_undo(muted):
    commands.exit_cmd("", _ctx(thread="111.1"))
    commands.exit_cmd("undo", _ctx(thread="111.1"))
    assert not botmute.is_muted("C0SHARED", "111.1")


def test_korean_spelling_and_undo(muted):
    commands.handle("!종료", _ctx(thread="111.1"))
    assert botmute.is_muted("C0SHARED", "111.1")
    commands.handle("!종료 해제", _ctx(thread="111.1"))
    assert not botmute.is_muted("C0SHARED", "111.1")


def test_survives_a_restart(muted):
    commands.exit_cmd("", _ctx(thread="111.1"))
    assert "C0SHARED:111.1" in botmute._load()["threads"]   # re-read from disk


def test_junk_argument_gets_help_and_changes_nothing(muted):
    reply = commands.exit_cmd("maybe", _ctx(thread="111.1"))
    assert not botmute.is_muted("C0SHARED", "111.1")
    assert "!exit" in reply


def test_list_shows_the_otherwise_invisible_state(muted):
    assert "!exit" not in commands.exit_cmd("list", _ctx())   # empty → plain msg
    commands.exit_cmd("", _ctx(thread="111.1"))
    assert "111.1" in commands.exit_cmd("list", _ctx())


# ── it cancels bot work, and only bot work ───────────────────────────────────
def test_running_bot_work_here_is_cancelled(muted, monkeypatch):
    snap = [
        {"id": "j1", "kind": "bot", "channel": "C0SHARED", "thread": "111.1"},
        {"id": "j2", "kind": "bot", "channel": "C0SHARED", "thread": "999.9"},
        {"id": "j3", "kind": "owner", "channel": "C0SHARED", "thread": "111.1"},
        {"id": "j4", "kind": "bot", "channel": "C0OTHER", "thread": "111.1"},
    ]
    killed = []
    monkeypatch.setattr(jobs, "snapshot", lambda: snap)
    monkeypatch.setattr(jobs, "cancel", lambda i: (killed.append(i), "killed")[1])
    commands.exit_cmd("", _ctx(thread="111.1"))
    # only the bot job in THIS thread — the owner's own work survives `!exit`
    assert killed == ["j1"]


# ── through the real dispatch ────────────────────────────────────────────────
def test_a_bot_is_ignored_after_exit_but_people_are_not(adapter, muted,
                                                        monkeypatch):
    monkeypatch.setattr(adapter.autolisten, "is_zone", lambda c, t: True)
    monkeypatch.setattr(adapter.botallow, "is_allowed", lambda ev, ids: True)

    def bot_msg():
        ev = event(text="계속 얘기하자", channel="C0SHARED", user=None)
        ev.update({"bot_id": "B0WIFE123", "thread_ts": "111.1"})
        return ev

    # Same entry point before and after, or the comparison proves nothing about
    # the mute — only that two different code paths behave differently.
    adapter.on_message({}, bot_msg(), None)
    assert len(adapter.submitted) == 1, "the other Loki should wake this one"

    commands.exit_cmd("", _ctx(thread="111.1"))

    # after: silence for bots…
    adapter.on_message({}, bot_msg(), None)
    assert len(adapter.submitted) == 1, "bot was still heard after !exit"

    # …and people in the same thread are untouched
    human = event(text="이건 사람이 쓴 거야", channel="C0SHARED", user=OWNER,
                  thread_ts="111.1")
    adapter._dispatch({"event_id": "m2"}, human, is_mention=False)
    assert len(adapter.submitted) == 2, "!exit must not silence people"
