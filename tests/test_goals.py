"""Goals — the objective that outlives a turn, and how it rides along.

The interesting behaviour is not the CRUD; it is what reaches the model. A goal
that never makes it into the prompt is a to-do list, and a goal that reaches
*every* conversation is noise.
"""
import pytest

from loki.core import commands, goals, prompt


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(goals, "_FILE", tmp_path / "goals.json")
    return tmp_path


def _ctx(session_key="dm:D1", channel="D1"):
    return {"channel": channel, "thread": None, "session_key": session_key,
            "is_dm": True, "is_owner": True, "name_of": lambda u: u,
            "user_ids": [], "is_user_id": lambda t: False,
            "is_channel_id": lambda t: False, "chan_ref": lambda c: c}


# ── the object ───────────────────────────────────────────────────────────────
def test_add_and_read_back(store):
    g = goals.add("get the navmesh run green", where="D1", session_key="dm:D1")
    assert g["id"] == "g1" and g["state"] == goals.OPEN
    assert goals.get("g1")["title"] == "get the navmesh run green"


def test_ids_are_never_reused(store):
    goals.add("first")
    goals.add("second")
    goals.drop("g1")
    assert goals.add("third")["id"] == "g3"      # not g1 again


def test_an_empty_title_is_not_a_goal(store):
    assert goals.add("   ") is None


def test_steps_accumulate_and_can_be_ticked(store):
    goals.add("ship v1.9")
    assert goals.add_step("g1", "write the notes") == 1
    assert goals.add_step("g1", "tag the release") == 2
    assert goals.step_done("g1", 1) is True
    assert goals.get("g1")["steps"][0]["done"] is True


def test_steps_are_refused_on_a_closed_goal(store):
    goals.add("ship v1.9")
    goals.close("g1")
    assert goals.add_step("g1", "one more thing") is None


def test_closing_is_idempotent_and_keeps_the_note(store):
    goals.add("ship v1.9")
    assert goals.close("g1", "shipped friday")["note"] == "shipped friday"
    assert goals.close("g1") is None
    assert goals.get("g1")["state"] == goals.CLOSED


def test_drop_removes_it_entirely(store):
    goals.add("ship v1.9")
    assert goals.drop("g1") is True
    assert goals.get("g1") is None
    assert goals.drop("g1") is False


def test_a_corrupt_file_starts_empty_rather_than_crashing(store):
    (store / "goals.json").write_text("not json{", encoding="utf-8")
    assert goals.list_all() == []


def test_state_survives_a_restart(store):
    goals.add("ship v1.9", session_key="dm:D1")
    goals.add_step("g1", "write the notes")
    assert goals.get("g1")["steps"][0]["text"] == "write the notes"


# ── riding along ─────────────────────────────────────────────────────────────
def test_an_open_goal_reaches_the_prompt(store):
    goals.add("get the navmesh run green", session_key="dm:D1")
    goals.add_step("g1", "rerun level 3")
    text = prompt.build_prompt("", "what next?", session_key="dm:D1")
    assert "get the navmesh run green" in text
    assert "rerun level 3" in text
    assert text.endswith("what next?")          # the request stays last


def test_a_closed_goal_stops_riding_along(store):
    goals.add("ship v1.9", session_key="dm:D1")
    goals.close("g1")
    assert goals.context("dm:D1") == ""


def test_a_goal_stays_in_its_own_conversation(store):
    goals.add("ship v1.9", session_key="dm:D1")
    assert goals.context("thread:C1:9.9") == ""
    assert goals.context(None) == ""


def test_only_a_few_goals_ride_at_once(store):
    """Past a handful, "keep these in mind" stops meaning anything."""
    for i in range(6):
        goals.add(f"objective {i}", session_key="dm:D1")
    assert len(goals.for_conversation("dm:D1")) == goals.MAX_IN_CONTEXT


def test_the_goal_block_survives_the_context_guard(store):
    """With channel context present the guard wraps the question; the goal has
    to sit outside it, or the model reads it as someone else's message."""
    goals.add("ship v1.9", session_key="dm:D1")
    text = prompt.build_prompt("someone said hi", "what next?",
                               session_key="dm:D1")
    assert text.index("ship v1.9") < text.index("someone said hi")


