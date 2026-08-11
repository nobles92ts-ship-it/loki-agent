"""Nudges — what trips, what stays quiet, and what a stranger is allowed to see.

The CRUD here is thin. The behaviour worth pinning down is the restraint: a
watcher that is right is a watcher that repeats, and an assistant that repeats
gets muted — taking the useful alerts down with the noisy one. So most of these
tests are about *not* firing.
"""
import json
import time

import pytest

from loki.core import goals, nudge, usage


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(nudge, "_FILE", tmp_path / "nudges.json")
    monkeypatch.setattr(goals, "_FILE", tmp_path / "goals.json")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(nudge, "ENABLED", True)
    return tmp_path


def _age_goal(gid, hours, store):
    """Backdate a goal's last touch without waiting three days for it."""
    data = json.loads((store / "goals.json").read_text(encoding="utf-8"))
    data[gid]["touched"] = time.time() - hours * 3600
    (store / "goals.json").write_text(json.dumps(data), encoding="utf-8")


def _runs(store, *oks, reason="error"):
    (store / "usage.jsonl").write_text("".join(
        json.dumps({"ts": time.time(), "kind": "owner", "user": "U1", "ok": ok,
                    "dur": 1.0, "reason": "ok" if ok else reason}) + "\n"
        for ok in oks), encoding="utf-8")


def _keys(items):
    return [n["key"] for n in items]


# ── the stale-goal watcher ───────────────────────────────────────────────────
def test_a_fresh_goal_is_not_stale(store):
    goals.add("ship v1.10")
    assert nudge.probe() == []


def test_a_goal_nobody_touched_trips(store):
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert _keys(nudge.probe()) == ["goal:g1"]


def test_ticking_a_step_resets_the_clock(store):
    """`touched` is what makes the watcher mean "abandoned" rather than "old"."""
    goals.add("ship v1.10")
    goals.add_step("g1", "write the notes")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    goals.step_done("g1", 1)
    assert nudge.probe() == []


def test_a_closed_goal_stops_being_watched(store):
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    goals.close("g1")
    assert nudge.probe() == []


def test_the_nudge_names_the_next_undone_step(store):
    goals.add("ship v1.10")
    goals.add_step("g1", "write the notes")
    goals.add_step("g1", "tag the release")
    goals.step_done("g1", 1)
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert "tag the release" in nudge.probe()[0]["prompt"]


def test_a_goal_with_no_steps_still_says_something_useful(store):
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert nudge.probe()[0]["prompt"].strip()


def test_a_goal_written_before_touched_existed_still_ages(store):
    """Upgrading an install must not make every existing goal invisible."""
    goals.add("ship v1.10")
    data = json.loads((store / "goals.json").read_text(encoding="utf-8"))
    data["g1"].pop("touched")
    data["g1"]["created"] = time.time() - (nudge.STALE_GOAL_H + 1) * 3600
    (store / "goals.json").write_text(json.dumps(data), encoding="utf-8")
    assert _keys(nudge.probe()) == ["goal:g1"]


# ── the failure watcher ──────────────────────────────────────────────────────
def test_one_failure_is_weather(store):
    _runs(store, True, True, False)
    assert nudge.probe() == []


def test_a_streak_of_the_same_failure_trips(store):
    _runs(store, True, False, False, False, reason="not_found")
    n, = nudge.probe()
    assert n["key"] == "fail:not_found"
    assert "not_found" in n["why"]


def test_a_streak_of_different_failures_does_not(store):
    """Three unrelated errors are three problems, not one story to tell."""
    (store / "usage.jsonl").write_text("".join(
        json.dumps({"ts": time.time(), "ok": False, "reason": r}) + "\n"
        for r in ("error", "timeout", "not_found")), encoding="utf-8")
    assert nudge.probe() == []


def test_a_success_after_the_streak_clears_it(store):
    _runs(store, False, False, False, True)
    assert nudge.probe() == []


def test_an_empty_ledger_says_nothing(store):
    assert nudge.probe() == []


