"""Poll model — what counts as a request, what a model's reply may become, voting,
rendering. Pure logic: no Slack, no network."""
import pytest

from loki.core import poll as P

VOTER = "U0VOTER1"
OTHER = "U0VOTER2"


def opts(*names):
    return [{"text": n, "url": ""} for n in names]


def make(multi=True, closed=False):
    qs = [{"title": "Where", "multi": multi, "options": opts("A", "B", "C")},
          {"title": "When", "multi": True, "options": opts("Thu", "Fri")}]
    p = P.new("C1", "Dinner", qs, "U0MAKER", thread_ts="1.1", now=1000.0)
    return {**p, "closed": closed}


# ── classify ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "지금 있는 리스트들 투표로 만들어줘",
    "이걸로 투표 해줘",
    "회식 투표 올려줘",
    "투표 하자",
    "make a poll from these",
    "create a poll",
])
def test_classify_create(text):
    assert P.classify(text) == "create"


@pytest.mark.parametrize("text", ["투표 결과 알려줘", "투표 현황", "poll results", "몇 표야? 투표"])
def test_classify_results(text):
    assert P.classify(text) == "results"


@pytest.mark.parametrize("text", ["투표 마감해줘", "이 투표 닫아줘", "close the poll"])
def test_classify_close(text):
    assert P.classify(text) == "close"


@pytest.mark.parametrize("text", ["투표 취소해줘", "투표 지워줘", "delete the poll"])
def test_classify_cancel(text):
    assert P.classify(text) == "cancel"


@pytest.mark.parametrize("text", [
    "지금 있는 리스트들 투표로 만들어줘 라고 하면 만들 수 있는거지?",   # a question about it
    "투표 어떻게 만들어?",
    "투표 마감은 언제야",
    "투표가 마감됐네",                                                 # a statement
    "can you make a poll?",
    "!poll something",                                                  # commands are not ours
    "점심 뭐 먹지",                                                     # not about polls
    "",
])
def test_classify_leaves_everything_else_alone(text):
    assert P.classify(text) is None


# ── a model's reply ──────────────────────────────────────────────────────────
def test_parse_reply_plain_fenced_and_noisy():
    assert P.parse_reply('{"a": 1}') == {"a": 1}
    assert P.parse_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert P.parse_reply('Sure! {"a": 1} hope that helps') == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", '{"a": '])
def test_parse_reply_rejects_what_is_not_an_object(text):
    assert P.parse_reply(text) is None


def test_clean_questions_keeps_good_ones():
    got = P.clean_questions([{"title": " Where  to? ", "multi": False, "options": [
        {"text": "A", "url": "https://a.example/x?y=1"}, "B"]}])
    assert got == [{"title": "Where to?", "multi": False, "options": [
        {"text": "A", "url": "https://a.example/x?y=1"}, {"text": "B", "url": ""}]}]


def test_clean_questions_multi_defaults_on():
    got = P.clean_questions([{"options": ["A", "B"]}])
    assert got[0]["multi"] is True


def test_clean_questions_needs_two_distinct_options():
    assert P.clean_questions([{"options": ["A"]}]) == []
    assert P.clean_questions([{"options": ["A", "a", " A "]}]) == []


def test_clean_questions_caps():
    many = [{"options": [f"o{i}" for i in range(15)]} for _ in range(8)]
    got = P.clean_questions(many)
    assert len(got) == P.MAX_QUESTIONS
    assert all(len(q["options"]) == P.MAX_OPTIONS for q in got)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.example/a", "https://x.example/a|b",
                                 "https://x.example/a>b", "https://x.example/a b", "x.example"])
def test_clean_questions_drops_unsafe_urls(url):
    got = P.clean_questions([{"options": [{"text": "A", "url": url}, "B"]}])
    assert got[0]["options"][0]["url"] == ""


@pytest.mark.parametrize("raw", [None, "x", 3, {"a": 1}, [None, "x"], [{"options": "AB"}]])
def test_clean_questions_survives_garbage(raw):
    assert P.clean_questions(raw) == []


# ── voting ───────────────────────────────────────────────────────────────────
def test_vote_toggles_in_a_multi_question():
    p = make()
    p1 = P.vote(P.vote(p, "0:0", VOTER), "0:1", VOTER)
    assert p1["votes"] == {"0:0": [VOTER], "0:1": [VOTER]}
    p2 = P.vote(p1, "0:0", VOTER)
    assert p2["votes"] == {"0:1": [VOTER]}


def test_vote_in_a_pick_one_question_moves_the_vote():
    p = P.vote(P.vote(make(multi=False), "0:0", VOTER), "0:2", VOTER)
    assert p["votes"] == {"0:2": [VOTER]}


def test_pick_one_is_per_question():
    p = P.vote(P.vote(make(multi=False), "0:0", VOTER), "1:0", VOTER)
    assert p["votes"] == {"0:0": [VOTER], "1:0": [VOTER]}


def test_vote_is_immutable():
    p = make()
    P.vote(p, "0:0", VOTER)
    assert p["votes"] == {}


@pytest.mark.parametrize("key", ["9:0", "0:9", "x", "", "0", "0:0:0", None, "-1:0"])
def test_vote_ignores_a_bad_key(key):
    p = make()
    assert P.vote(p, key, VOTER) is p


@pytest.mark.parametrize("user", ["", None, "someone", "<@U1>", "u0lower"])
def test_vote_ignores_a_bad_user(user):
    p = make()
    assert P.vote(p, "0:0", user) is p


