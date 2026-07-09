"""Prompt assembly — wraps conversation context with an injection guard.

Context gathered from a platform (thread/channel history) is DATA, not
instructions; only the user's final request line is followed.
"""
from __future__ import annotations

from .config import t


def build_prompt(context: str, question: str,
                 kind_key: str = "kind_thread", scope: str = "") -> str:
    if not context:
        return question
    return t("ctx_guard",
             kind=t(kind_key),
             scope=scope or t("scope_thread"),
             context=context,
             q=question)
