"""Telegram adapter — routing, the trust model, and command reuse."""
import os

import pytest

OWNER = "123456"
BOT_ID = "999"
BOT_NAME = "lokibot"


@pytest.fixture(scope="session")
def tg_module(tmp_path_factory):
    work = tmp_path_factory.mktemp("tgwork")
    env = {"TELEGRAM_BOT_TOKEN": "111:AAA", "TELEGRAM_OWNER_ID": OWNER,
           "WORK_DIR": str(work), "SELFTEST_ON_BOOT": "0"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        from loki.platforms.telegram import adapter as mod
        mod.BOT_USERNAME, mod.BOT_USER_ID = BOT_NAME, BOT_ID
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def tg(tg_module, tmp_path, monkeypatch):
    """Adapter with the Bot API stubbed and the queue captured."""
    from loki.core import (blocked, budget, config, dedup, ratelimit,
                           sessions, usage)

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(budget, "BUDGET_FILE", tmp_path / "budget.json")
    budget._cache.update(stamp=None, data=None)
    monkeypatch.setattr(dedup, "already_seen", lambda _id: False)
    monkeypatch.setattr(ratelimit, "check", lambda u, limit=None: (True, 0))
    monkeypatch.setattr(usage, "record", lambda *a, **kw: None)
    monkeypatch.setattr(blocked, "is_blocked", lambda c: False)
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(tg_module, "_api",
                        lambda method, params=None, timeout=None:
                        (calls.append((method, params or {})), {})[1])
    docs: list = []
    monkeypatch.setattr(tg_module, "_send_document",
                        lambda chat, path, thread=None:
                        (docs.append((chat, str(path))), True)[1])

    monkeypatch.setattr(tg_module, "_download",
                        lambda file_id, dest: (dest.write_bytes(b"x"), True)[1])

    submitted: list[dict] = []
    monkeypatch.setattr(tg_module.jobs, "submit",
                        lambda job: (submitted.append(job), ("j1", 0))[1])

    tg_module.work = work
    tg_module.calls = calls
    tg_module.docs = docs
    tg_module.submitted = submitted
    tg_module.sent = lambda: [p.get("text", "") for m, p in calls
                              if m == "sendMessage"]
    tg_module._history.clear()
    return tg_module


def update(text="hello", user=OWNER, chat=None, chat_type="private",
           uid=1, **extra):
    msg = {"message_id": uid, "text": text,
           "from": {"id": int(user), "username": f"u{user}"},
           "chat": {"id": int(chat if chat is not None else user),
                    "type": chat_type}}
    msg.update(extra)
    return {"update_id": uid, "message": msg}


# ── the trust model ──────────────────────────────────────────────────────────
def test_owner_private_message_reaches_the_brain(tg):
    tg._dispatch(update("what's in my inbox"))
    job = tg.submitted[0]
    assert job["text"] == "what's in my inbox"
    assert job["kind"] == "owner" and job["user"] == OWNER


def test_stranger_private_message_is_ignored(tg):
    tg._dispatch(update("hi", user="777"))
    assert tg.submitted == [] and tg.sent() == []


def test_group_message_needs_addressing(tg):
    tg._dispatch(update("random chatter", user="777", chat="-100",
                        chat_type="supergroup"))
    assert tg.submitted == []


def test_group_mention_is_a_read_only_guest(tg):
    tg._dispatch(update(f"@{BOT_NAME} what is this repo", user="777",
                        chat="-100", chat_type="supergroup"))
    job = tg.submitted[0]
    assert job["permission_mode"] == "plan"
    assert job["kind"] == "guest"
    assert job["text"] == "what is this repo"       # mention stripped


def test_reply_to_the_bot_counts_as_addressing(tg):
    tg._dispatch(update("and the tests?", user="777", chat="-100",
                        chat_type="supergroup",
                        reply_to_message={"from": {"id": int(BOT_ID)}}))
    assert len(tg.submitted) == 1


def test_reply_to_someone_else_does_not(tg):
    tg._dispatch(update("nice", user="777", chat="-100", chat_type="supergroup",
                        reply_to_message={"from": {"id": 555}}))
    assert tg.submitted == []


def test_owner_in_a_group_keeps_owner_powers(tg):
    tg._dispatch(update(f"@{BOT_NAME} fix the build", chat="-100",
                        chat_type="supergroup"))
    job = tg.submitted[0]
    assert job["kind"] == "owner"
    assert job["permission_mode"] == tg.config.PERMISSION_MODE


def test_other_bots_are_ignored(tg):
    up = update("deploy finished", user="777", chat="-100",
                chat_type="supergroup")
    up["message"]["from"]["is_bot"] = True
    tg._dispatch(up)
    assert tg.submitted == []


def test_blocked_group_is_silent(tg, monkeypatch):
    monkeypatch.setattr(tg.blocked, "is_blocked", lambda c: True)
    tg._dispatch(update(f"@{BOT_NAME} hi", user="777", chat="-100",
                        chat_type="supergroup"))
    assert tg.submitted == []


def test_auto_listen_zone_drops_the_mention_requirement(tg, monkeypatch):
    monkeypatch.setattr(tg.autolisten, "is_zone", lambda c, t: c == "-100")
    tg._dispatch(update("no mention needed", user="777", chat="-100",
                        chat_type="supergroup"))
    assert len(tg.submitted) == 1


# ── conversations ────────────────────────────────────────────────────────────
def test_chat_is_one_conversation(tg):
    tg._dispatch(update("first", uid=1))
    tg._dispatch(update("second", uid=2))
    assert tg.submitted[0]["thread"] == tg.submitted[1]["thread"]


def test_forum_topics_are_separate_conversations(tg):
    tg._dispatch(update(f"@{BOT_NAME} in topic 5", chat="-100",
                        chat_type="supergroup", message_thread_id=5, uid=1))
    tg._dispatch(update(f"@{BOT_NAME} in topic 9", chat="-100",
                        chat_type="supergroup", message_thread_id=9, uid=2))
    assert tg.submitted[0]["thread"] != tg.submitted[1]["thread"]


def test_new_clears_the_session(tg):
    """Session reset is upstream's built-in `!new`, routed through core."""
    key = tg.sessions.key_for("123456", None, is_dm=True)
    tg.sessions.remember(key, "sess-abc")
    tg._dispatch(update("!new"))
    assert tg.sessions.get(key) is None
    assert tg.submitted == []


def test_group_history_becomes_context(tg, monkeypatch):
    monkeypatch.setattr(tg.autolisten, "is_zone", lambda c, t: True)
    tg._dispatch(update("the deploy broke", user="777", chat="-100",
                        chat_type="supergroup", uid=1))
    tg._dispatch(update("what happened?", user="778", chat="-100",
                        chat_type="supergroup", uid=2))
    assert "the deploy broke" in tg._context("-100")
    assert tg.submitted[0]["with_context"] is True


def test_private_chat_carries_no_group_context(tg):
    tg._dispatch(update("hello"))
    assert tg.submitted[0]["with_context"] is False


# ── commands come from core ──────────────────────────────────────────────────
def test_core_commands_work_unchanged(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(tg.scheduler, "SCHED_FILE", tmp_path / "s.json")
    tg._dispatch(update("!schedule daily 09:00 morning summary"))
    assert tg.scheduler.list_all()[0]["prompt"] == "morning summary"
    assert tg.submitted == []
    assert "09:00" in tg.sent()[0]


def test_alias_round_trip(tg):
    (tg.work / "loki").mkdir()
    tg._dispatch(update("!alias add standup summarize commits", uid=1))
    assert tg.alias.get("standup") == "summarize commits"
    tg._dispatch(update("!standup", uid=2))
    assert tg.submitted[0]["text"] == "summarize commits"
    assert tg.submitted[0]["kind"] == "alias"


def test_budget_command_and_guest_cap(tg):
    tg._dispatch(update("!budget daily 1", uid=1))
    assert tg.budget.settings()["daily"] == 1
    tg.budget.apply("pause")
    tg._dispatch(update(f"@{BOT_NAME} hi", user="777", chat="-100",
                        chat_type="supergroup", uid=2))
    assert tg.submitted == []


def test_guest_cannot_run_commands(tg):
    tg._dispatch(update(f"@{BOT_NAME} !budget off", user="777", chat="-100",
                        chat_type="supergroup"))
    assert tg.submitted[0]["text"] == "!budget off"      # treated as text


def test_stop_is_owner_only(tg):
    tg._dispatch(update("!stop"))
    assert tg.submitted == [] and tg.sent()


# ── files ────────────────────────────────────────────────────────────────────
def test_send_uploads_from_work_dir(tg):
    (tg.work / "notes.md").write_text("hi")
    tg._dispatch(update("!send notes.md"))
    assert len(tg.docs) == 1 and tg.docs[0][1].endswith("notes.md")


def test_send_refuses_outside_work_dir(tg, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("no")
    tg._dispatch(update(f"!send {outside}"))
    assert tg.docs == [] and "WORK_DIR" in tg.sent()[0]


def test_owner_document_is_accepted(tg):
    tg._dispatch(update("read this", document={
        "file_id": "F1", "file_name": "report.pdf",
        "mime_type": "application/pdf"}))
    job = tg.submitted[0]
    assert len(job["doc_paths"]) == 1 and job["image_paths"] == []


def test_blocked_document_type_is_refused(tg):
    tg._dispatch(update("", document={
        "file_id": "F2", "file_name": "payload.exe",
        "mime_type": "application/octet-stream"}))
    assert tg.submitted == []
    assert "payload.exe" in tg.sent()[0]


def test_photo_takes_the_largest_size(tg):
    accepted, _ = tg._classify_attachments(
        {"photo": [{"file_id": "small"}, {"file_id": "big"}]})
    assert accepted[0] == {"file_id": "big", "name": "photo.jpg",
                           "kind": "image"}


def test_photo_is_downloaded_as_an_image(tg):
    tg._dispatch(update("", photo=[{"file_id": "big"}]))
    assert len(tg.submitted[0]["image_paths"]) == 1


def test_guest_attachments_are_ignored(tg):
    tg._dispatch(update(f"@{BOT_NAME} look", user="777", chat="-100",
                        chat_type="supergroup",
                        document={"file_id": "F3", "file_name": "a.pdf",
                                  "mime_type": "application/pdf"}))
    assert tg.submitted[0]["doc_paths"] == []
    assert tg.submitted[0]["image_paths"] == []


# ── entrypoint ───────────────────────────────────────────────────────────────
def test_telegram_is_a_known_platform():
    from loki import __main__ as entry
    assert "telegram" in entry.PLATFORMS


def test_unknown_platform_is_refused(monkeypatch):
    from loki import __main__ as entry
    monkeypatch.setattr(entry.config, "validate_core", lambda: None)
    assert entry._run_worker("carrier-pigeon") == 2


def test_owner_can_still_reach_a_blocked_group(tg, monkeypatch):
    """Otherwise `!unblock` is unreachable from the group you'd type it in."""
    monkeypatch.setattr(tg.blocked, "is_blocked", lambda c: True)
    tg._dispatch(update(f"@{BOT_NAME} !unblock -100", chat="-100",
                        chat_type="supergroup"))
    assert tg.sent()                       # answered the owner


def test_blocking_switches_off_the_zone(tg, monkeypatch):
    monkeypatch.setattr(tg.blocked, "is_blocked", lambda c: True)
    monkeypatch.setattr(tg.autolisten, "is_zone", lambda c, t: True)
    tg._dispatch(update("unaddressed", chat="-100", chat_type="supergroup"))
    assert tg.submitted == []