# ── the provider watcher ─────────────────────────────────────────────────────
def test_a_provider_that_cannot_answer_trips(store, monkeypatch):
    from loki.core import providers
    monkeypatch.setattr(providers, "current",
                        lambda: providers.ALL["groq"])
    monkeypatch.setattr(providers.ALL["groq"], "available",
                        lambda: (False, "set GROQ_API_KEY in .env"))
    n, = nudge.probe()
    assert n["key"] == "provider:groq"
    assert "GROQ_API_KEY" in n["prompt"]


def test_a_probe_that_explodes_does_not_take_the_loop_down(store, monkeypatch):
    """A watcher is a background thread. One that raises must cost its own
    nudge, not every other watcher's."""
    from loki.core import providers
    boom = type("X", (), {"NAME": "x",
                          "available": staticmethod(lambda: 1 / 0)})
    monkeypatch.setattr(providers, "current", lambda: boom)
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert _keys(nudge.probe()) == ["goal:g1"]


# ── restraint: cooldown, silencing, the master switch ────────────────────────
def test_watch_fires_once_then_goes_quiet(store):
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert _keys(nudge.watch()) == ["goal:g1"]
    assert nudge.watch() == []              # still true, already said
    assert nudge.probe()                    # …but the condition has not gone away


def test_the_cooldown_expires(store):
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    nudge.watch()
    state = json.loads((store / "nudges.json").read_text(encoding="utf-8"))
    state["fired"]["goal:g1"] = time.time() - nudge.COOLDOWN_H["goal"] * 3600 - 1
    (store / "nudges.json").write_text(json.dumps(state), encoding="utf-8")
    assert _keys(nudge.watch()) == ["goal:g1"]


def test_silencing_one_key_leaves_the_others(store, monkeypatch):
    from loki.core import providers
    monkeypatch.setattr(providers, "current", lambda: providers.ALL["groq"])
    monkeypatch.setattr(providers.ALL["groq"], "available",
                        lambda: (False, "no key"))
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    nudge.silence("goal:g1")
    assert _keys(nudge.watch()) == ["provider:groq"]


def test_unsilencing_brings_it_back(store):
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    nudge.silence("goal:g1")
    assert nudge.watch() == []
    nudge.silence("goal:g1", False)
    assert _keys(nudge.watch()) == ["goal:g1"]


def test_off_stops_the_push_but_not_the_pane(store):
    """`!nudge off` is about being interrupted. Suggestions are pulled — you
    opened the pane — so they keep working."""
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    nudge.set_enabled(False)
    assert nudge.watch() == []
    assert any(n["key"] == "goal:g1" for n in nudge.suggest())


def test_a_burst_is_capped(store, monkeypatch):
    for i in range(5):
        goals.add(f"objective {i}")
        _age_goal(f"g{i + 1}", nudge.STALE_GOAL_H + 1, store)
    assert len(nudge.watch()) == nudge.MAX_PUSH


def test_a_corrupt_state_file_does_not_wedge_it(store):
    (store / "nudges.json").write_text("not json{", encoding="utf-8")
    assert nudge.enabled() is True
    assert nudge.watch() == []


# ── suggestions ──────────────────────────────────────────────────────────────
def test_an_empty_install_still_offers_openings(store):
    """The pane is never blank — a blank pane is the problem it exists to fix."""
    items = nudge.suggest()
    assert items and all(n["key"].startswith("default:") for n in items)


def test_open_goals_reach_the_pane(store):
    goals.add("ship v1.10")
    assert "goal:g1" in _keys(nudge.suggest())


def test_this_conversation_s_goals_come_first(store):
    goals.add("someone else's objective")
    goals.add("mine", session_key="dm:D1")
    assert _keys(nudge.suggest("dm:D1"))[0] == "goal:g2"


def test_a_live_condition_outranks_a_goal(store, monkeypatch):
    """If the provider is down, that is the most useful thing Loki can say
    before you have typed anything."""
    from loki.core import providers
    monkeypatch.setattr(providers, "current", lambda: providers.ALL["groq"])
    monkeypatch.setattr(providers.ALL["groq"], "available",
                        lambda: (False, "no key"))
    goals.add("ship v1.10")
    assert _keys(nudge.suggest())[0] == "provider:groq"


