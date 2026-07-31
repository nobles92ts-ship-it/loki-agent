"""Usage budgets — caps that protect the subscription, and how to react.

Two separate things live here, and keeping them separate is the whole design:

**Caps** are hard. A daily or weekly total, and optionally a per-org daily
total; once one is reached, *guests* are refused. The owner is never capped —
same rule as the rate limiter, since a budget exists to stop other people
spending your subscription, not to lock you out of your own machine.

**Mitigations** are how Loki reacts as a cap approaches: switch the model to
something lighter, or pause guests early. Those change how the whole install
behaves, so they are **manual by default** — at 80% and again at 100% Loki
asks the owner in DM and waits. Set ``mode: auto`` and it applies the model
switch itself instead of asking.

State lives in ``state/budget.json``; usage counts come from the existing
ledger, so nothing new is recorded and nothing new is retained.
"""
from __future__ import annotations

import json
import threading
import time

from . import config, usage
from .config import log

BUDGET_FILE = config.STATE / "budget.json"

WARN_PCT = 80
MODES = ("manual", "auto")
ACTIONS = ("sonnet", "pause", "resume", "ignore", "default")
LIGHT_MODEL = "sonnet"
NOTICE_RETENTION_DAYS = 7

_DEFAULTS = {
    "mode": "manual",
    "daily": 0,              # 0 = no cap
    "weekly": 0,
    "orgs": {},              # org name -> daily cap
    "model_override": "",    # set by the 'sonnet' mitigation
    "guests_paused_until": 0.0,
    "notified": {},          # "<date>:<scope>:<pct>" -> True
}

_lock = threading.Lock()
_cache: dict = {"stamp": None, "data": None}


# ── persistence ──────────────────────────────────────────────────────────────
def _read() -> dict:
    try:
        st = BUDGET_FILE.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except Exception:
        stamp = None
    with _lock:
        if stamp is not None and _cache["stamp"] == stamp and _cache["data"]:
            return dict(_cache["data"])
    data = dict(_DEFAULTS)
    if stamp is not None:
        try:
            loaded = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({k: v for k, v in loaded.items() if k in _DEFAULTS})
        except Exception:
            log.exception("budget.json unreadable — using defaults")
    data["orgs"] = dict(data.get("orgs") or {})
    data["notified"] = dict(data.get("notified") or {})
    with _lock:
        _cache.update(stamp=stamp, data=dict(data))
    return data


def _write(data: dict) -> None:
    data["notified"] = _prune_notices(data.get("notified") or {})
    try:
        BUDGET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception:
        log.exception("budget.json write failed")
    with _lock:                          # never trust mtime for our own writes
        _cache.update(stamp=None, data=dict(data))


def settings() -> dict:
    return _read()


