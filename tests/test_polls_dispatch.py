"""Polls through the adapter — who can ask, what gets posted, clicks, closing.

The model is a stub that answers with canned JSON; the Slack client is the
conftest fake. `_spawn` runs the model reading inline so nothing is racy."""
import json

import pytest

from conftest import OWNER, event
from loki.core import poll as P

THREAD_TALK = ("[Ann] 후보: 풍류 더 블랙 <https://map.example/pung|지도>, 업투유\n"
               "[Bo] 날짜는 10/15 아니면 10/16")
GOOD = {"title": "회식", "questions": [
    {"title": "장소", "multi": True, "options": [
        {"text": "풍류 더 블랙", "url": "https://map.example/pung"}, {"text": "업투유", "url": ""}]},
    {"title": "날짜", "multi": True, "options": [{"text": "10/15"}, {"text": "10/16"}]}],
    "question": ""}


@pytest.fixture
def pl(adapter, monkeypatch):
    """The adapter with polls wired to a stub model and no real scope files."""
    polls = adapter.polls
    monkeypatch.setattr(polls.config, "LANG", "en")      # labels and messages are asserted in English
    monkeypatch.setattr(polls, "_spawn", lambda fn: fn())
    monkeypatch.setattr(polls.guard, "settings_file", lambda: "guard.json")
    monkeypatch.setattr(polls.scope, "write_scope_settings", lambda org: ("scope.json", "m"))
    monkeypatch.setattr(polls.scope, "read_roots", lambda manifest: [])
    monkeypatch.setattr(adapter, "_thread_context", lambda ch, ts: THREAD_TALK)
    monkeypatch.setattr(adapter, "_channel_context", lambda ch: "[Ann] 후보: A, B")
    adapter.asked = []
    adapter.reply = {"text": json.dumps(GOOD), "error": None, "reason": "ok", "session_id": None}

    def fake(prompt, resume_id, mode=None, **kw):
        adapter.asked.append({"prompt": prompt, "mode": mode, **kw})
        return adapter.reply
    monkeypatch.setattr(polls.brain, "run_claude", fake)
    return adapter


def say(mod, text, user=OWNER, channel="C0CH", ts="2.2", **extra):
    mod._dispatch({"event_id": f"e{ts}{user}"}, event(text=text, user=user, channel=channel,
                                                     ts=ts, **extra), is_mention=True)


def posts(mod):
    return mod.app.client.of("chat_postMessage")


def saved(mod):
    return P.find_latest(mod.polls._DIR, "C0CH")


# ── asking for one ───────────────────────────────────────────────────────────
def test_owner_in_a_thread_gets_a_poll_in_that_thread(pl):
    say(pl, "지금 있는 리스트들 투표로 만들어줘", thread_ts="1.1")
    (post,) = posts(pl)
    assert post["thread_ts"] == "1.1" and post["channel"] == "C0CH"
    assert "회식" in post["text"]
    assert len([b for b in post["blocks"] if b.get("accessory")]) == 4
    assert pl.submitted == []                           # never reached the ordinary brain
    assert saved(pl)["created_by"] == OWNER and saved(pl)["thread_ts"] == "1.1"


def test_the_model_reads_the_thread_as_data_with_no_tools(pl):
    say(pl, "투표로 만들어줘", thread_ts="1.1")
    ask = pl.asked[0]
    assert THREAD_TALK in ask["prompt"] and "투표로 만들어줘" in ask["prompt"]
    assert ask["mode"] == "plan" and ask["settings_file"] == "guard.json"


def test_outside_a_thread_it_reads_the_channel_and_posts_top_level(pl):
    say(pl, "후보들 투표 올려줘")
    assert "[Ann] 후보: A, B" in pl.asked[0]["prompt"]
    assert posts(pl)[0]["thread_ts"] is None


def test_anyone_can_ask_and_runs_under_the_guest_scope(pl):
    say(pl, "투표로 만들어줘", user="UGUEST", thread_ts="1.1")
    assert saved(pl)["created_by"] == "UGUEST"
    assert pl.asked[0]["settings_file"] == "scope.json"
    assert pl.asked[0]["cwd"] is not None


def test_a_guest_over_the_budget_gets_the_refusal_not_a_poll(pl, monkeypatch):
    monkeypatch.setattr(pl.polls.budget, "check_guest",
                        lambda org=None: (False, "rate_limited", {"n": 1, "m": 5}))
    say(pl, "투표로 만들어줘", user="UGUEST", thread_ts="1.1")
    assert pl.asked == [] and saved(pl) is None
    assert len(posts(pl)) == 1 and "text" in posts(pl)[0] and "blocks" not in posts(pl)[0]


def test_a_question_about_polls_is_left_to_the_ordinary_path(pl):
    pl._dispatch({"event_id": "q1"}, event(text="투표 만들 수 있어?"), is_mention=False)
    assert pl.asked == [] and len(pl.submitted) == 1


