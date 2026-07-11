"""Guest rate limiting — a rolling-hour cap per non-owner user.

Protects the owner's subscription: each guest gets at most
``GUEST_RATE_PER_HOUR`` requests in any 60-minute window. Owners are never
throttled (the adapter only calls this for guests). Counts persist in
``state/ratelimit.json`` so a restart doesn't wipe an abuser's tally.
"""
from __future__ import annotations

import json
import threading
import time

from . import config

RATE_FILE = config.STATE / "ratelimit.json"
WINDOW_SEC = 3600

_lock = threading.Lock()


def check(user_id: str) -> tuple[bool, int]:
    """Record an attempt. Returns (allowed, retry_after_minutes).

    Disabled (allow-all) when GUEST_RATE_PER_HOUR is 0."""
    limit = config.GUEST_RATE_PER_HOUR
    if limit <= 0 or not user_id:
        return True, 0
    now = time.time()
    with _lock:
        data = _load()
        hits = [t for t in data.get(user_id, []) if now - t < WINDOW_SEC]
        if len(hits) >= limit:
            remaining = WINDOW_SEC - (now - hits[0])  # until the oldest ages out
            retry = max(1, (int(remaining) + 59) // 60)  # ceil to minutes (≤60)
            data[user_id] = hits                      # prune, don't count this one
            _save(data)
            return False, retry
        hits.append(now)
        data[user_id] = hits
        _prune_idle(data, now)
        _save(data)
        return True, 0


def _prune_idle(data: dict, now: float) -> None:
    for uid in [u for u, ts in data.items()
                if not any(now - t < WINDOW_SEC for t in ts)]:
        del data[uid]


def _load() -> dict:
    try:
        return json.loads(RATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        RATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        config.log.exception("ratelimit.json write failed")