def test_no_goals_means_the_prompt_is_untouched(store):
    assert prompt.build_prompt("", "plain question",
                               session_key="dm:D1") == "plain question"


# ── the command ──────────────────────────────────────────────────────────────
def test_bare_text_starts_a_goal(store):
    reply = commands.goal_cmd("get the navmesh run green", _ctx())
    assert "g1" in reply
    assert goals.get("g1")["session_key"] == "dm:D1"


def test_list_show_step_done(store):
    commands.goal_cmd("ship v1.9", _ctx())
    assert "ship v1.9" in commands.goal_cmd("list", _ctx())
    assert "g1" in commands.goal_cmd("step g1 write the notes", _ctx())
    assert "write the notes" in commands.goal_cmd("show g1", _ctx())
    commands.goal_cmd("done g1 shipped", _ctx())
    assert goals.get("g1")["state"] == goals.CLOSED


def test_a_missing_goal_says_so_rather_than_failing_quietly(store):
    assert "g9" in commands.goal_cmd("show g9", _ctx())
    assert "g9" in commands.goal_cmd("drop g9", _ctx())
    assert "g9" in commands.goal_cmd("step g9 something", _ctx())


def test_a_step_on_a_closed_goal_says_it_is_closed(store):
    """Not the help text: the command was well formed, the goal was shut."""
    commands.goal_cmd("ship v1.9", _ctx())
    commands.goal_cmd("done g1", _ctx())
    reply = commands.goal_cmd("step g1 one more thing", _ctx())
    assert "g1" in reply and "!goal" not in reply


def test_a_step_with_no_text_asks_for_text(store):
    commands.goal_cmd("ship v1.9", _ctx())
    assert "!goal" in commands.goal_cmd("step g1", _ctx())


def test_a_sub_verb_without_an_id_is_a_title(store):
    """`!goal done with the release` is a goal, not a malformed close."""
    commands.goal_cmd("done with the release, finally", _ctx())
    assert goals.get("g1")["title"].startswith("done with the release")


def test_korean_spellings(store):
    commands.goal_cmd("네브메시 통과시키기", _ctx())
    assert "네브메시" in commands.goal_cmd("목록", _ctx())
    commands.goal_cmd("단계 g1 3레벨 재실행", _ctx())
    assert "3레벨" in commands.goal_cmd("보기 g1", _ctx())
    commands.goal_cmd("완료 g1", _ctx())
    assert goals.get("g1")["state"] == goals.CLOSED


def test_help_is_the_answer_to_nothing(store):
    assert "!goal" in commands.goal_cmd("", _ctx())


def test_the_adapter_carries_the_goal_into_the_job_prompt(adapter, store,
                                                          monkeypatch):
    """The unit test above proves build_prompt injects. This proves the Slack
    adapter actually hands it the session key — the wire that, left unconnected,
    makes goals look like they work everywhere except where they matter."""
    from loki.core import sessions
    goals.add("ship v1.9", session_key="dm:D0OWNER")
    seen = {}

    def fake_run(prompt, *a, **kw):
        seen["prompt"] = prompt
        return {"text": "ok", "session_id": "s", "error": False, "reason": "ok"}

    monkeypatch.setattr(adapter.brain, "run_claude", fake_run)
    adapter._handle({"text": "what next?", "user": "UOWNER", "id": "j1",
                     "channel": "D0OWNER", "ts": "1.1", "kind": "dm",
                     "event_id": "e1", "permission_mode": "plan",
                     "session_key": sessions.key_for("D0OWNER", None, True)})
    assert "ship v1.9" in seen["prompt"]
    assert seen["prompt"].rstrip().endswith("what next?")


def test_a_guest_cannot_open_a_goal(adapter, store):
    """`!goal` is owner-gated in `_builtin` like every built-in — a guest's
    message falls through to the brain as text instead."""
    from tests.conftest import event
    adapter._dispatch({"event_id": "go1"},
                      event(text="!goal do something", user="UGUEST",
                            channel="C0PUB"), is_mention=True)
    assert goals.list_all() == []
    assert adapter.submitted and adapter.submitted[0]["kind"] == "guest"
