"""`!exit` — end a bot-to-bot exchange here, without closing anything else.

Two Lokis can talk to each other: an allowlisted bot posting inside an
auto-listen zone wakes the other one (see :mod:`loki.core.botallow`). That is
useful and it is also the one path where nobody is deciding when to stop —
neither side gets bored, and an exchange that has stopped being productive
keeps spending both subscriptions.

Every existing off-switch is the wrong size for that:

- ``!stop`` cancels what is running; the next bot message wakes Loki again.
- ``!block`` shuts the whole channel — for people too.
- ``!unlisten`` removes the zone, so humans have to go back to @mentioning.
- ``!new`` clears the conversation's memory but not the relay.

This is the missing one: **bots stop being heard in this thread, people carry
on untouched.** It is deliberately human-only — a bot cannot issue commands
(the adapter routes bot messages straight to the brain), so the other Loki can
never mute yours, and yours can never be talked out of staying quiet.

One side going quiet is enough to end the exchange: with nothing new posted,
there is nothing left to wake the other one.
"""
from __future__ import annotations

import json
import threading

from . import config
from .config import log

_FILE = config.STATE / "bot_muted.json"
_lock = threading.Lock()


def _load() -> dict:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        return {"channels": set(d.get("channels", [])),
                "threads": set(d.get("threads", []))}
    except Exception:
        return {"channels": set(), "threads": set()}


_state = _load()


def _save() -> None:
    try:
        _FILE.write_text(json.dumps(
            {"channels": sorted(_state["channels"]),
             "threads": sorted(_state["threads"])}), encoding="utf-8")
    except Exception:
        log.exception("bot_muted.json write failed")


def _tkey(channel: str, thread_ts: str) -> str:
    return f"{channel}:{thread_ts}"


def is_muted(channel: str, thread_ts: str | None) -> bool:
    """Are bot triggers switched off here? A muted channel covers its threads."""
    with _lock:
        if channel in _state["channels"]:
            return True
        return bool(thread_ts) and _tkey(channel, thread_ts) in _state["threads"]


def add(channel: str, thread_ts: str | None) -> str:
    """Mute this thread, or the whole channel at top level. Returns an i18n key.

    Mirrors ``autolisten.add`` on purpose: same scoping rule, so `!listen` and
    `!exit` never disagree about what "here" means.
    """
    with _lock:
        if thread_ts:
            if channel in _state["channels"] or \
                    _tkey(channel, thread_ts) in _state["threads"]:
                return "exit_already"
            _state["threads"].add(_tkey(channel, thread_ts))
            _save()
            return "exit_thread"
        if channel in _state["channels"]:
            return "exit_already"
        _state["channels"].add(channel)
        _save()
        return "exit_channel"


def remove(channel: str, thread_ts: str | None) -> str:
    """Let bots back in — most specific first, same as ``autolisten.remove``."""
    with _lock:
        if thread_ts and _tkey(channel, thread_ts) in _state["threads"]:
            _state["threads"].discard(_tkey(channel, thread_ts))
            _save()
            return "exit_undo_ok"
        if channel in _state["channels"]:
            _state["channels"].discard(channel)
            _save()
            return "exit_undo_ok"
        return "exit_undo_none"


def snapshot() -> tuple[list, list]:
    """(muted channel ids, muted thread keys) — the state is otherwise invisible."""
    with _lock:
        return sorted(_state["channels"]), sorted(_state["threads"])
