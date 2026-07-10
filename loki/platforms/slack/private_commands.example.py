"""Optional private commands — copy to `private_commands.py` to activate.

`private_commands.py` is gitignored: it's where you wire heavy, workspace-
specific `!commands` (a QA pipeline, a deploy, a report generator) that shouldn't
live in the public repo. If the file exists, the Slack adapter imports it and
calls `try_handle(ctx)` on every message BEFORE normal dispatch.

Contract:
  try_handle(ctx: dict) -> bool
    ctx = {app, event, text, user, channel, thread, is_owner, post}
    return True  → you handled it; the adapter stops here
    return False → not yours; normal dispatch continues

Rules of thumb (see docs/EXAMPLES.md):
  • Gate to the owner or a NAMED trusted-user allowlist from .env —
    never pattern-match permission out of free text.
  • Run long jobs on your own daemon thread so you don't block the queue.
  • Post progress with ctx["post"](channel, thread, text).
"""
from __future__ import annotations

import os
import re
import threading
import time

from ...core import brain, config

# Named trusted users (comma-separated Slack ids in .env) — never free-text matched.
REPORT_USERS = {u.strip() for u in os.environ.get("REPORT_USERS", "").split(",") if u.strip()}
_CMD_RE = re.compile(r"^!report\s+(\S+)", re.IGNORECASE)
_lock = threading.Lock()


def try_handle(ctx: dict) -> bool:
    m = _CMD_RE.match(ctx["text"])
    if not m:
        return False
    post, channel, thread = ctx["post"], ctx["channel"], ctx["thread"]
    if not (ctx["is_owner"] or ctx["user"] in REPORT_USERS):
        post(channel, thread, "⛔ You're not allowed to run !report.")
        return True
    if not _lock.acquire(blocking=False):
        post(channel, thread, "⏳ A report is already running — try again when it finishes.")
        return True
    post(channel, thread, "🚀 Generating the report… I'll stream progress here.")
    threading.Thread(target=_run, args=(post, channel, thread, m.group(1)),
                     daemon=True).start()
    return True


def _run(post, channel: str, thread: str, target: str) -> None:
    """Example: drive a Claude Code skill in write mode with a long timeout."""
    try:
        prompt = f"Run my weekly-report skill for {target} and summarize the result."
        # brain.run_claude runs one claude -p; for streaming progress see the
        # stream-json pattern in docs/EXAMPLES.md.
        res = brain.run_claude(prompt, None, "bypassPermissions")
        post(channel, thread, res["text"] if not res["error"]
             else f"⚠️ Report failed: {res['reason']}")
    finally:
        _lock.release()
