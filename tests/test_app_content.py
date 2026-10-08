"""What an app posts — a poll's counts, a forwarded message — must reach the
brain as people see it, not just the message's `text`.

2026-10-01, a team channel: a guest asked `@Loki 투표 결과 알려줘` about two
Open Poll+ polls, three ways, and got "there are no tallies here" every time.
The prompts Loki sent (the Codex rollouts of those runs) held no vote count:

- the bare mention's channel context had no poll in it. Their `text` is not
  empty, so the subtype skip is what dropped them: every subtype was treated
  like a join or a topic change, an app's own post included;
- kept, they would still have said only `Poll : <question>` — Open Poll+ shows
  each option and its "N표" in blocks (polppol/openpollslack-i18n, index.js);
- the copies forwarded into the thread arrived as attachments, which the
  thread context never read, and a sealed run cannot open the links itself.

The polls below have the real ones' size — option count, label length, votes
per option — with the names replaced, laid out the way Open Poll+ builds them:
a section per option, then a context block with the voters' mentions and the
count. FORWARD is Slack's shape for a shared message, not captured live.

Checked live on 2026-10-02 through the owner's `!summary`: both real polls
reached the channel context with every option and count. The live messages
carry no bot name — no `bot_profile`, no `username` — so they are labelled by
bot id, and the poll fixture has none either.
"""
from tests.conftest import event


def _poll(question: str, options: list[tuple[str, int]]) -> dict:
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": question}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": ":writing_hand: <@U0CREATOR1>이(가) 만듦"}]},
        {"type": "divider"},
    ]
    for i, (label, votes) in enumerate(options):
        voters = " ".join(f"<@U0VOTER{n:04d}>" for n in range(votes))
        blocks += [
            {"type": "section", "text": {"type": "mrkdwn", "text": label},
             "accessory": {"type": "button", "action_id": "btn_vote",
                           "text": {"type": "plain_text", "text": "투표하기"},
                           "value": f'{{"id": "{i}"}}'}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"{voters} {votes}표"}]},
        ]
    return {"type": "message", "subtype": "bot_message", "bot_id": "B0POLLAPP1",
            "ts": "1790830413.394909", "text": f"Poll : {question}",
            "blocks": blocks}


DATES = _poll("회식 날짜 투표(중복가능)", [
    (":one: 10/19(월)", 1), (":two: 10/20(화)", 1), (":three: 10/21(수)", 2),
    (":four: 10/22(목)", 13), (":five: 10/23((금)", 11), (":six: 10/28(수)", 2),
    (":seven: 10/29(목)", 7), (":eight: 10/30(금)", 7), (":nine: 10/26(월)", 1)])

PLACES = _poll("회식 장소 투표", [
    (":one: 첫째고깃집 역앞지점   (소+돼지)", 12),
    (":two: 둘째식당 본점  (돼지 + 이것저것 파는곳)", 2),
    (":three: 셋째 돼지  (돼지)", 3), (":four: 넷째고깃집 직영지점  (돼지)", 4),
    (":five: 다섯째횟집", 10), (":six: 여섯째집 (한식 + 술집)", 4),
    (":seven: 술 마실분 :raised_hands:", 5),
    (":eight: 술 *안* 마실분:raised_hands:", 4)])

FORWARD = {
    "is_share": True, "is_msg_unfurl": True,
    "author_name": "Open Poll+", "channel_id": "C1", "ts": DATES["ts"],
    "text": DATES["text"],
    "fallback": f"[2026년 10월 1일 오후 1:53] Open Poll+: {DATES['text']}",
    "message_blocks": [{"team": "T1", "channel": "C1", "ts": DATES["ts"],
                        "message": {"blocks": DATES["blocks"]}}],
}


