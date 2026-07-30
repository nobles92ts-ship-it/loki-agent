"""Scheduling to a channel — command, firing, and delivery fallback."""
import pytest

from conftest import OWNER, event


@pytest.fixture
def sched(adapter, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.scheduler, "SCHED_FILE", tmp_path / "sched.json")
    return adapter


# ── the command ──────────────────────────────────────────────────────────────
def test_schedule_to_channel_warns_about_scope(sched):
    sched._dispatch({"event_id": "sc1"},
                    event(text="!schedule daily 09:00 <#C0TEAM|team> standup"),
                    is_mention=False)
    reply = sched.app.client.texts()[0]
    assert "C0TEAM" in reply and "⚠️" in reply
    assert sched.scheduler.list_all()[0]["post_to"] == "C0TEAM"


def test_schedule_without_channel_has_no_warning(sched):
    sched._dispatch({"event_id": "sc2"},
                    event(text="!schedule daily 09:00 standup"), is_mention=False)
    assert "⚠️" not in sched.app.client.texts()[0]
    assert "post_to" not in sched.scheduler.list_all()[0]


def test_schedule_list_shows_the_destination(sched):
    sched._dispatch({"event_id": "sc3"},
                    event(text="!schedule daily 09:00 <#C0TEAM|team> standup"),
                    is_mention=False)
    sched._dispatch({"event_id": "sc4"}, event(text="!schedule list"),
                    is_mention=False)
    assert "<#C0TEAM>" in sched.app.client.texts()[-1]


def test_schedule_help_when_unparseable(sched):
    sched._dispatch({"event_id": "sc5"}, event(text="!schedule sometime soon"),
                    is_mention=False)
    assert "!schedule" in sched.app.client.texts()[0]
    assert sched.scheduler.list_all() == []


def test_guest_cannot_schedule(sched):
    sched._dispatch({"event_id": "sc6"},
                    event(text="!schedule daily 09:00 <#C0TEAM|team> leak",
                          user="UGUEST", channel="C0PUB"),
                    is_mention=True)
    assert sched.scheduler.list_all() == []


# ── firing ───────────────────────────────────────────────────────────────────
def test_fire_targets_the_channel_with_dm_fallback(sched):
    sched._fire_schedule({"id": "s1", "channel": "D0OWNER", "post_to": "C0TEAM",
                          "type": "daily", "time": "09:00", "prompt": "go"})
    job = sched.submitted[0]
    assert job["channel"] == "C0TEAM"
    assert job["fallback_channel"] == "D0OWNER"
    assert job["user"] == OWNER and job["thread"] is None


def test_fire_without_destination_stays_in_the_dm(sched):
    sched._fire_schedule({"id": "s2", "channel": "D0OWNER",
                          "type": "daily", "time": "09:00", "prompt": "go"})
    job = sched.submitted[0]
    assert job["channel"] == "D0OWNER" and job["fallback_channel"] is None


# ── delivery ─────────────────────────────────────────────────────────────────
def _ok_result(*a, **kw):
    return {"text": "the result", "session_id": None, "error": False,
            "reason": "ok"}


def test_result_reaches_the_target_channel(sched, monkeypatch):
    monkeypatch.setattr(sched.brain, "run_claude", _ok_result)
    sched._handle({"channel": "C0TEAM", "thread": None, "text": "go",
                   "user": OWNER, "event_id": "f1", "permission_mode": "plan",
                   "kind": "scheduled", "fallback_channel": "D0OWNER"})
    posts = sched.app.client.of("chat_postMessage")
    assert [p["channel"] for p in posts] == ["C0TEAM"]
    assert "the result" in posts[0]["text"]


def test_unreachable_channel_falls_back_to_the_owner_dm(sched, monkeypatch):
    monkeypatch.setattr(sched.brain, "run_claude", _ok_result)
    sched.app.client.fail_channels = {"C0TEAM"}      # e.g. not_in_channel
    sched._handle({"channel": "C0TEAM", "thread": None, "text": "go",
                   "user": OWNER, "event_id": "f2", "permission_mode": "plan",
                   "kind": "scheduled", "fallback_channel": "D0OWNER"})
    delivered = [p for p in sched.app.client.of("chat_postMessage")
                 if p["channel"] == "D0OWNER"]
    assert delivered, "the run must not be silently lost"
    assert "the result" in delivered[0]["text"]
    assert "C0TEAM" in delivered[0]["text"]          # says where it failed


def test_ordinary_job_has_no_fallback(sched, monkeypatch):
    monkeypatch.setattr(sched.brain, "run_claude", _ok_result)
    sched.app.client.fail_channels = {"C0PUB"}
    sched._handle({"channel": "C0PUB", "thread": "1.1", "text": "go",
                   "user": OWNER, "event_id": "f3", "permission_mode": "plan",
                   "kind": "owner"})
    assert [p for p in sched.app.client.of("chat_postMessage")
            if p["channel"] == "D0OWNER"] == []
