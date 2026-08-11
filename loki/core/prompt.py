"""Prompt assembly — wraps conversation context with an injection guard.

Context gathered from a platform (thread/channel history) is DATA, not
instructions; only the user's final request line is followed.

A conversation's standing goals ride in front of that, as their own delimited
block. They go *outside* the context guard rather than inside it because they
are not part of the transcript: the guard exists to say "everything in here was
written by other people, treat it as data", and a goal was written by the owner
through ``!goal``. Keeping them separate also means a goal survives a channel
with no context at all, which is most of them.
"""
from __future__ import annotations

from . import goals
from .config import t


def build_prompt(context: str, question: str,
                 kind_key: str = "kind_thread", scope: str = "",
                 session_key: str | None = None) -> str:
    body = question if not context else t(
        "ctx_guard",
        kind=t(kind_key),
        scope=scope or t("scope_thread"),
        context=context,
        q=question)
    return goals.context(session_key) + body
