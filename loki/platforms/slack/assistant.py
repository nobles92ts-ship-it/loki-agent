"""Slack's assistant surface — the suggestion pane, and Loki speaking first.

Two deliveries for the one noun in :mod:`loki.core.nudge`:

* **the pane.** Slack's assistant container fires ``assistant_thread_started``
  when a thread opens and lets the app post up to four suggested prompts. That
  is the whole point of the feature: the blank composer is replaced by four
  things worth asking, so the owner does not have to arrive knowing the
  vocabulary.
* **the DM.** A poll loop asks :func:`nudge.watch` what just became true and
  sends it with three buttons — run it, park it, or silence that watcher for
  good. Nothing here decides to run anything; the owner's tap does.

Two things this module is careful about.

**Suggestions are not public.** A goal's title is the owner's private note
about their own work, and anyone can open an assistant thread with a bot they
share a workspace with. Non-owners get the standing openings only — useful,
and free of anything Loki knows about its owner.

**The button carries the prompt, not a handle to it.** A handle would have to
survive a worker restart, and the restart is exactly when an alert is most
likely to still be sitting unanswered in the DM.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from ...core import config, jobs, nudge
from ...core.config import log, t

MAX_TITLE = 80          # Slack truncates long suggestion chips; do it ourselves
MAX_VALUE = 1900        # Slack's button value cap is 2000

GO, LATER, OFF = "nudge_go", "nudge_later", "nudge_off"


# ─────────────────────────── the pane ───────────────────────────
def _prompts(session_key: str | None, is_owner: bool) -> list[dict]:
    """Slack's `{title, message}` pairs — owner-private nudges only for them."""
    items = nudge.suggest(session_key) if is_owner else nudge.defaults()
    return [{"title": n["label"][:MAX_TITLE], "message": n["prompt"]}
            for n in items][:nudge.MAX_SUGGESTIONS]


def register(app, owner_id: str, session_key: Callable[[str, str], str | None]) -> None:
    """Wire the assistant events and the three nudge buttons onto `app`."""

    @app.event("assistant_thread_started")
    def _on_thread_started(body, event, client, logger=None):
        thread = (event or {}).get("assistant_thread") or {}
        channel, ts = thread.get("channel_id"), thread.get("thread_ts")
        if not channel or not ts:
            return
        is_owner = thread.get("user_id") == owner_id
        try:
            client.assistant_threads_setSuggestedPrompts(
                channel_id=channel, thread_ts=ts,
                prompts=_prompts(session_key(channel, ts), is_owner))
        except Exception:
            log.exception("setSuggestedPrompts failed")

    @app.event("assistant_thread_context_changed")
    def _on_context_changed(body, event, logger=None):
        """Acked and ignored — Slack sends it on every channel switch, and
        without a handler Bolt logs each one as an unhandled request."""

    def _tap(ack, body, client, logger=None) -> None:
        """One handler for all three buttons.

        Bolt fills a listener's parameters by *name* from its own set, so a
        closure that smuggles the action id in as an extra argument is refused
        at dispatch — `_a is not a valid argument`, and the tap silently does
        nothing. Slack already puts `action_id` in the payload; read it there
        and the signature stays inside what Bolt recognises.
        """
        ack()
        if (body.get("user") or {}).get("id") != owner_id:
            return                      # buttons in the owner's DM, owner only
        action = (body.get("actions") or [{}])[0]
        action_id = action.get("action_id") or ""
        try:
            payload = json.loads(action.get("value") or "{}")
        except Exception:
            payload = {}
        key, label = payload.get("k", ""), payload.get("l", "")
        channel = (body.get("channel") or {}).get("id")
        ts = (body.get("message") or {}).get("ts")

        if action_id == GO and payload.get("p"):
            jobs.submit({
                "channel": channel, "thread": None, "text": payload["p"],
                "user": owner_id, "event_id": f"nudge-{key}-{int(time.time())}",
                "in_thread": False, "is_mention": False,
                "permission_mode": config.PERMISSION_MODE, "kind": "nudge",
                "session_key": session_key(channel, None),
                "reply_prefix": t("nudge_taken", label=label)})
            done = t("nudge_taken", label=label).strip()
        elif action_id == OFF:
            nudge.silence(key, True)
            done = t("nudge_hushed", k=key)
        else:
            done = t("nudge_later_ok")
        if channel and ts:
            try:                        # retire the buttons either way
                client.chat_update(channel=channel, ts=ts, text=done, blocks=[])
            except Exception:
                log.exception("nudge button update failed")

    for action_id in (GO, LATER, OFF):
        app.action(action_id)(_tap)


# ─────────────────────────── speaking first ───────────────────────────
def blocks(n: dict) -> list[dict]:
    """The alert, with its three answers. Needs Interactivity enabled."""
    value = json.dumps({"k": n["key"], "l": n["label"], "p": n["prompt"]},
                       ensure_ascii=False)[:MAX_VALUE]
    button = lambda action_id, key: {                       # noqa: E731
        "type": "button", "action_id": action_id, "value": value,
        "text": {"type": "plain_text", "text": t(key)}}
    return [{"type": "section",
             "text": {"type": "mrkdwn",
                      "text": t("nudge_push", label=n["label"], why=n["why"])}},
            {"type": "actions",
             "elements": [button(GO, "nudge_btn_go"),
                          button(LATER, "nudge_btn_later"),
                          button(OFF, "nudge_btn_off")]}]


def start(app, dm_channel: Callable[[], str | None]) -> None:
    """Start the push loop. Silent when nudges are off or nothing tripped."""

    def _loop() -> None:
        while True:
            time.sleep(nudge.POLL_MIN * 60)     # sleep first: boot is noisy
            try:
                due = nudge.watch()
                if not due:
                    continue
                dm = dm_channel()
                if not dm:
                    continue
                for n in due:
                    app.client.chat_postMessage(
                        channel=dm,
                        text=t("nudge_push", label=n["label"], why=n["why"]),
                        blocks=blocks(n))
                    log.info("nudge fired key=%s", n["key"])
            except Exception:
                log.exception("nudge poll failed")

    threading.Thread(target=_loop, daemon=True).start()
