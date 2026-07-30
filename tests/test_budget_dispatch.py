"""Budget wiring — guest enforcement, owner exemption, alerts and buttons."""
import json
import time

import pytest

from conftest import OWNER, event


@pytest.fixture
def bud(adapter, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.budget, "BUDGET_FILE", tmp_path / "budget.json")
    monkeypatch.setattr(adapter.usage, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(adapter.usage, "record", lambda *a, **kw: None)
    adapter.budget._cache.update(stamp=None, data=None)
    adapter._owner_dm_id = None
    return adapter


def spend(mod, n, org=None):
    rows = []
    for _ in range(n):
        row = {"ts": time.time(), "kind": "guest", "user": "UG", "ok": True,
               "dur": 1.0, "reason": "ok"}
        if org:
            row["org"] = org
        rows.append(json.dumps(row))
    with mod.usage.USAGE_FILE.open("a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def guest(text="hello"):
    return event(text=text, user="UGUEST", channel="C0PUB")


# ── enforcement ──────────────────────────────────────────────────────────────
def test_guest_refused_when_the_cap_is_reached(bud):
    bud.budget.set_limit("daily", 2)
    spend(bud, 2)
    bud._dispatch({"event_id": "b1"}, guest(), is_mention=True)
    assert bud.submitted == []
    assert "2/2" in bud.app.client.texts()[0]


def test_guest_allowed_below_the_cap(bud):
    bud.budget.set_limit("daily", 5)
    spend(bud, 2)
    bud._dispatch({"event_id": "b2"}, guest(), is_mention=True)
    assert len(bud.submitted) == 1


def test_owner_is_never_capped(bud):
    bud.budget.set_limit("daily", 1)
    spend(bud, 50)
    bud._dispatch({"event_id": "b3"}, event(text="still mine"), is_mention=False)
    assert len(bud.submitted) == 1


def test_paused_guests_are_told_when_to_return(bud):
    bud.budget.apply("pause")
    bud._dispatch({"event_id": "b4"}, guest(), is_mention=True)
    assert bud.submitted == []
    assert "min" in bud.app.client.texts()[0] or "분" in bud.app.client.texts()[0]


def test_pause_does_not_touch_the_owner(bud):
    bud.budget.apply("pause")
    bud._dispatch({"event_id": "b5"}, event(text="mine"), is_mention=False)
    assert len(bud.submitted) == 1


def test_org_cap_does_not_block_a_different_org(bud, monkeypatch):
    bud.budget.set_org_limit("acme", 1)
    spend(bud, 5, org="acme")
    monkeypatch.setattr(bud.orgs, "resolve", lambda u, c: "beta")
    bud._dispatch({"event_id": "b6"}, guest(), is_mention=True)
    assert len(bud.submitted) == 1


def test_budget_check_precedes_the_rate_limiter(bud, monkeypatch):
    """A capped guest shouldn't burn a rate-limit slot to be told no."""
    calls = []
    monkeypatch.setattr(bud.ratelimit, "check",
                        lambda u, limit=None: (calls.append(u), (True, 0))[1])
    bud.budget.set_limit("daily", 1)
    spend(bud, 1)
    bud._dispatch({"event_id": "b7"}, guest(), is_mention=True)
    assert calls == []


# ── the command ──────────────────────────────────────────────────────────────
def test_status_reports_caps_and_usage(bud):
    bud.budget.set_limit("daily", 10)
    spend(bud, 4)
    bud._dispatch({"event_id": "c1"}, event(text="!budget"), is_mention=False)
    reply = bud.app.client.texts()[0]
    assert "manual" in reply and "4/10" in reply


def test_setting_caps_and_mode(bud):
    bud._dispatch({"event_id": "c2"}, event(text="!budget daily 25"),
                  is_mention=False)
    assert bud.budget.settings()["daily"] == 25

    bud._dispatch({"event_id": "c3"}, event(text="!budget mode auto"),
                  is_mention=False)
    assert bud.budget.settings()["mode"] == "auto"

    bud._dispatch({"event_id": "c4"}, event(text="!budget org acme 5"),
                  is_mention=False)
    assert bud.budget.settings()["orgs"] == {"acme": 5}

    bud._dispatch({"event_id": "c5"}, event(text="!budget off"), is_mention=False)
    assert bud.budget.settings()["daily"] == 0


def test_manual_mitigations_via_text(bud):
    bud._dispatch({"event_id": "c6"}, event(text="!budget sonnet"),
                  is_mention=False)
    assert bud.budget.model_override() == "sonnet"
    bud._dispatch({"event_id": "c7"}, event(text="!budget default"),
                  is_mention=False)
    assert bud.budget.model_override() == ""


def test_bad_budget_input_shows_help(bud):
    for text in ("!budget mode chaos", "!budget daily abc", "!budget org acme"):
        bud.app.client.reset()
        bud._dispatch({"event_id": text}, event(text=text), is_mention=False)
        assert "!budget" in bud.app.client.texts()[0]
    assert bud.budget.settings()["mode"] == "manual"


def test_guest_cannot_touch_the_budget(bud):
    bud._dispatch({"event_id": "c8"}, guest("!budget daily 9999"),
                  is_mention=True)
    assert bud.budget.settings()["daily"] == 0


def test_korean_budget_alias(bud):
    bud._dispatch({"event_id": "c9"}, event(text="!예산 daily 7"), is_mention=False)
    assert bud.budget.settings()["daily"] == 7


# ── alerts ───────────────────────────────────────────────────────────────────
def _ok_result(*a, **kw):
    return {"text": "done", "session_id": None, "error": False, "reason": "ok"}


def test_crossing_a_threshold_dms_the_owner_with_buttons(bud, monkeypatch):
    monkeypatch.setattr(bud.brain, "run_claude", _ok_result)
    bud.budget.set_limit("daily", 10)
    spend(bud, 8)
    bud._handle({"channel": "C0PUB", "thread": "1.1", "text": "go",
                 "user": "UGUEST", "event_id": "a1", "permission_mode": "plan",
                 "kind": "guest"})
    alerts = [p for p in bud.app.client.of("chat_postMessage") if p.get("blocks")]
    assert len(alerts) == 1
    actions = [b for b in alerts[0]["blocks"] if b["type"] == "actions"][0]
    assert {e["value"] for e in actions["elements"]} == {"sonnet", "pause", "ignore"}
    assert bud.budget.model_override() == ""        # manual: nothing applied yet


def test_full_alert_drops_the_pause_button(bud, monkeypatch):
    monkeypatch.setattr(bud.brain, "run_claude", _ok_result)
    bud.budget.set_limit("daily", 2)
    spend(bud, 2)
    bud._handle({"channel": "C0PUB", "thread": "1.1", "text": "go",
                 "user": "UGUEST", "event_id": "a2", "permission_mode": "plan",
                 "kind": "guest"})
    alerts = [p for p in bud.app.client.of("chat_postMessage") if p.get("blocks")]
    actions = [b for b in alerts[0]["blocks"] if b["type"] == "actions"][0]
    assert {e["value"] for e in actions["elements"]} == {"sonnet", "ignore"}


def test_no_alert_without_a_budget(bud, monkeypatch):
    monkeypatch.setattr(bud.brain, "run_claude", _ok_result)
    spend(bud, 100)
    bud._handle({"channel": "C0PUB", "thread": "1.1", "text": "go",
                 "user": OWNER, "event_id": "a3", "permission_mode": "plan",
                 "kind": "owner"})
    assert [p for p in bud.app.client.of("chat_postMessage")
            if p.get("blocks")] == []


# ── buttons ──────────────────────────────────────────────────────────────────
def _click(mod, value, user=OWNER):
    mod._on_budget_action(
        ack=lambda: None,
        body={"user": {"id": user}, "actions": [{"value": value}],
              "channel": {"id": "D0OWNER"}, "message": {"ts": "1.1"}},
        client=mod.app.client)


def test_button_applies_and_retires_itself(bud):
    _click(bud, "sonnet")
    assert bud.budget.model_override() == "sonnet"
    update = bud.app.client.of("chat_update")[0]
    assert update["blocks"] == [] and "sonnet" in update["text"]


def test_pause_button_holds_guests(bud):
    _click(bud, "pause")
    assert bud.budget.guests_paused_for() > 0


def test_non_owner_click_is_ignored(bud):
    _click(bud, "sonnet", user="UGUEST")
    assert bud.budget.model_override() == ""
    assert bud.app.client.of("chat_update") == []


def test_unknown_button_value_changes_nothing(bud):
    _click(bud, "sudo-rm-rf")
    assert bud.budget.model_override() == ""
