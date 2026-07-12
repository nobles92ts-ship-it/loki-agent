"""Usage ledger — metadata only (who/when/kind/duration), never message bodies.

Backs the owner's `!usage` command. Rows land in ``state/usage.jsonl`` and are
pruned past 90 days when the file grows large.
"""
from __future__ import annotations

import json
import threading
import time

from . import config

USAGE_FILE = config.STATE / "usage.jsonl"
RETENTION_DAYS = 90
PRUNE_AT_BYTES = 1_000_000

_lock = threading.Lock()


def record(kind: str, user: str, ok: bool, dur_s: float, reason: str = "ok",
           org: str | None = None) -> None:
    row = {"ts": time.time(), "kind": kind or "?", "user": user or "?",
           "ok": bool(ok), "dur": round(float(dur_s), 1), "reason": reason}
    if org:
        row["org"] = org
    with _lock:
        try:
            if (USAGE_FILE.exists()
                    and USAGE_FILE.stat().st_size > PRUNE_AT_BYTES):
                _prune_locked()
            with USAGE_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            config.log.exception("usage write failed")


def _prune_locked() -> None:
    cutoff = time.time() - RETENTION_DAYS * 86400
    rows = [r for r in _read_rows() if r.get("ts", 0) >= cutoff]
    USAGE_FILE.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _read_rows() -> list[dict]:
    try:
        text = USAGE_FILE.read_text(encoding="utf-8")
    except Exception:
        return []
    rows = []
    for line in text.splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def summarize(days: int = 7) -> dict:
    """Aggregate the last N days (plus a since-local-midnight 'today' block)."""
    now = time.time()
    cutoff = now - days * 86400
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    rows = [r for r in _read_rows() if r.get("ts", 0) >= cutoff]
    by_user: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_org: dict[str, int] = {}
    ok = fail = 0
    dur_total = 0.0
    today = {"total": 0, "dur": 0.0}
    for r in rows:
        (by_user.__setitem__(r.get("user", "?"),
                             by_user.get(r.get("user", "?"), 0) + 1))
        by_kind[r.get("kind", "?")] = by_kind.get(r.get("kind", "?"), 0) + 1
        if r.get("org"):
            by_org[r["org"]] = by_org.get(r["org"], 0) + 1
        ok += 1 if r.get("ok") else 0
        fail += 0 if r.get("ok") else 1
        dur_total += float(r.get("dur", 0))
        if r.get("ts", 0) >= midnight:
            today["total"] += 1
            today["dur"] += float(r.get("dur", 0))
    return {
        "days": days, "total": len(rows), "ok": ok, "fail": fail,
        "dur_total": dur_total,
        "by_user": sorted(by_user.items(), key=lambda kv: -kv[1]),
        "by_kind": sorted(by_kind.items(), key=lambda kv: -kv[1]),
        "by_org": sorted(by_org.items(), key=lambda kv: -kv[1]),
        "today": today,
    }
