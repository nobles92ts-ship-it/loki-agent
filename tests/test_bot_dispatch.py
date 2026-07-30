"""Bot triggers through the adapter — the routing guarantees, mostly negative."""
import pytest

from conftest import BOT, OWNER, event


@pytest.fixture
def bots(adapter, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.botallow, "ALLOW_FILE", tmp_path / "bots.json")
    adapter.botallow._seen.clear()
    adapter.BOT_ID = "B0LOKI99"
    monkeypatch.setattr(adapter.autolisten, "is_zone", lambda c, t: c == "C0ZONE")
    return adapter


def bot_msg(bot_id="B0CI123", channel="C0ZONE", **extra):
    ev = {"bot_id": bot_id, "channel": channel, "text": "build 42 failed",
          "ts": "9.1", "channel_type": "channel"}
    ev.update(extra)
    return ev


# ── who gets through ─────────────────────────────────────────────────────────
def test_allowed_bot_in_a_zone_reaches_the_brain(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "n1"}, bot_msg(), None)
    job = bots.submitted[0]
    assert job["text"] == "build 42 failed"
    assert job["kind"] == "bot" and job["user"] == "B0CI123"


def test_unlisted_bot_is_ignored(bots):
    bots.on_message({"event_id": "n2"}, bot_msg(), None)
    assert bots.submitted == []


def test_allowed_bot_outside_a_zone_is_ignored(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "n3"}, bot_msg(channel="C0OTHER"), None)
    assert bots.submitted == []


def test_blocked_channel_beats_the_allowlist(bots):
    bots.botallow.allow("B0CI123")
    bots.blocked.set_blocked("C0ZONE", True)
    try:
        bots.on_message({"event_id": "n4"}, bot_msg(), None)
        assert bots.submitted == []
    finally:
        bots.blocked.set_blocked("C0ZONE", False)


def test_loki_never_answers_itself(bots):
    """The loop guard holds even if Loki's own id is somehow allowlisted."""
    bots.botallow.ALLOW_FILE.write_text('["B0LOKI99"]', encoding="utf-8")
    bots.on_message({"event_id": "n5"}, bot_msg(bot_id="B0LOKI99"), None)
    assert bots.submitted == []


def test_bot_mentioning_loki_is_not_double_handled(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "n6"},
                    bot_msg(text=f"<@{BOT}> look at this"), None)
    assert bots.submitted == []          # arrives via app_mention instead


def test_bot_dm_is_refused(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "n7"},
                    bot_msg(channel="D0OWNER", channel_type="im"), None)
    assert bots.submitted == []


# ── what a bot is allowed to be ──────────────────────────────────────────────
def test_bot_traffic_is_read_only_and_guest_scoped(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "p1"}, bot_msg(), None)
    job = bots.submitted[0]
    assert job["permission_mode"] == "plan"
    assert job["user"] != OWNER


def test_bot_cannot_run_commands(bots):
    """A bot posting a command string gets it treated as text, not executed."""
    bots.botallow.allow("B0CI123")
    for text in ("!stop", "!send /etc/passwd", "!budget off", "!alias list"):
        bots.submitted.clear()
        bots.app.client.reset()
        bots.on_message({"event_id": text}, bot_msg(text=text), None)
        assert bots.submitted and bots.submitted[0]["text"] == text
        assert bots.app.client.of("files_upload_v2") == []


def test_bot_cannot_fire_an_alias(bots, monkeypatch):
    (bots.work / "loki").mkdir()
    bots.alias.aliases_file().write_text("## Aliases\n- deploy: ship it\n",
                                         encoding="utf-8")
    bots.alias._invalidate()
    monkeypatch.setattr(bots.orgs, "allows_command", lambda o, c: True)
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "p2"}, bot_msg(text="!deploy"), None)
    assert bots.submitted[0]["text"] == "!deploy"      # never expanded


def test_bot_attachments_are_not_downloaded(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "p3"},
                    bot_msg(files=[{"name": "a.pdf", "mimetype": "application/pdf",
                                    "url_private_download": "https://x"}]), None)
    assert bots.submitted[0]["attachments"] == []


