"""Slack timestamps — `conversations.history` is silent about a bad `oldest`.

Hand it more than 6 decimal places and it answers `ok: true` with an empty
message list rather than an error, so channel context and `!summary` run on an
empty history and nothing in the log says why. Reported as #3.
"""
import time

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
