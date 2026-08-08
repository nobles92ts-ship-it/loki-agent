"""Slack's "Sent via <app>" credit must not reach the command layer.

Posting *through an app* — a Claude session driving Slack from another machine
via a connector — makes Slack append a credit line. It is invisible in the
client but concatenated into `text`, so an anchored command regex misses and
the command falls through to the brain as ordinary chat.

The event bodies here are the real shape, captured from a live round trip
(`!usage` sent through the Claude connector, 2026-08-08).
"""
from tests.conftest import event

TAIL = "*다음을 사용하여 보냄* <@U0AFK1Q8S4W>"


def _via_app(body: str, tail: str = TAIL) -> dict:
    """A message posted through an app: credit in `text` AND its own block."""
    return {
        "text": f"{body} {tail}",
        "app_id": "A08SF47R6P4",
        "blocks": [
            {"type": "rich_text", "elements": [
                {"type": "rich_text_section",
                 "elements": [{"type": "text", "text": body}]}]},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": tail, "verbatim": False}]},
        ],
    }


def _typed(body: str) -> dict:
    """What Slack's own composer produces — rich_text only, no context block."""
    return {"text": body, "blocks": [
        {"type": "rich_text", "elements": [
            {"type": "rich_text_section",
             "elements": [{"type": "text", "text": body}]}]}]}


# ── the helper ───────────────────────────────────────────────────────────────
def test_credit_is_removed(adapter):
    assert adapter.message_text(_via_app("!usage")) == "!usage"
    assert adapter.message_text(_via_app("!account off")) == "!account off"
    assert adapter.message_text(_via_app("!summary C0ABC")) == "!summary C0ABC"


def test_a_typed_message_is_untouched(adapter):
    assert adapter.message_text(_typed("!usage")) == "!usage"
    assert adapter.message_text(_typed("just talking")) == "just talking"


def test_works_in_any_workspace_language(adapter):
    """The credit is localized, so nothing may depend on its wording."""
    for tail in ("*Sent via* <@U0AFK1Q8S4W>",
                 "*Enviado mediante* <@U0AFK1Q8S4W>",
                 "*経由で送信* <@U0AFK1Q8S4W>"):
        assert adapter.message_text(_via_app("!jobs", tail)) == "!jobs"


def test_body_that_ends_in_a_mention_survives(adapter):
    """Only a trailing *context block* is Slack's — a mention the author typed
    is theirs, even though it looks similar at the end of the string."""
    ev = _typed("ping <@U0AGE4NUB62>")
    assert adapter.message_text(ev) == "ping <@U0AGE4NUB62>"


def test_no_blocks_at_all(adapter):
    assert adapter.message_text({"text": "  hi  "}) == "hi"
    assert adapter.message_text({}) == ""


def test_multiline_body_keeps_its_newlines(adapter):
    ev = _via_app("!learn line one\nline two")
    assert adapter.message_text(ev) == "!learn line one\nline two"


# ── through the real dispatch ────────────────────────────────────────────────
def test_command_sent_through_an_app_is_handled_not_chatted(adapter):
    """The bug, end to end: `!usage` used to reach the brain as chat."""
    ev = event(text="")                       # conftest shape, then the app body
    ev.update(_via_app("!usage"))
    adapter._dispatch({"event_id": "ap1"}, ev, is_mention=False)
    assert adapter.submitted == []            # not queued as a chat request
    assert adapter.app.client.texts()         # answered by the command layer


def test_alias_arguments_are_not_polluted(adapter):
    ev = event(text="")
    ev.update(_via_app("!standup"))
    adapter.alias.aliases_file().parent.mkdir(parents=True, exist_ok=True)
    adapter.alias.aliases_file().write_text(
        "## Aliases\n- standup: Summarize {args} please\n", encoding="utf-8")
    adapter.alias._invalidate()
    adapter._dispatch({"event_id": "ap2"}, ev, is_mention=False)
    assert adapter.submitted, "the alias did not fire"
    assert "다음을 사용하여 보냄" not in adapter.submitted[0]["text"]