# ── period helpers ───────────────────────────────────────────────────────────
def day_start(now: float | None = None) -> float:
    """Local midnight — the daily cap matches how `!usage` reports 'today'."""
    lt = time.localtime(now if now is not None else time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def week_start(now: float | None = None) -> float:
    """Rolling seven days, matching how subscription limits actually behave."""
    return (now if now is not None else time.time()) - 7 * 86400


def _date_key(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d",
                         time.localtime(now if now is not None else time.time()))


def _prune_notices(notified: dict) -> dict:
    cutoff = _date_key(time.time() - NOTICE_RETENTION_DAYS * 86400)
    return {k: v for k, v in notified.items() if k.split(":", 1)[0] >= cutoff}


# ── reading the current picture ──────────────────────────────────────────────
def scopes(data: dict | None = None, now: float | None = None) -> list[dict]:
    """Every configured cap with its current usage.

    Each entry: {name, label, used, limit, pct}. Scopes without a limit are
    omitted — an unset cap is not a budget.
    """
    s = data if data is not None else _read()
    now = now if now is not None else time.time()
    out: list[dict] = []
    if s.get("daily"):
        out.append({"name": "daily", "label": "daily",
                    "used": usage.count_since(day_start(now)),
                    "limit": int(s["daily"])})
    if s.get("weekly"):
        out.append({"name": "weekly", "label": "weekly",
                    "used": usage.count_since(week_start(now)),
                    "limit": int(s["weekly"])})
    for org, limit in sorted((s.get("orgs") or {}).items()):
        if limit:
            out.append({"name": f"org:{org}", "label": org,
                        "used": usage.count_since(day_start(now), org=org),
                        "limit": int(limit)})
    for sc in out:
        sc["pct"] = min(999, sc["used"] * 100 // max(1, sc["limit"]))
    return out


def model_override() -> str:
    """Model the 'sonnet' mitigation pinned, or '' to use the configured one."""
    return (_read().get("model_override") or "").strip()


def guests_paused_for(now: float | None = None) -> int:
    """Minutes remaining on a guest pause (0 when guests are not paused)."""
    now = now if now is not None else time.time()
    left = float(_read().get("guests_paused_until") or 0) - now
    return max(0, int((left + 59) // 60)) if left > 0 else 0


def check_guest(org: str | None = None,
                now: float | None = None) -> tuple[bool, str, dict]:
    """(allowed, i18n key, message params) for a non-owner. Owners skip this."""
    now = now if now is not None else time.time()
    s = _read()
    paused = guests_paused_for(now)
    if paused:
        return False, "budget_paused", {"n": paused}
    for sc in scopes(s, now):
        # An org's cap binds that org only — one company burning through its
        # budget must never lock out another, same as scope isolation.
        if sc["name"].startswith("org:") and sc["name"] != f"org:{org}":
            continue
        if sc["used"] >= sc["limit"]:
            key = ("budget_reached_org" if sc["name"].startswith("org:")
                   else "budget_reached")
            return False, key, {"label": sc["label"], "used": sc["used"],
                                "limit": sc["limit"]}
    return True, "", {}


# ── mitigations ──────────────────────────────────────────────────────────────
def apply(action: str, now: float | None = None) -> str:
    """Apply a mitigation. Returns an i18n key describing what changed —
    'budget_nochange' when it was already in that state."""
    now = now if now is not None else time.time()
    action = (action or "").lower()
    if action not in ACTIONS:
        return "budget_help"
    s = _read()
    if action == "sonnet":
        if s.get("model_override") == LIGHT_MODEL:
            return "budget_nochange"
        s["model_override"] = LIGHT_MODEL
    elif action == "default":
        if not s.get("model_override"):
            return "budget_nochange"
        s["model_override"] = ""
    elif action == "pause":
        s["guests_paused_until"] = day_start(now) + 86400   # until local midnight
    elif action == "resume":
        if not guests_paused_for(now):
            return "budget_nochange"
        s["guests_paused_until"] = 0.0
    elif action == "ignore":
        for sc in scopes(s, now):                            # silence today
            for pct in (WARN_PCT, 100):
                s["notified"][f"{_date_key(now)}:{sc['name']}:{pct}"] = True
    _write(s)
    return f"budget_applied_{action}"


def note_usage(now: float | None = None) -> list[dict]:
    """Called after each job. Returns thresholds crossed since the last check.

    Each alert: {label, used, limit, threshold, applied}. `applied` is the
    i18n key of an automatic mitigation ('' in manual mode, where the adapter
    asks the owner instead). At most one alert per scope per day per threshold.
    """
    now = now if now is not None else time.time()
    s = _read()
    alerts: list[dict] = []
    dirty = False
    for sc in scopes(s, now):
        for threshold in (100, WARN_PCT):
            if sc["pct"] < threshold:
                continue
            key = f"{_date_key(now)}:{sc['name']}:{threshold}"
            if s["notified"].get(key):
                break                     # already told them about this level
            s["notified"][key] = True
            dirty = True
            applied = ""
            if s.get("mode") == "auto" and s.get("model_override") != LIGHT_MODEL:
                s["model_override"] = LIGHT_MODEL
                applied = "budget_applied_sonnet"
            alerts.append({"label": sc["label"], "used": sc["used"],
                           "limit": sc["limit"], "threshold": threshold,
                           "scope": sc["name"], "applied": applied})
            break                         # report the highest level only
    if dirty:
        _write(s)
    return alerts


# ── owner settings ───────────────────────────────────────────────────────────
def set_mode(mode: str) -> bool:
    mode = (mode or "").lower()
    if mode not in MODES:
        return False
    s = _read()
    s["mode"] = mode
    _write(s)
    return True


def set_limit(period: str, n: int) -> bool:
    period = (period or "").lower()
    if period not in ("daily", "weekly") or n < 0:
        return False
    s = _read()
    s[period] = int(n)
    _write(s)
    return True


def set_org_limit(org: str, n: int) -> bool:
    if not org or n < 0:
        return False
    s = _read()
    if n:
        s["orgs"][org] = int(n)
    else:
        s["orgs"].pop(org, None)
    _write(s)
    return True


def clear() -> None:
    """Drop every cap and mitigation — budgets off, nothing pinned."""
    s = _read()
    s.update(daily=0, weekly=0, orgs={}, model_override="",
             guests_paused_until=0.0, notified={})
    _write(s)
