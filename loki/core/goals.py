"""Goals — the thing that makes Loki an agent rather than a very good relay.

Everything else in Loki is turn-shaped. A message arrives, an agent answers, the
session id gets filed, and the next message starts from whatever the transcript
happens to still contain. That is a *chat bot*: it can remember, but it is not
working toward anything, and nothing in it survives ``!new`` or an idle expiry.

A goal is the missing noun. It is an objective the owner states once — "get the
navmesh run green", "ship the v1.9 release notes" — which then outlives any
single answer: it keeps its own steps, it rides along in the conversation's
context on every turn, and it stays there until it is closed. The agent stops
being asked isolated questions and starts being told what it is for.

Three deliberate limits, because the useful version of this is small:

* **Goals do not act on their own.** Nothing here schedules or triggers a run.
  A goal shapes the next turn the owner takes; it does not take turns by
  itself. Autonomy without a supervisor is how a subscription evaporates
  overnight, and Loki already has ``!schedule`` for work that should fire on a
  clock.
* **Steps are notes, not a plan the code executes.** They exist so a long
  objective has a written spine — what's done, what's next — that both you and
  the model can read on turn twenty.
* **They are the owner's.** ``!goal`` is owner-gated like every built-in, and a
  goal only rides along in the conversation it was opened in.

Storage is ``state/goals.json``: small, hand-editable, and readable when
something has gone wrong at 3am.
"""
from __future__ import annotations

import json
import threading
import time

from . import config
from .config import log, t

_FILE = config.STATE / "goals.json"
_lock = threading.Lock()

OPEN, CLOSED = "open", "closed"
MAX_TITLE = 200
MAX_STEP = 300
# How many open goals ride along in one conversation's prompt. A standing
# objective is only useful if it stays short enough to read — past a handful,
# "keep these in mind" stops meaning anything to a model or a person.
MAX_IN_CONTEXT = 3


def _load() -> dict[str, dict]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(state: dict[str, dict]) -> bool:
    try:
        _FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        return True
    except Exception:
        log.exception("goals.json write failed")
        return False


def _next_id(state: dict) -> str:
    """g1, g2, … — reusing a closed goal's id would make the log ambiguous."""
    used = {int(k[1:]) for k in state if k.startswith("g") and k[1:].isdigit()}
    return f"g{max(used) + 1 if used else 1}"


# ─────────────────────────── the verbs ───────────────────────────
def add(title: str, where: str = "", session_key: str | None = None) -> dict | None:
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        return None
    with _lock:
        state = _load()
        gid = _next_id(state)
        goal = {"id": gid, "title": title, "state": OPEN, "steps": [],
                "created": time.time(), "closed": 0, "note": "",
                "where": where or "", "session_key": session_key or ""}
        state[gid] = goal
        return goal if _save(state) else None


def get(gid: str) -> dict | None:
    return _load().get((gid or "").strip().lower())


def list_all(include_closed: bool = True) -> list[dict]:
    items = sorted(_load().values(), key=lambda g: g.get("created", 0))
    return items if include_closed else [g for g in items if g["state"] == OPEN]


def add_step(gid: str, text: str) -> int | None:
    """Append a step. Returns its 1-based number, or None if there's no goal."""
    text = " ".join((text or "").split())[:MAX_STEP]
    gid = (gid or "").strip().lower()
    if not text:
        return None
    with _lock:
        state = _load()
        goal = state.get(gid)
        if not goal or goal["state"] != OPEN:
            return None
        goal["steps"].append({"text": text, "done": False})
        return len(goal["steps"]) if _save(state) else None


def step_done(gid: str, n: int) -> bool:
    gid = (gid or "").strip().lower()
    with _lock:
        state = _load()
        goal = state.get(gid)
        if not goal or not (1 <= n <= len(goal.get("steps", []))):
            return False
        goal["steps"][n - 1]["done"] = True
        return _save(state)


def close(gid: str, note: str = "") -> dict | None:
    """Close a goal. Returns it, or None when it's missing or already closed."""
    gid = (gid or "").strip().lower()
    with _lock:
        state = _load()
        goal = state.get(gid)
        if not goal or goal["state"] == CLOSED:
            return None
        goal["state"] = CLOSED
        goal["closed"] = time.time()
        goal["note"] = " ".join((note or "").split())[:MAX_STEP]
        return goal if _save(state) else None


def drop(gid: str) -> bool:
    gid = (gid or "").strip().lower()
    with _lock:
        state = _load()
        if state.pop(gid, None) is None:
            return False
        return _save(state)


# ─────────────────────────── riding along ───────────────────────────
def for_conversation(session_key: str | None) -> list[dict]:
    """The open goals this conversation is working toward, oldest first."""
    if not session_key:
        return []
    return [g for g in list_all(include_closed=False)
            if g.get("session_key") == session_key][:MAX_IN_CONTEXT]


def context(session_key: str | None) -> str:
    """The prompt prefix for this conversation's goals — "" when there are none.

    Framed as data, in the same voice as the channel-context guard: a goal is
    something the owner wrote down earlier, and an answer should be shaped by it
    without treating it as a fresh instruction that outranks the actual request.
    """
    parts = []
    for goal in for_conversation(session_key):
        steps = "\n".join(
            f"{'[x]' if s.get('done') else '[ ]'} {i}. {s['text']}"
            for i, s in enumerate(goal.get("steps") or [], 1)) or "(no steps yet)"
        parts.append(t("goal_note", id=goal["id"], title=goal["title"],
                       steps=steps))
    return "".join(parts)
