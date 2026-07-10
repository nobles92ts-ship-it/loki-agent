"""`!learn` capture — the owner's learnings inbox.

Notes land in ``state/learnings.md`` (private: the state folder is never
guest-visible). Feed the inbox into whatever memory/review process you run —
e.g. a weekly consolidation — then archive or delete consumed items.
"""
from __future__ import annotations

import threading
import time

from . import config

LEARN_FILE = config.STATE / "learnings.md"
HEADER = (
    "# Loki learnings inbox\n\n"
    "Captured with `!learn`. Feed these into your memory/review process,\n"
    "then archive or delete consumed items.\n\n"
)

_lock = threading.Lock()


def capture(text: str) -> int:
    """Append one note. Returns how many items are now waiting in the inbox."""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    body = (text or "").strip().replace("\n", "\n  ")   # keep list structure
    with _lock:
        try:
            existing = LEARN_FILE.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        if not existing.strip():
            existing = HEADER
        if not existing.endswith("\n"):
            existing += "\n"
        existing += f"- [{stamp}] {body}\n"
        LEARN_FILE.write_text(existing, encoding="utf-8")
        return sum(1 for line in existing.splitlines()
                   if line.startswith("- ["))