def _rich(text: str) -> list[dict]:
    """What Slack's composer stores beside `text` for a typed message."""
    return [{"type": "rich_text", "elements": [
        {"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}]}]


def _history(adapter, monkeypatch, newest_first: list[dict]) -> None:
    monkeypatch.setattr(adapter.app.client, "conversations_history",
                        lambda **kw: {"ok": True, "messages": newest_first},
                        raising=False)


def _replies(adapter, monkeypatch, oldest_first: list[dict]) -> None:
    monkeypatch.setattr(adapter.app.client, "conversations_replies",
                        lambda **kw: {"ok": True, "messages": oldest_first},
                        raising=False)


# ── the three asks from that thread ──────────────────────────────────────────
def test_a_bare_mention_sees_both_polls_whole(adapter, monkeypatch):
    """The first ask. Each poll's last option must survive the per-message cap."""
    ask = event(text="<@UBOT> 투표 결과 알려줘", channel="C1", ts="1790858585.0")
    _history(adapter, monkeypatch, [ask, DATES, PLACES])
    ctx = adapter._channel_context("C1")
    for shown in (":four: 10/22(목)", "13표", ":nine: 10/26(월)",
                  ":one: 첫째고깃집", "12표", ":eight: 술 *안* 마실분"):
        assert shown in ctx, shown


def test_asking_under_the_poll_sees_the_counts(adapter, monkeypatch):
    ask = event(text="<@UBOT> 결과 알려줘", channel="C1", ts="1790858585.0",
                thread_ts=DATES["ts"])
    _replies(adapter, monkeypatch, [DATES, ask])
    ctx = adapter._thread_context("C1", DATES["ts"])
    assert ":four: 10/22(목)" in ctx and "13표" in ctx and "11표" in ctx


def test_a_forwarded_poll_is_read_in_the_thread(adapter, monkeypatch):
    """The third ask: the poll pasted into the thread as a forward."""
    first = event(text="<@UBOT> 투표 결과 2종 알려줘", channel="C1", ts="1.0")
    fwd = event(text="<https://x.slack.com/archives/C1/p1790830413394909|link>",
                channel="C1", ts="2.0", thread_ts="1.0", attachments=[FORWARD])
    _replies(adapter, monkeypatch, [first, fwd])
    ctx = adapter._thread_context("C1", "1.0")
    assert "↪ Open Poll+:" in ctx
    assert ":four: 10/22(목)" in ctx and "13표" in ctx


# ── what must not change ─────────────────────────────────────────────────────
def test_voter_mentions_are_stripped_like_any_other(adapter, monkeypatch):
    _history(adapter, monkeypatch, [DATES])
    assert "<@" not in adapter._channel_context("C1")


def test_joins_and_topic_changes_are_still_skipped(adapter, monkeypatch):
    _history(adapter, monkeypatch, [
        event(text="<@U9> 님이 채널에 참여함", subtype="channel_join", channel="C1"),
        event(text="주제 설정: 10월 회식", subtype="channel_topic", channel="C1"),
        event(text="안녕하세요", channel="C1", ts="3.0")])
    ctx = adapter._channel_context("C1")
    assert "안녕하세요" in ctx
    assert "참여함" not in ctx and "주제 설정" not in ctx


def test_a_persons_link_preview_stays_out(adapter, monkeypatch):
    """Previews of the pages a person links are the pages, not the talk."""
    msg = event(text="1. <https://example.com/place/1|첫째고깃집>", channel="C1",
                ts="2.0", attachments=[{"title": "첫째고깃집 : 지도",
                                         "text": "영업 중 · 리뷰 999",
                                         "fallback": "첫째고깃집 : 지도"}])
    _history(adapter, monkeypatch, [msg])
    ctx = adapter._channel_context("C1")
    assert "첫째고깃집" in ctx and "영업 중" not in ctx


def test_a_typed_message_is_read_once(adapter, monkeypatch):
    """A person's `rich_text` mirrors `text`; reading both would double it."""
    _history(adapter, monkeypatch,
             [event(text="안녕하세요", channel="C1", ts="2.0", blocks=_rich("안녕하세요"))])
    assert adapter._channel_context("C1").count("안녕하세요") == 1


def test_a_bot_that_writes_only_text_still_reads(adapter, monkeypatch):
    msg = {"type": "message", "bot_id": "B0CI", "bot_profile": {"name": "CI"},
           "ts": "2.0", "text": "build #12 passed"}
    _history(adapter, monkeypatch, [msg])
    assert "build #12 passed" in adapter._channel_context("C1")
