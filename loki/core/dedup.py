"""Event dedup — platforms redeliver events; process each one exactly once."""
from __future__ import annotations

import json
import threading
import time

from .config import STATE, log

SEEN_FILE = STATE / "seen.json"
SEEN_TTL = 3600            # dedup memory (seconds)

_seen_lock = threading.Lock()


def already_seen(event_id: str) -> bool:
    """True if this event_id was handled before (dedup platform retries)."""
    if not event_id:
        return False
    now = time.time()
    with _seen_lock:
        try:
            seen = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            seen = {}
        seen = {k: v for k, v in seen.items() if now - v < SEEN_TTL}  # prune
        hit = event_id in seen
        seen[event_id] = now
        try:
            SEEN_FILE.write_text(json.dumps(seen), encoding="utf-8")
        except Exception:
            log.exception("seen.json write failed")
        return hit
