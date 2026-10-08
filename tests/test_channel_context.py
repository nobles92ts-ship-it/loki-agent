"""Slack timestamps — `conversations.history` is silent about a bad `oldest`.

Hand it more than 6 decimal places and it answers `ok: true` with an empty
message list rather than an error, so channel context and `!summary` run on an
empty history and nothing in the log says why. Reported as #3.
"""
import time

from tests.conftest import event

# NOTE: no module-level adapter import — that would pull the real slack_bolt in
# before conftest can stub it. The `adapter` fixture hands over the module.


def _decimals(ts: str) -> int:
    return len(ts.split(".")[1]) if "." in ts else 0


def test_slack_ts_never_exceeds_six_decimals(adapter):
    # The values `str()` renders with 7+ decimals — the ones that broke it.
    for epoch in (1786089257.1934988, 1700000000.0000001, 0.1234567891,
                  time.time(), time.time() - 7 * 86400):
        assert _decimals(adapter.slack_ts(epoch)) <= 6


def test_str_float_would_have_been_rejected():
    """Guards the premise: this is a real shape `str()` produces, not a theory."""
    assert _decimals(str(1786089257.1934988)) > 6


def test_channel_context_sends_a_six_decimal_oldest(adapter):
    adapter._channel_context("C123")
    calls = adapter.app.client.of("conversations_history")
    assert calls, "no history call was made"
    assert _decimals(calls[0]["oldest"]) <= 6


# ── the 10,000-char budget ───────────────────────────────────────────────────
def test_over_budget_the_oldest_lines_go_whole(adapter, monkeypatch):
    """A bare mention is about what was just said, so past the budget the
    oldest lines are the ones that go — whole, never cut mid-message.

    The cap used to slice the joined text, which kept the oldest 10,000 chars:
    in a busy channel the talk right before the mention was cut, silently.
    """
    # 40 messages of ~330 chars as lines: ~13,000 chars, past the budget
    msgs = [event(text=f"m{i:02d} {'x' * 300} m{i:02d}.", channel="C1",
                  ts=f"{1790000000 + 60 * i}.000000") for i in range(40)]
    monkeypatch.setattr(adapter.app.client, "conversations_history",
                        lambda **kw: {"ok": True, "messages": msgs[::-1]},
                        raising=False)               # the API is newest-first
    ctx = adapter._channel_context("C1")
    lines = ctx.split("\n")
    assert len(ctx) <= 10_000
    assert msgs[-1]["text"] in ctx                   # the newest survives
    assert msgs[0]["text"] not in ctx                # the oldest is what goes
    bodies = [line.partition("] ")[2] for line in lines]
    assert bodies == [m["text"] for m in msgs[-len(lines):]]   # whole, in order
    assert len(ctx) + 1 + len(lines[0]) > 10_000     # one more line would not fit