def test_closed_poll_takes_no_votes():
    p = make(closed=True)
    assert P.vote(p, "0:0", VOTER) is p


def test_voter_count_is_distinct_people():
    p = P.vote(P.vote(P.vote(make(), "0:0", VOTER), "1:0", VOTER), "0:0", OTHER)
    assert P.voter_count(p) == 2


# ── rendering ────────────────────────────────────────────────────────────────
def buttons(blocks):
    return [b["accessory"] for b in blocks if b.get("accessory")]


def test_render_has_a_button_per_option():
    blocks = P.render_blocks(make())
    assert [b["value"] for b in buttons(blocks)] == ["0:0", "0:1", "0:2", "1:0", "1:1"]
    assert all(b["action_id"] == P.ACTION_ID for b in buttons(blocks))
    assert blocks[0]["type"] == "header" and "Dinner" in blocks[0]["text"]["text"]


def test_render_lists_voters_and_counts():
    p = P.vote(P.vote(make(), "0:1", VOTER), "0:1", OTHER)
    text = " ".join(b["text"]["text"] for b in P.render_blocks(p) if b["type"] == "section")
    assert f"<@{VOTER}> <@{OTHER}>" in text
    assert "*2 votes*" in text and "*0 votes*" in text


def test_render_closed_has_no_buttons():
    blocks = P.render_blocks(make(closed=True))
    assert buttons(blocks) == []
    assert any("Closed" in b["elements"][0]["text"] for b in blocks if b["type"] == "context")


def test_closed_poll_no_longer_invites_a_tap():
    open_footer = P.render_blocks(make())[-1]["elements"][0]["text"]
    closed_footer = P.render_blocks(make(closed=True))[-1]["elements"][0]["text"]
    assert "tap" in open_footer and "tap" not in closed_footer


def test_render_stays_inside_slacks_block_limit():
    qs = P.clean_questions([{"title": f"q{q}", "options": [f"o{i}" for i in range(10)]}
                            for q in range(5)])
    assert len(P.render_blocks(P.new("C1", "Big", qs, "U0MAKER"))) <= 100


def test_render_escapes_what_would_ping_the_channel():
    qs = P.clean_questions([{"title": "<!channel> &", "options": ["<!here>", "<@U0X> & co"]}])
    text = " ".join(b["text"]["text"] for b in P.render_blocks(P.new("C1", "t", qs, "U0M"))
                    if b["type"] == "section")
    assert "<!channel>" not in text and "<!here>" not in text and "<@U0X>" not in text
    assert "&lt;!here&gt;" in text and "&amp;" in text


def test_render_links_the_option_name():
    qs = P.clean_questions([{"options": [{"text": "Pung | Black", "url": "https://m.example/p?a=1&b=2"}, "B"]}])
    text = P.render_blocks(P.new("C1", "t", qs, "U0M"))[2]["text"]["text"]
    assert "<https://m.example/p?a=1&amp;b=2|Pung / Black>" in text


def test_render_caps_listed_voters():
    p = make()
    p = {**p, "votes": {"0:0": [f"U0V{i:03d}" for i in range(P.MAX_SHOWN_VOTERS + 5)]}}
    text = P.render_blocks(p)[2]["text"]["text"]
    assert text.count("<@") == P.MAX_SHOWN_VOTERS and "+5 more" in text


def test_fallback_text_cannot_ping_either():
    p = P.new("C1", "<!channel> & co", P.clean_questions([{"options": ["A", "B"]}]), "U0M")
    assert P.fallback_text(p) == "📊 &lt;!channel&gt; &amp; co"


def test_single_question_poll_does_not_repeat_its_title():
    qs = P.clean_questions([{"title": "Lunch", "options": ["A", "B"]}])
    blocks = P.render_blocks(P.new("C1", "Lunch", qs, "U0M"))
    assert blocks[1]["text"]["text"] == "_(pick any)_"


# ── results ──────────────────────────────────────────────────────────────────
def test_results_mark_the_leader_and_name_voters_without_pinging():
    p = P.vote(P.vote(make(), "0:1", VOTER), "0:1", OTHER)
    text = P.results_text(p, name_of={VOTER: "Ann", OTHER: "Bo"}.get)
    assert "2. B — 2 votes 🏆: Ann, Bo" in text
    assert "1. A — 0 votes" in text and "🏆" not in text.split("1. A")[1].split("\n")[0]
    assert "<@" not in text


def test_results_with_no_votes_crown_nobody():
    assert "🏆" not in P.results_text(make())


def test_results_say_when_closed():
    assert "Closed" in P.results_text(make(closed=True))


# ── storage ──────────────────────────────────────────────────────────────────
def test_save_find_and_delete(tmp_path):
    p = {**make(), "message_ts": "5.5"}
    P.save(tmp_path, p)
    assert P.load_by_ts(tmp_path, "C1", "5.5")["title"] == "Dinner"
    assert P.find_target(tmp_path, "C1", "1.1")["message_ts"] == "5.5"     # by its thread
    assert P.find_target(tmp_path, "C1", "5.5")["message_ts"] == "5.5"     # by its own ts
    assert P.find_latest(tmp_path, "C1")["message_ts"] == "5.5"
    P.delete(tmp_path, p)
    assert P.load_by_ts(tmp_path, "C1", "5.5") is None
    P.delete(tmp_path, p)                                                  # twice is fine
