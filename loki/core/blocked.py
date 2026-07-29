"""Blocked channels — the owner's opt-out switch.

Every channel Loki is invited to is usable by default (guests read-only,
rate-limited, inside the shared scope). When one turns out to be a bad fit,
the owner shuts it off from their DM with ``!block <channel_id>`` and reopens
it with ``!unblock``. Persists to ``state/blocked_channels.json``.

Opt-out rather than opt-in is deliberate: an approval queue meant every new
channel sat mute until the owner noticed, which read as "the bot is broken".
"""
from __future__ import annotations

import json
import threading

from . import config
from .config import log

_FILE = config.STATE / "blocked_channels.json"
_lock = threading.Lock()


def _load() -> set[str]:
    try:
        return set(json.loads(_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


_state: set[str] = _load()


def _save() -> None:
    try:
        _FILE.write_text(json.dumps(sorted(_state)), encoding="utf-8")
    except Exception:
        log.exception("blocked_channels.json write failed")


def is_blocked(channel_id: str) -> bool:
    with _lock:
        return channel_id in _state


def set_blocked(channel_id: str, blocked: bool) -> None:
    with _lock:
        (_state.add if blocked else _state.discard)(channel_id)
        _save()