def test_a_bot_cannot_ask_for_a_poll(pl):
    assert pl.polls.try_handle({"from_bot": True, "text": "투표로 만들어줘", "is_owner": False}) is False


def test_when_the_model_needs_more_it_asks_in_the_thread(pl):
    pl.reply = {"text": json.dumps({"questions": [], "question": "어떤 후보를 쓸까요?"}),
                "error": None, "reason": "ok"}
    say(pl, "투표로 만들어줘", thread_ts="1.1")
    assert saved(pl) is None
    assert posts(pl)[0]["text"].startswith("🤔 어떤 후보를")


@pytest.mark.parametrize("text", ["not json at all", json.dumps({"questions": [{"options": ["only"]}]})])
def test_nothing_to_vote_on_says_so(pl, text):
    pl.reply = {"text": text, "error": None, "reason": "ok"}
    say(pl, "투표로 만들어줘", thread_ts="1.1")
    assert saved(pl) is None and "blocks" not in posts(pl)[0]


def test_a_failed_reading_posts_a_notice_not_a_poll(pl):
    pl.reply = {"text": "", "error": True, "reason": "error"}
    say(pl, "투표로 만들어줘", thread_ts="1.1")
    assert saved(pl) is None and len(posts(pl)) == 1


def test_a_hostile_reply_cannot_ping_the_channel(pl):
    pl.reply = {"text": json.dumps({"title": "x", "questions": [
        {"title": "<!channel>", "options": ["<!here>", "ok"]}]}), "error": None, "reason": "ok"}
    say(pl, "투표로 만들어줘", thread_ts="1.1")
    body = json.dumps(posts(pl)[0]["blocks"])
    assert "<!channel>" not in body and "<!here>" not in body


# ── voting ───────────────────────────────────────────────────────────────────
def click(mod, user, value, ts="1700000000.000100"):
    acks = []
    mod.polls._on_vote(lambda: acks.append(1),
                       {"channel": {"id": "C0CH"}, "message": {"ts": ts},
                        "user": {"id": user}, "actions": [{"value": value}]},
                       mod.app.client)
    assert acks == [1]


@pytest.fixture
def live(pl):
    say(pl, "투표로 만들어줘", thread_ts="1.1")
    pl.app.client.reset()
    return pl


def test_a_click_records_the_vote_and_rerenders_for_everyone(live):
    click(live, "U0VOTER1", "0:1")
    (upd,) = live.app.client.of("chat_update")
    assert upd["ts"] == "1700000000.000100"
    assert "<@U0VOTER1>" in json.dumps(upd["blocks"], ensure_ascii=False)
    assert saved(live)["votes"] == {"0:1": ["U0VOTER1"]}


def test_a_second_click_takes_it_back(live):
    click(live, "U0VOTER1", "0:1")
    click(live, "U0VOTER1", "0:1")
    assert saved(live)["votes"] == {}


def test_a_click_on_an_unknown_poll_is_ignored(live):
    click(live, "U0VOTER1", "0:1", ts="9.9")
    assert live.app.client.of("chat_update") == []


# ── tally, close, remove ─────────────────────────────────────────────────────
def test_anyone_in_the_thread_can_ask_for_the_tally(live):
    click(live, "U0VOTER1", "0:1")
    live.app.client.reset()
    say(live, "투표 결과 알려줘", user="UGUEST", ts="3.3", thread_ts="1.1")
    text = posts(live)[0]["text"]
    assert "2. 업투유 — 1 vote 🏆" in text
    assert "<@" not in text


def test_the_creator_closes_it(live):
    click(live, "U0VOTER1", "0:1")
    live.app.client.reset()
    say(live, "투표 마감해줘", ts="3.3", thread_ts="1.1")
    (upd,) = live.app.client.of("chat_update")
    assert not [b for b in upd["blocks"] if b.get("accessory")]
    assert saved(live)["closed"] is True and len(posts(live)) == 1
    click(live, "U0VOTER2", "0:0")                       # too late
    assert saved(live)["votes"] == {"0:1": ["U0VOTER1"]}


def test_someone_else_cannot_close_it(live):
    say(live, "투표 마감해줘", user="UGUEST", ts="3.3", thread_ts="1.1")
    assert saved(live)["closed"] is False
    assert live.app.client.of("chat_update") == []
    assert len(posts(live)) == 1


def test_the_owner_can_close_anyones(live):
    live.polls.P.save(live.polls._DIR, {**saved(live), "created_by": "UGUEST"})
    say(live, "투표 마감해줘", ts="3.3", thread_ts="1.1")
    assert saved(live)["closed"] is True


def test_cancel_takes_the_message_down(live):
    say(live, "투표 취소해줘", ts="3.3", thread_ts="1.1")
    assert live.app.client.of("chat_delete")[0]["ts"] == "1700000000.000100"
    assert saved(live) is None


def test_no_poll_here_means_the_ordinary_path(pl):
    pl._dispatch({"event_id": "n1"}, event(text="투표 결과 알려줘", channel="C0NONE"), is_mention=True)
    assert len(pl.submitted) == 1
