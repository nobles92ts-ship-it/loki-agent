"""Nudges — what Loki offers before you ask, and what it raises unprompted.

Loki is good at answering. It is bad at being *asked*. The owner has to arrive
already knowing what to request, and the requests worth making are exactly the
ones whose details they do not have at hand: which run failed, what ``g2`` was
waiting on, whether the provider they switched to last week still works. The
gap is not in the agent's ability, it is in the opening move — and the opening
move is the one thing Loki was never allowed to make.

A nudge is that opening move, in one shape, pointed two ways:

* **pull** — :func:`suggest` builds the prompts Slack shows in the assistant
  pane the moment a thread opens. Nothing has happened; these are doors.
* **push** — :func:`watch` is polled on a timer and returns only what *just
  became true*. Something has happened, and Loki says so first.

Same dict either way, so one button handler serves both and a nudge written
once appears wherever it fits.

Push is the direction that can go wrong. A watcher that is reliably right is a
watcher that reliably interrupts, and an assistant that interrupts is one you
mute — at which point the correct alerts die with the noisy ones. Three limits
hold that line:

* every nudge carries a stable ``key`` and fires at most once per cooldown
  window, sized to how fast its condition can honestly change;
* a single key can be silenced (``!nudge off provider:codex``) without
  silencing the mechanism;
* watchers read state Loki already keeps — goals, the usage ledger, the
  provider probe. Nothing here starts a run to find something to say.

State is ``state/nudges.json``: what is off, what is silenced, what last fired.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import config, goals, providers, usage
from .config import log, t

_FILE = config.STATE / "nudges.json"
_lock = threading.Lock()

# How often the push loop looks. Watchers are cheap (a JSON read and a cached
# version probe), but the conditions they watch move in hours, not seconds.
POLL_MIN = max(1, int(os.environ.get("LOKI_NUDGE_POLL_MIN", "15")))
ENABLED = (os.environ.get("LOKI_NUDGE", "on").strip().lower()
           not in ("0", "off", "false", "no"))

MAX_SUGGESTIONS = 4        # Slack's assistant pane shows at most four
MAX_PUSH = 2               # per poll — a burst of alerts is an outage report

# A goal nobody has touched in this long is one the owner has probably lost the
# thread of. Long enough that a weekend does not trip it.
STALE_GOAL_H = int(os.environ.get("LOKI_NUDGE_STALE_H", "72"))
# Consecutive failed runs before Loki stops assuming it was a one-off.
FAIL_STREAK = 3

# Quiet time after a nudge fires, per key prefix. Sized to the condition: a
# stale goal will still be stale in an hour, a broken provider is fixed the
# second you re-authenticate.
COOLDOWN_H = {"goal": 24, "fail": 6, "provider": 12}
DEFAULT_COOLDOWN_H = 6


# ─────────────────────────── the shape ───────────────────────────
def make(key: str, label: str, prompt: str, why: str = "") -> dict:
    """One nudge.

    ``key``    stable id — what cooldown and silencing are keyed on
    ``label``  the button text; short enough for Slack's suggestion chip
    ``prompt`` what actually runs when it is accepted
    ``why``    the evidence, in one line — a nudge that cannot say why it
               appeared is indistinguishable from the agent inventing work
    """
    return {"key": key, "label": label, "prompt": prompt, "why": why}


# ─────────────────────────── state ───────────────────────────
def _load() -> dict:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _save(state: dict) -> bool:
    try:
        _FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        return True
    except Exception:
        log.exception("nudges.json write failed")
        return False


def enabled() -> bool:
    """Push nudges on? The env var is the default; `!nudge on|off` overrides."""
    with _lock:
        off = _load().get("off")
    return ENABLED if off is None else not off


def set_enabled(on: bool) -> None:
    with _lock:
        state = _load()
        state["off"] = not on
        _save(state)


def silenced() -> list[str]:
    with _lock:
        return sorted(_load().get("silenced") or [])


def silence(key: str, on: bool = True) -> bool:
    """Mute (or unmute) one key. False when nothing changed."""
    key = (key or "").strip()
    if not key:
        return False
    with _lock:
        state = _load()
        keys = set(state.get("silenced") or [])
        if on and key in keys or not on and key not in keys:
            return False
        keys.add(key) if on else keys.discard(key)
        state["silenced"] = sorted(keys)
        return _save(state)


def fired() -> dict[str, float]:
    with _lock:
        raw = _load().get("fired") or {}
    return {k: v for k, v in raw.items() if isinstance(v, (int, float))}


def _cooldown_s(key: str) -> float:
    return COOLDOWN_H.get(key.split(":", 1)[0], DEFAULT_COOLDOWN_H) * 3600


def cooldown_left(key: str) -> float:
    """Seconds until `key` may fire again — 0 when it is free to fire now."""
    return max(0.0, _cooldown_s(key) - (time.time() - fired().get(key, 0)))


def _mark_fired(keys: list[str]) -> None:
    now = time.time()
    with _lock:
        state = _load()
        stamps = state.get("fired") or {}
        stamps.update({k: now for k in keys})
        # Forget stamps far past their own cooldown so the file stays small and
        # a key that goes quiet for a month does not linger forever.
        state["fired"] = {k: ts for k, ts in stamps.items()
                          if now - ts < _cooldown_s(k) * 4}
        _save(state)


# ─────────────────────────── watchers ───────────────────────────
def _short(s: str, n: int = 30) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _next_step(goal: dict) -> str:
    for step in goal.get("steps") or []:
        if not step.get("done"):
            return step.get("text") or ""
    return ""


def _stale_goals() -> list[dict]:
    """An open goal nobody has moved in STALE_GOAL_H hours."""
    now = time.time()
    out = []
    for g in goals.list_all(include_closed=False):
        touched = g.get("touched") or g.get("created") or 0
        idle_h = (now - touched) / 3600
        if idle_h < STALE_GOAL_H:
            continue
        step = _next_step(g)
        out.append(make(
            f"goal:{g['id']}",
            t("nudge_goal_label", id=g["id"], title=_short(g["title"])),
            t("nudge_goal_prompt", id=g["id"], title=g["title"],
              step=step or t("nudge_goal_nostep")),
            t("nudge_goal_why", id=g["id"], days=int(idle_h // 24) or 1)))
    return out


def _failing_runs() -> list[dict]:
    """The last FAIL_STREAK runs all failed for the same reason.

    One failure is weather. Three in a row with the same reason is a broken
    setup, and the owner is usually the only one who can fix it.
    """
    rows = usage.recent(FAIL_STREAK)
    if len(rows) < FAIL_STREAK or any(r.get("ok") for r in rows):
        return []
    reasons = {r.get("reason") or "error" for r in rows}
    if len(reasons) != 1:
        return []
    reason = reasons.pop()
    return [make(f"fail:{reason}",
                 t("nudge_fail_label", n=FAIL_STREAK),
                 t("nudge_fail_prompt", n=FAIL_STREAK, reason=reason),
                 t("nudge_fail_why", n=FAIL_STREAK, reason=reason))]


def _provider_down() -> list[dict]:
    """The provider currently selected cannot answer.

    Worth interrupting for: nothing else in Loki reports it until a request
    has already been sent and lost.
    """
    mod = providers.current()
    try:
        ok, note = mod.available()
    except Exception:
        log.exception("provider probe failed in nudge watcher")
        return []
    if ok:
        return []
    return [make(f"provider:{mod.NAME}",
                 t("nudge_provider_label", name=mod.NAME),
                 t("nudge_provider_prompt", name=mod.NAME, note=note),
                 t("nudge_provider_why", name=mod.NAME, note=note))]


WATCHERS = (_stale_goals, _failing_runs, _provider_down)


def probe() -> list[dict]:
    """Every condition that is true right now — no cooldown, no side effects.

    Read-only on purpose: :func:`suggest` calls this to decorate the assistant
    pane, and looking at the pane must not consume a push nudge.
    """
    out: list[dict] = []
    for watcher in WATCHERS:
        try:
            out.extend(watcher())
        except Exception:
            log.exception("nudge watcher failed (%s)", watcher.__name__)
    return out


def watch() -> list[dict]:
    """What Loki should raise *now* — cooldown applied, firing recorded."""
    if not enabled():
        return []
    quiet, stamps, now = set(silenced()), fired(), time.time()
    due = [n for n in probe()
           if n["key"] not in quiet
           and now - stamps.get(n["key"], 0) >= _cooldown_s(n["key"])][:MAX_PUSH]
    if due:
        _mark_fired([n["key"] for n in due])
    return due


# ─────────────────────────── suggestions ───────────────────────────
def defaults() -> list[dict]:
    """Openings that are always true — used when nothing else has anything
    to say, which is the state a brand-new thread is always in."""
    return [make(f"default:{n}", t(f"nudge_d{n}_label"), t(f"nudge_d{n}_msg"))
            for n in (1, 2, 3)]


def suggest(session_key: str | None = None,
            limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """The prompts to offer when a thread opens.

    Live conditions first (a broken provider is the most useful thing Loki can
    tell you before you type), then the open goals, then the standing openings.
    ``session_key`` biases the goals toward this conversation's own; a fresh
    assistant thread has none, so the rest of the open goals fill in.
    """
    out = probe()
    mine = {g["id"] for g in goals.for_conversation(session_key)}
    ordered = sorted(goals.list_all(include_closed=False),
                     key=lambda g: (g["id"] not in mine, g.get("created", 0)))
    for g in ordered:
        step = _next_step(g)
        out.append(make(
            f"goal:{g['id']}",
            t("nudge_goal_next_label", id=g["id"], title=_short(g["title"])),
            t("nudge_goal_next_prompt", id=g["id"], title=g["title"],
              step=step or t("nudge_goal_nostep"))))
    out.extend(defaults())
    seen, unique = set(), []
    for n in out:
        if n["key"] in seen:
            continue
        seen.add(n["key"])
        unique.append(n)
    return unique[:limit]
