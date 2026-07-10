"""Owner scheduler — recurring/one-shot prompts fired into the job queue.

Owner DM syntax (also `!예약`):
    !schedule daily HH:MM <prompt>
    !schedule weekly <mon..sun> HH:MM <prompt>
    !schedule once YYYY-MM-DD HH:MM <prompt>
    !schedule list · !schedule remove <id>

Fires run at the owner's configured permission level and post back to the DM
where they were created. Times are machine-local. While the worker is down:
recurring schedules roll forward to their next future slot (no catch-up spam);
a missed `once` fires immediately on boot.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable

from . import config
from .config import log

SCHED_FILE = config.STATE / "schedules.json"
POLL_SEC = 20

_lock = threading.Lock()

_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_DOW_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_TIME_RE = r"([01]?\d|2[0-3]):([0-5]\d)"
_DAILY_RE = re.compile(rf"^(?:daily|매일)\s+{_TIME_RE}\s+(.+)$",
                       re.IGNORECASE | re.DOTALL)
_WEEKLY_RE = re.compile(rf"^(?:weekly|매주)\s+(\S+)\s+{_TIME_RE}\s+(.+)$",
                        re.IGNORECASE | re.DOTALL)
_ONCE_RE = re.compile(rf"^once\s+(\d{{4}}-\d{{2}}-\d{{2}})\s+{_TIME_RE}\s+(.+)$",
                      re.IGNORECASE | re.DOTALL)


# ── parsing ──────────────────────────────────────────────────────────────────
def parse(text: str) -> dict | None:
    """'daily 09:00 do X' → {type, time, dow?, date?, prompt} — or None."""
    s = (text or "").strip()
    m = _DAILY_RE.match(s)
    if m:
        return {"type": "daily", "time": f"{int(m.group(1)):02d}:{m.group(2)}",
                "prompt": m.group(3).strip()}
    m = _WEEKLY_RE.match(s)
    if m:
        dow = _DOW.get(m.group(1).lower())
        if dow is None:
            return None
        return {"type": "weekly", "dow": dow,
                "time": f"{int(m.group(2)):02d}:{m.group(3)}",
                "prompt": m.group(4).strip()}
    m = _ONCE_RE.match(s)
    if m:
        return {"type": "once", "date": m.group(1),
                "time": f"{int(m.group(2)):02d}:{m.group(3)}",
                "prompt": m.group(4).strip()}
    return None


def compute_next(spec: dict, after: float) -> float:
    """Next fire timestamp strictly after `after` (once → its fixed time,
    which may be in the past — the caller fires it immediately)."""
    hh, mm = (int(x) for x in spec["time"].split(":"))
    if spec["type"] == "once":
        st = time.strptime(f"{spec['date']} {spec['time']}", "%Y-%m-%d %H:%M")
        return time.mktime(st)
    lt = time.localtime(after)
    for d in range(0, 9):   # mktime normalizes day overflow; DST-safe (isdst=-1)
        cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + d,
                            hh, mm, 0, 0, 0, -1))
        if cand <= after:
            continue
        if spec["type"] == "daily":
            return cand
        if time.localtime(cand).tm_wday == spec["dow"]:
            return cand
    return after + 86400    # unreachable fallback


def spec_str(s: dict) -> str:
    if s["type"] == "daily":
        return f"daily {s['time']}"
    if s["type"] == "weekly":
        return f"weekly {_DOW_NAMES[s['dow']]} {s['time']}"
    return f"once {s['date']} {s['time']}"


# ── persistence ──────────────────────────────────────────────────────────────
def _load() -> list[dict]:
    try:
        return json.loads(SCHED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    try:
        SCHED_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except Exception:
        log.exception("schedules.json write failed")


def add(spec: dict, channel: str) -> dict:
    with _lock:
        items = _load()
        used = {int(s["id"][1:]) for s in items if str(s.get("id", "")).startswith("s")}
        n = 1
        while n in used:
            n += 1
        item = dict(spec, id=f"s{n}", channel=channel, created=time.time(),
                    next_fire=compute_next(spec, time.time()))
        items.append(item)
        _save(items)
        return item


def remove(sched_id: str) -> bool:
    with _lock:
        items = _load()
        kept = [s for s in items if s.get("id") != sched_id]
        if len(kept) == len(items):
            return False
        _save(kept)
        return True


def list_all() -> list[dict]:
    with _lock:
        return _load()


# ── runner ───────────────────────────────────────────────────────────────────
def rollforward(items: list[dict], now: float) -> bool:
    """Boot policy: recurring schedules missed while down skip to the next
    future slot (no catch-up); `once` is left as-is so the loop fires it.
    Returns True when anything changed."""
    changed = False
    for s in items:
        if s["type"] != "once" and s.get("next_fire", 0) <= now:
            s["next_fire"] = compute_next(s, now)
            changed = True
    return changed


def tick(now: float) -> list[dict]:
    """One poll: pop due schedules (recompute recurring, drop fired `once`).
    Returns the schedules to fire."""
    with _lock:
        items = _load()
        due, keep = [], []
        for s in items:
            if s.get("next_fire", 0) <= now:
                due.append(dict(s))
                if s["type"] != "once":
                    s["next_fire"] = compute_next(s, now)
                    keep.append(s)
            else:
                keep.append(s)
        if due:
            _save(keep)
    return due


def start(on_fire: Callable[[dict], None]) -> None:
    """Start the polling daemon. `on_fire(schedule)` submits the job."""
    with _lock:
        items = _load()
        if rollforward(items, time.time()):
            _save(items)

    def _loop() -> None:
        while True:
            for s in tick(time.time()):
                try:
                    on_fire(s)
                except Exception:
                    log.exception("schedule fire failed (%s)", s.get("id"))
            time.sleep(POLL_SEC)

    threading.Thread(target=_loop, daemon=True).start()
