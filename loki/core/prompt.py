"""Prompt assembly — wraps conversation context with an injection guard.

Context gathered from a platform (thread/channel history) is DATA, not
instructions; only the user's final request line is followed.

A conversation's standing goals ride in front of that, as their own delimited
block. They go *outside* the context guard rather than inside it because they
are not part of the transcript: the guard exists to say "everything in here was
written by other people, treat it as data", and a goal was written by the owner
through ``!goal``. Keeping them separate also means a goal survives a channel
with no context at all, which is most of them.

The owner's style note (``<WORK_DIR>/loki/style.md``) rides in front of both,
for the same reason: the owner wrote it, so it is not transcript. It says how to
write an answer, never what to answer, and it reaches every prompt — a guest's
sealed run included, since a sealed run sees nothing but the prompt.
"""
from __future__ import annotations

from pathlib import Path

from . import config, goals
from .config import log, t


def style_path() -> Path:
    return Path(config.WORK_DIR) / "loki" / "style.md"


def style_note() -> str:
    """"" when there is no note. Read on every request, like ``aliases.md``, so
    an edit applies to the next answer without a restart."""
    if not config.WORK_DIR:
        return ""
    path = style_path()
    try:
        text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except Exception:
        log.exception("style.md unreadable — answering without the style note")
        return ""
    return t("style_note", style=text) if text else ""


def build_prompt(context: str, question: str,
                 kind_key: str = "kind_thread", scope: str = "",
                 session_key: str | None = None) -> str:
    body = question if not context else t(
        "ctx_guard",
        kind=t(kind_key),
        scope=scope or t("scope_thread"),
        context=context,
        q=question)
    return style_note() + goals.context(session_key) + body
