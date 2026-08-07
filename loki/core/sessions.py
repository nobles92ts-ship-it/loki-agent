"""Conversation → Claude session ids, with idle expiry.

``--resume`` is what makes Loki remember: hand Claude the session id a
conversation used last time and it picks up where it left off. The adapter
decides what counts as one conversation (a DM, a thread) and hands the key
here; this module owns the mapping, its lifetime, and its persistence.

Sessions expire after ``SESSION_IDLE_MIN`` minutes of silence, so a chat
picked up the next morning starts clean instead of dragging yesterday's
context — and yesterday's token cost — behind it. ``!new`` drops one on
demand. State persists to ``state/sessions.json`` so restarting the worker
doesn't wipe an in-flight conversation.
"""
from __future__ import annotations

import json
import threading
import time

from . import config
from .config import log

_FILE = config.STATE / "sessions.json"
_lock = threading.Lock()


def key_for(channel: str, thread: str | None, is_dm: bool) -> str | None:
    """The conversation key for a message — None means always start fresh.

    A thread is one conversation. A DM is one long conversation, so follow-up
    messages continue instead of each starting over. A message at a channel's
    top level deliberately gets no key: several people share a channel, and one
    person's session must not carry into the next person's answer. Adapters
    hand those the channel's recent history as context instead.

    Adapters supply ``is_dm`` because only they know their platform's id
    conventions (Slack's ``D…`` channels, Discord's ``DMChannel``, …).
    """
    if thread:
        return f"thread:{channel}:{thread}"
    if is_dm and channel:
        return f"dm:{channel}"
    return None


def _load() -> dict[str, dict]:
    try:
        raw = json.loads(_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items()
                if isinstance(v, dict) and v.get("sid")}
    except Exception:
        return {}


_state: dict[str, dict] = _load()


def _save() -> None:
    try:
        _FILE.write_text(json.dumps(_state), encoding="utf-8")
    except Exception:
        log.exception("sessions.json write failed")


def _expired(entry: dict, now: float) -> bool:
    idle = config.SESSION_IDLE_MIN * 60
    return idle > 0 and now - entry.get("ts", 0) > idle


def get(key: str | None) -> str | None:
    """The session id to ``--resume`` here, or None to start a fresh one.

    An entry that has gone idle is dropped on the way out, so expiry costs
    nothing extra — the next turn simply starts over.
    """
    if not key:
        return None
    with _lock:
        entry = _state.get(key)
        if not entry:
            return None
        if _expired(entry, time.time()):
            del _state[key]
            _save()
            return None
        return entry.get("sid")


def remember(key: str | None, session_id: str | None) -> None:
    """Store the session Claude just returned and mark the conversation alive."""
    if not key or not session_id:
        return
    now = time.time()
    with _lock:
        _state[key] = {"sid": session_id, "ts": now}
        for stale in [k for k, v in _state.items() if _expired(v, now)]:
            del _state[stale]
        _save()


def reset(key: str | None) -> bool:
    """Forget one conversation (``!new``). True if there was one to forget."""
    if not key:
        return False
    with _lock:
        if _state.pop(key, None) is None:
            return False
        _save()
        return True


def reset_all() -> int:
    """Forget every conversation. Returns how many were dropped.

    For changes that make the remembered sessions wrong rather than stale —
    switching the account a spawn runs as, for one: resuming would replay one
    account's thread under the other's login and quota.
    """
    with _lock:
        n = len(_state)
        if not n:
            return 0
        _state.clear()
        _save()
    return n


def active() -> int:
    """How many conversations are currently remembered (diagnostics)."""
    now = time.time()
    with _lock:
        return sum(1 for v in _state.values() if not _expired(v, now))
