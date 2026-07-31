"""Bot allowlist — identity, the loop guard, and reading bot content."""
import pytest

from loki.core import botallow


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(botallow, "ALLOW_FILE", tmp_path / "bots.json")
    botallow._seen.clear()
    return tmp_path


def bot_event(bot_id="B0CI", **extra):
    ev = {"bot_id": bot_id, "channel": "C0PUB", "text": "build failed"}
    ev.update(extra)
    return ev


# ── allowlist CRUD ───────────────────────────────────────────────────────────
def test_default_is_empty(store):
    assert botallow.allowed() == []
    assert botallow.is_allowed(bot_event()) is False


def test_allow_and_deny(store):
    assert botallow.allow("B0CI123") is True
    assert botallow.allow("B0CI123") is False        # already there
    assert botallow.allowed() == ["B0CI123"]
    assert botallow.deny("B0CI123") is True
    assert botallow.deny("B0CI123") is False
    assert botallow.allowed() == []


def test_allow_normalises_case(store):
    botallow.allow("b0ci123")
    assert botallow.allowed() == ["B0CI123"]


def test_allow_rejects_malformed_ids(store):
    for bad in ("", "U0USER12", "notabot", "B", "C0CHAN12"):
        assert botallow.allow(bad) is False
    assert botallow.allowed() == []


def test_corrupt_file_fails_closed(store):
    botallow.ALLOW_FILE.write_text("{not json", encoding="utf-8")
    assert botallow.allowed() == []


def test_junk_entries_are_filtered_out(store):
    botallow.ALLOW_FILE.write_text('["B0GOOD12", "U0BAD", 42, null]',
                                   encoding="utf-8")
    assert botallow.allowed() == ["B0GOOD12"]


# ── the loop guard ───────────────────────────────────────────────────────────
def test_loki_can_never_trigger_itself(store):
    """Even allowlisted, Loki's own ids must never come back through."""
    botallow.allow("B0SELF12")
    ev = bot_event("B0SELF12")
    assert botallow.is_allowed(ev, self_ids={"B0SELF12"}) is False
    assert botallow.is_allowed(ev, self_ids={"UBOT", "B0SELF12"}) is False


def test_loop_guard_also_matches_the_bot_user_id(store):
    botallow.allow("B0SELF12")
    ev = bot_event("B0SELF12", user="UBOT")
    assert botallow.is_allowed(ev, self_ids={"UBOT"}) is False


def test_allowed_third_party_bot_passes(store):
    botallow.allow("B0CI123")
    assert botallow.is_allowed(bot_event("B0CI123"), self_ids={"UBOT"}) is True


def test_unlisted_bot_is_refused(store):
    botallow.allow("B0CI123")
    assert botallow.is_allowed(bot_event("B0OTHER1"), self_ids={"UBOT"}) is False


def test_non_bot_event_is_not_a_bot(store):
    assert botallow.is_allowed({"user": "UGUEST", "text": "hi"}) is False


# ── observation ──────────────────────────────────────────────────────────────
def test_seen_records_id_name_and_status(store):
    botallow.observe(bot_event("B0CI123", bot_profile={"name": "CircleCI"}))
    entry = botallow.seen()[0]
    assert entry["id"] == "B0CI123" and entry["name"] == "CircleCI"
    assert entry["allowed"] is False

    botallow.allow("B0CI123")
    assert botallow.seen()[0]["allowed"] is True


def test_seen_falls_back_to_username(store):
    botallow.observe(bot_event("B0CI123", username="jenkins"))
    assert botallow.seen()[0]["name"] == "jenkins"


def test_seen_is_bounded(store):
    for i in range(30):
        botallow.observe(bot_event(f"B0BOT{i:03d}"))
    assert len(botallow.seen()) <= botallow.SEEN_MAX


def test_observe_ignores_non_bot_events(store):
    botallow.observe({"user": "UGUEST", "text": "hi"})
    assert botallow.seen() == []


# ── reading bot content ──────────────────────────────────────────────────────
def test_message_text_prefers_text():
    assert botallow.message_text(bot_event()) == "build failed"


def test_message_text_reads_attachment_fallbacks():
    ev = bot_event(text="", attachments=[
        {"fallback": "Build #42 failed on main"},
        {"title": "stacktrace", "text": "NullPointerException"},
    ])
    out = botallow.message_text(ev)
    assert "Build #42 failed" in out and "stacktrace" in out


def test_message_text_is_capped():
    ev = bot_event(text="x" * 99999)
    assert len(botallow.message_text(ev)) <= botallow.TEXT_MAX


def test_message_text_survives_malformed_attachments():
    ev = bot_event(text="ok", attachments=["not a dict", None, {}])
    assert botallow.message_text(ev) == "ok"


def test_message_text_empty_when_nothing_readable():
    assert botallow.message_text({"bot_id": "B0CI", "text": ""}) == ""
