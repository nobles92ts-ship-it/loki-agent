"""Bot triggers — which other apps are allowed to wake Loki.

Ignoring every bot message is what makes bot-to-bot loops impossible, so that
stays the default and the allowlist is deliberately narrow. An allowlisted bot
can trigger Loki **only inside an auto-listen zone** — the one surface the
owner has already opted into — and it arrives as a guest: read-only, scoped to
the shared allowlist, rate-limited under its own bot id, and silent in blocked
channels.

Loki's own ids can never be allowlisted. That check lives in `is_allowed` and
runs before the allowlist is consulted, so no amount of editing state can make
Loki answer itself.

Bot output is untrusted text from outside the workspace's people — a CI job
prints whatever a branch name says. It reaches the brain through the same
injection guard as any other context, and under the guest fence, so the worst
a hostile build log can do is what a guest in that channel could already do.
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import config
from .config import log

ALLOW_FILE = config.STATE / "bots_allowed.json"

BOT_ID_RE = re.compile(r"^B[A-Z0-9]{4,}$")
TEXT_MAX = 4000          # CI bots dump whole logs; the prompt stays bounded
MAX_ATTACHMENTS = 5
SEEN_MAX = 20            # recently observed bots, for `!bot seen`

_lock = threading.Lock()
_seen: dict[str, dict] = {}


# ── allowlist ────────────────────────────────────────────────────────────────
def _load() -> list[str]:
    try:
        data = json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
        return [b for b in data if isinstance(b, str) and BOT_ID_RE.match(b)]
    except Exception:
        return []


def _save(ids: list[str]) -> None:
    try:
        ALLOW_FILE.write_text(json.dumps(sorted(set(ids))), encoding="utf-8")
    except Exception:
        log.exception("bots_allowed.json write failed")


def allowed() -> list[str]:
    with _lock:
        return sorted(set(_load()))


def allow(bot_id: str) -> bool:
    bot_id = (bot_id or "").strip().upper()
    if not BOT_ID_RE.match(bot_id):
        return False
    with _lock:
        ids = _load()
        if bot_id in ids:
            return False
        ids.append(bot_id)
        _save(ids)
    log.info("bot allowed: %s", bot_id)
    return True


def deny(bot_id: str) -> bool:
    bot_id = (bot_id or "").strip().upper()
    with _lock:
        ids = _load()
        if bot_id not in ids:
            return False
        _save([b for b in ids if b != bot_id])
    log.info("bot denied: %s", bot_id)
    return True


# ── event inspection ─────────────────────────────────────────────────────────
def identity(event: dict) -> tuple[str, str]:
    """(bot_id, display name) for a bot message; ('', '') when it isn't one."""
    bot_id = (event.get("bot_id") or "").strip().upper()
    if not bot_id:
        return "", ""
    profile = event.get("bot_profile") or {}
    name = (profile.get("name") or event.get("username")
            or (event.get("app_id") or "") or bot_id)
    return bot_id, str(name)[:40]


def is_allowed(event: dict, self_ids: set | None = None) -> bool:
    """True only for an allowlisted bot that is definitely not Loki itself."""
    bot_id, _ = identity(event)
    if not bot_id:
        return False
    mine = {i for i in (self_ids or set()) if i}
    # Hard loop guard, checked before the allowlist so it can't be edited away.
    if bot_id in mine or (event.get("user") or "") in mine:
        return False
    return bot_id in allowed()


def observe(event: dict) -> None:
    """Remember a bot seen posting in a zone so `!bot seen` can list its id."""
    bot_id, name = identity(event)
    if not bot_id:
        return
    with _lock:
        _seen[bot_id] = {"id": bot_id, "name": name,
                         "channel": event.get("channel") or "",
                         "ts": time.time()}
        if len(_seen) > SEEN_MAX:                  # drop the oldest
            oldest = min(_seen.values(), key=lambda s: s["ts"])["id"]
            _seen.pop(oldest, None)


def seen() -> list[dict]:
    """Recently observed bots, newest first, each flagged with its status."""
    live = set(allowed())
    with _lock:
        items = sorted(_seen.values(), key=lambda s: -s["ts"])
    return [dict(s, allowed=s["id"] in live) for s in items]


def message_text(event: dict, limit: int = TEXT_MAX) -> str:
    """The readable content of a bot message.

    Many bots (CI, GitHub, alerting) put nothing in `text` and everything in
    attachment fallbacks, so a text-only read would see an empty message.
    """
    parts = [(event.get("text") or "").strip()]
    for att in (event.get("attachments") or [])[:MAX_ATTACHMENTS]:
        if not isinstance(att, dict):
            continue
        # `fallback` is Slack's own plain-text summary of the attachment; when
        # a bot omits it, the visible fields together say the same thing.
        fallback = (att.get("fallback") or "").strip()
        if fallback:
            parts.append(fallback)
            continue
        parts += [(att.get(k) or "").strip() for k in ("pretext", "title", "text")]
    return "\n".join(p for p in parts if p)[:limit].strip()