def test_suggestions_are_capped_and_deduped(store):
    for i in range(8):
        goals.add(f"objective {i}")
    keys = _keys(nudge.suggest())
    assert len(keys) == nudge.MAX_SUGGESTIONS == len(set(keys))


def test_a_stale_goal_is_not_listed_twice(store):
    """It is both a live condition and an open goal — it is still one chip."""
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert _keys(nudge.suggest()).count("goal:g1") == 1


def test_every_suggestion_carries_a_prompt(store):
    goals.add("ship v1.10")
    assert all(n["label"] and n["prompt"] for n in nudge.suggest())


def test_defaults_leak_nothing_about_the_owner(store):
    """What a guest sees when they open an assistant thread."""
    goals.add("migrate the billing schema before friday")
    text = json.dumps(nudge.defaults(), ensure_ascii=False)
    assert "billing" not in text


# ── the command ──────────────────────────────────────────────────────────────
def test_command_toggles(store):
    from loki.core import commands
    commands.nudge_cmd("off")
    assert nudge.enabled() is False
    commands.nudge_cmd("on")
    assert nudge.enabled() is True


def test_command_silences_one_key(store):
    from loki.core import commands
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    commands.nudge_cmd("off goal:g1")
    assert nudge.silenced() == ["goal:g1"]
    assert nudge.watch() == []


def test_command_now_shows_live_conditions_without_consuming_them(store):
    from loki.core import commands
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    assert "goal:g1" in commands.nudge_cmd("now")
    assert _keys(nudge.watch()) == ["goal:g1"]      # `now` did not spend it


def test_command_status_reports_quiet_keys(store):
    from loki.core import commands
    goals.add("ship v1.10")
    _age_goal("g1", nudge.STALE_GOAL_H + 1, store)
    nudge.watch()
    assert "goal:g1" in commands.nudge_cmd("")


def test_the_command_is_owner_only_and_unshadowable(store):
    from loki.core import alias, commands, registry
    assert commands.handle("!nudge", {"is_owner": False}) is None
    assert registry.taken("nudge") and registry.taken("넛지")
    assert alias.add("nudge", "something").startswith("shadowed:")


def test_korean_spellings(store):
    from loki.core import commands
    commands.nudge_cmd("끄기")
    assert nudge.enabled() is False
    commands.nudge_cmd("켜기")
    assert nudge.enabled() is True


# ── the Slack surface ────────────────────────────────────────────────────────
# Bolt fills a listener's parameters by *name* from a fixed set. A name outside
# it is not an error: Bolt logs a warning and injects `None`, so the listener
# runs with a hole in it. That is how the three nudge buttons all behaved like
# "Later" in production — `action_id` arrived as None and fell to the else.
# Every name below is one Bolt actually injects.
BOLT_ARGS = {"ack", "body", "client", "logger", "event", "action", "payload",
             "context", "say", "respond", "request", "req", "response", "resp",
             "next", "next_", "message", "command", "view", "shortcut",
             "options", "step", "error"}


class _FakeApp:
    """Records what `register` wires up, without touching Slack."""

    def __init__(self):
        self.events, self.actions = {}, {}

    def event(self, name):
        def deco(fn):
            self.events[name] = fn
            return fn
        return deco

    def action(self, action_id):
        def deco(fn):
            self.actions[action_id] = fn
            return fn
        return deco


class _FakeClient:
    def __init__(self):
        self.updated, self.prompts = [], []

    def chat_update(self, **kw):
        self.updated.append(kw)

    def assistant_threads_setSuggestedPrompts(self, **kw):
        self.prompts.append(kw)


@pytest.fixture
def wired(store):
    from loki.platforms.slack import assistant
    app = _FakeApp()
    assistant.register(app, "UOWNER", lambda c, ts: "dm:D1")
    return app, assistant


def _tap_body(action_id, nudge_item, user="UOWNER"):
    import json as _json
    return {"user": {"id": user},
            "channel": {"id": "D1"}, "message": {"ts": "1.1"},
            "actions": [{"action_id": action_id,
                         "value": _json.dumps({"k": nudge_item["key"],
                                               "l": nudge_item["label"],
                                               "p": nudge_item["prompt"]})}]}