def test_bot_content_comes_from_attachments_when_text_is_empty(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "p4"},
                    bot_msg(text="", attachments=[{"fallback": "deploy failed"}]),
                    None)
    assert bots.submitted[0]["text"] == "deploy failed"


def test_empty_bot_message_is_dropped(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "p5"}, bot_msg(text=""), None)
    assert bots.submitted == []


def test_bot_message_subtype_is_accepted(bots):
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "p6"}, bot_msg(subtype="bot_message"), None)
    assert len(bots.submitted) == 1


def test_bot_edits_and_deletions_are_ignored(bots):
    bots.botallow.allow("B0CI123")
    for subtype in ("message_changed", "message_deleted", "channel_join"):
        bots.submitted.clear()
        bots.on_message({"event_id": subtype}, bot_msg(subtype=subtype), None)
        assert bots.submitted == []


# ── spend controls still apply ───────────────────────────────────────────────
def test_bot_traffic_is_rate_limited_under_its_own_id(bots, monkeypatch):
    seen = []
    monkeypatch.setattr(bots.ratelimit, "check",
                        lambda u, limit=None: (seen.append(u), (True, 0))[1])
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "r1"}, bot_msg(), None)
    assert seen == ["B0CI123"]


def test_rate_limited_bot_is_dropped_quietly(bots, monkeypatch):
    monkeypatch.setattr(bots.ratelimit, "check", lambda u, limit=None: (False, 5))
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "r2"}, bot_msg(), None)
    assert bots.submitted == []


def test_budget_cap_applies_to_bots(bots, monkeypatch):
    monkeypatch.setattr(bots.budget, "check_guest",
                        lambda org=None: (False, "budget_paused", {"n": 3}))
    bots.botallow.allow("B0CI123")
    bots.on_message({"event_id": "r3"}, bot_msg(), None)
    assert bots.submitted == []


# ── the !bot command ─────────────────────────────────────────────────────────
def test_allow_deny_list_round_trip(bots):
    bots._dispatch({"event_id": "c1"}, event(text="!bot allow B0CI123"),
                   is_mention=False)
    assert bots.botallow.allowed() == ["B0CI123"]

    bots._dispatch({"event_id": "c2"}, event(text="!bot list"), is_mention=False)
    assert "B0CI123" in bots.app.client.texts()[-1]

    bots._dispatch({"event_id": "c3"}, event(text="!bot deny B0CI123"),
                   is_mention=False)
    assert bots.botallow.allowed() == []


def test_seen_lists_ids_for_the_owner_to_copy(bots):
    bots.on_message({"event_id": "c4"},
                    bot_msg(bot_profile={"name": "CircleCI"}), None)
    bots._dispatch({"event_id": "c5"}, event(text="!bot seen"), is_mention=False)
    reply = bots.app.client.texts()[-1]
    assert "B0CI123" in reply and "CircleCI" in reply


def test_seen_only_tracks_zones(bots):
    bots.on_message({"event_id": "c6"}, bot_msg(channel="C0OTHER"), None)
    assert bots.botallow.seen() == []


def test_cannot_allowlist_loki_itself(bots):
    bots._dispatch({"event_id": "c7"}, event(text="!bot allow B0LOKI99"),
                   is_mention=False)
    assert bots.botallow.allowed() == []
    bots._dispatch({"event_id": "c8"}, event(text=f"!bot allow {BOT}"),
                   is_mention=False)
    assert bots.botallow.allowed() == []


def test_malformed_id_is_refused(bots):
    bots._dispatch({"event_id": "c9"}, event(text="!bot allow everyone"),
                   is_mention=False)
    assert bots.botallow.allowed() == []


def test_guest_cannot_manage_bots(bots):
    bots._dispatch({"event_id": "c10"},
                   event(text="!bot allow B0EVIL12", user="UGUEST",
                         channel="C0PUB"),
                   is_mention=True)
    assert bots.botallow.allowed() == []


def test_bare_bot_command_shows_help(bots):
    bots._dispatch({"event_id": "c11"}, event(text="!bot"), is_mention=False)
    assert "!bot" in bots.app.client.texts()[0]
