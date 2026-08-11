"""Auto-listen zones — channels/threads Loki answers WITHOUT an @mention.

Owner opt-in via `!listen` (in a thread → that thread; at channel top level →
the whole channel). State persists to state/autolisten.json. Everyone in a zone
reaches the brain (guests stay read-only + rate-limited); the adapter still skips
@mentions inside a zone — those arrive via app_mention — to avoid double-handling.
"""
from __future__ import annotations

import json
import threading

from . import config
from .config import log

_FILE = config.STATE / "autolisten.json"
_lock = threading.Lock()


def _load() -> dict:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        return {"channels": set(d.get("channels", [])),
                "threads": set(d.get("threads", [])),
                "seen": bool(d.get("seen"))}
    except Exception:
        return {"channels": set(), "threads": set(), "seen": False}


_state = _load()


def _save() -> None:
    try:
        _FILE.write_text(json.dumps(
            {"channels": sorted(_state["channels"]),
             "threads": sorted(_state["threads"]),
             "seen": _state.get("seen", False)}), encoding="utf-8")
    except Exception:
        log.exception("autolisten.json write failed")


# ── has a channel message ever reached us? ───────────────────────────────────
# A zone is inert unless the Slack app subscribes to `message.channels` /
# `message.groups`, and nothing says so: `!listen` confirms, the state is
# written, and no message ever arrives. Slack offers no way to read an app's
# event subscriptions with a bot token, so this reports the one thing we can
# actually know — whether a channel message has ever reached this install.
# It latches on the first one and persists, so a quiet channel does not undo it.
def note_channel_event() -> None:
    """Called for every non-DM message event. Cheap after the first."""
    if _state.get("seen"):
        return
    with _lock:
        if _state.get("seen"):
            return
        _state["seen"] = True
        _save()
    log.info("first channel message received — auto-listen zones can fire")


def channel_events_seen() -> bool:
    with _lock:
        return bool(_state.get("seen"))


def _tkey(channel: str, thread_ts: str) -> str:
    return f"{channel}:{thread_ts}"


def is_zone(channel: str, thread_ts: str | None) -> bool:
    """True if this channel (whole-channel zone) or this specific thread is registered."""
    with _lock:
        if channel in _state["channels"]:
            return True
        return bool(thread_ts) and _tkey(channel, thread_ts) in _state["threads"]


def add(channel: str, thread_ts: str | None) -> str:
    """Register the thread (if the command was in one) or the whole channel.
    Returns an i18n message key describing the outcome."""
    with _lock:
        if thread_ts:
            if channel in _state["channels"] or \
                    _tkey(channel, thread_ts) in _state["threads"]:
                return "listen_already"
            _state["threads"].add(_tkey(channel, thread_ts))
            _save()
            return "listen_thread"
        if channel in _state["channels"]:
            return "listen_already"
        _state["channels"].add(channel)
        _save()
        return "listen_channel"


def remove(channel: str, thread_ts: str | None) -> str:
    """Remove the most specific zone: this thread first, else the channel."""
    with _lock:
        if thread_ts and _tkey(channel, thread_ts) in _state["threads"]:
            _state["threads"].discard(_tkey(channel, thread_ts))
            _save()
            return "unlisten_ok"
        if channel in _state["channels"]:
            _state["channels"].discard(channel)
            _save()
            return "unlisten_ok"
        return "unlisten_none"


def snapshot() -> tuple[list, list]:
    """(sorted channel ids, sorted thread keys) for listing."""
    with _lock:
        return sorted(_state["channels"]), sorted(_state["threads"])