def test_every_listener_signature_is_one_bolt_can_fill(wired):
    """The regression that shipped: an extra parameter is silently None."""
    import inspect
    app, _ = wired
    for name, fn in {**app.events, **app.actions}.items():
        params = set(inspect.signature(fn).parameters)
        assert params <= BOLT_ARGS, f"{name}: {params - BOLT_ARGS}"


def test_go_runs_the_prompt_and_retires_the_buttons(wired, monkeypatch):
    app, assistant = wired
    goals.add("ship v1.10")
    n = nudge.suggest()[0]
    sent = []
    monkeypatch.setattr(assistant.jobs, "submit", sent.append)
    client = _FakeClient()
    app.actions[assistant.GO](lambda **kw: None, _tap_body(assistant.GO, n), client)
    assert sent and sent[0]["text"] == n["prompt"]
    assert sent[0]["user"] == "UOWNER" and sent[0]["kind"] == "nudge"
    assert client.updated and client.updated[0]["blocks"] == []


def test_later_runs_nothing(wired, monkeypatch):
    app, assistant = wired
    goals.add("ship v1.10")
    n = nudge.suggest()[0]
    sent = []
    monkeypatch.setattr(assistant.jobs, "submit", sent.append)
    client = _FakeClient()
    app.actions[assistant.LATER](lambda **kw: None,
                                 _tap_body(assistant.LATER, n), client)
    assert sent == []
    assert client.updated and nudge.silenced() == []


def test_stop_these_silences_that_one_key(wired, monkeypatch):
    app, assistant = wired
    goals.add("ship v1.10")
    n = nudge.suggest()[0]
    sent = []
    monkeypatch.setattr(assistant.jobs, "submit", sent.append)
    app.actions[assistant.OFF](lambda **kw: None,
                               _tap_body(assistant.OFF, n), _FakeClient())
    assert sent == []
    assert nudge.silenced() == [n["key"]]


def test_a_stranger_tapping_does_nothing(wired, monkeypatch):
    """The buttons sit in the owner's DM, but a payload is just JSON."""
    app, assistant = wired
    goals.add("ship v1.10")
    n = nudge.suggest()[0]
    sent = []
    monkeypatch.setattr(assistant.jobs, "submit", sent.append)
    client = _FakeClient()
    app.actions[assistant.GO](lambda **kw: None,
                              _tap_body(assistant.GO, n, user="UGUEST"), client)
    assert sent == [] and client.updated == []


def test_a_mangled_payload_does_not_raise(wired, monkeypatch):
    app, assistant = wired
    sent = []
    monkeypatch.setattr(assistant.jobs, "submit", sent.append)
    body = {"user": {"id": "UOWNER"}, "channel": {"id": "D1"},
            "message": {"ts": "1.1"},
            "actions": [{"action_id": assistant.GO, "value": "not json{"}]}
    app.actions[assistant.GO](lambda **kw: None, body, _FakeClient())
    assert sent == []                       # no prompt to run, so nothing ran


def test_opening_a_thread_offers_prompts(wired):
    app, assistant = wired
    goals.add("ship v1.10")
    client = _FakeClient()
    app.events["assistant_thread_started"](
        {}, {"assistant_thread": {"channel_id": "D1", "thread_ts": "1.1",
                                  "user_id": "UOWNER"}}, client)
    prompts = client.prompts[0]["prompts"]
    assert 0 < len(prompts) <= nudge.MAX_SUGGESTIONS
    assert all(p["title"] and p["message"] for p in prompts)
    assert any("ship v1.10" in p["title"] for p in prompts)


def test_a_guest_opening_a_thread_sees_nothing_of_yours(wired):
    app, assistant = wired
    goals.add("migrate the billing schema before friday")
    client = _FakeClient()
    app.events["assistant_thread_started"](
        {}, {"assistant_thread": {"channel_id": "C1", "thread_ts": "1.1",
                                  "user_id": "UGUEST"}}, client)
    assert "billing" not in str(client.prompts)


def test_a_thread_event_missing_its_ids_is_ignored(wired):
    app, assistant = wired
    client = _FakeClient()
    app.events["assistant_thread_started"]({}, {"assistant_thread": {}}, client)
    assert client.prompts == []
