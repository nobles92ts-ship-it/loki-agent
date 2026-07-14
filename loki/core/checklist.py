"""Checklist model — pure logic, no Slack SDK, fully unit-testable.

A checklist is rendered as Block Kit `checkboxes` so every item is a real,
clickable Slack checkbox; the same items can also be toggled conversationally
(`완료 2`, `done 3`). One JSON file per checklist lives under <state>/checklists/.
The Slack glue (posting, chat.update, action routing) is in
platforms/slack/checklists.py — this module never imports the Slack SDK.

Terms:
  • item        {"id": "i3", "text": "장보기", "checked": bool}
  • checklist   {v, channel, message_ts, thread_ts, title, items, created_by,
                 created_at}
  • chunk       a group of ≤10 items → one `checkboxes` element (Slack caps a
                checkboxes element at 10 options). block_id = "chk::<chunk_idx>".
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ACTION_ID = "checklist_toggle"
MAX_ITEMS = 95                     # header+footer+one block/item ≤ Slack's 100-block cap
_OPT_TEXT_MAX = 74                 # Slack: button text ≤75 chars

# Conversational toggle vocabulary. parse_toggle() is deliberately strict: the
# whole phrase must be keywords + numbers + fillers, so a *created* item that
# merely contains "완료" (e.g. "운동 완료 3회") is never mistaken for a toggle.
TOGGLE_ON = {"완료", "완", "done", "check", "체크", "끝", "ok"}
TOGGLE_OFF = {"취소", "해제", "undo", "uncheck", "미완", "안함"}
ALL_WORDS = {"다", "전부", "모두", "all", "everything"}
_FILLER = {"번", "item", "items", "no", "and", "및", "그리고", "개"}

_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\[\s*[xX ]?\s*\]|☐|☑|✅|\d+[.)])\s+?")
_CMD_PREFIX_RE = re.compile(r"^!(?:check|todo|체크)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z]+|[가-힣]+|\d+")
_SAFE_RE = re.compile(r"[^\w.-]")


# ── parsing a !check body into (title, items) ────────────────────────────────
def _strip_bullet(s: str) -> str:
    return _BULLET_RE.sub("", s).strip()


def parse(body: str) -> tuple[str | None, list[str]]:
    """(title, items). A first line ending in ':' is the title. A single line
    with commas/semicolons is split into items. Bullets/numbers are stripped."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    title = None
    if lines and lines[0].endswith(":"):
        title = lines[0][:-1].strip() or None
        lines = lines[1:]
    if len(lines) == 1 and re.search(r"[,;、]", lines[0]):
        lines = [s.strip() for s in re.split(r"[,;、]", lines[0]) if s.strip()]
    items = [t for t in (_strip_bullet(x) for x in lines) if t][:MAX_ITEMS]
    return title, items


# ── construction ─────────────────────────────────────────────────────────────
def new(channel: str, title: str | None, items: list[str], created_by: str,
        thread_ts: str | None = None, now: float | None = None) -> dict:
    return {
        "v": 1,
        "channel": channel,
        "message_ts": None,                    # filled in after the post lands
        "thread_ts": thread_ts,
        "title": title,
        "items": [{"id": f"i{n + 1}", "text": text, "checked": False}
                  for n, text in enumerate(items)],
        "created_by": created_by,
        "created_at": time.time() if now is None else now,
    }


def progress(cl: dict) -> tuple[int, int]:
    items = cl.get("items") or []
    return sum(1 for it in items if it.get("checked")), len(items)


# ── mutation (immutable — always returns a new checklist) ─────────────────────
def refs_to_ids(cl: dict, refs: list[int]) -> list[str]:
    """1-based display numbers → item ids (out-of-range refs are ignored)."""
    items = cl.get("items") or []
    return [items[r - 1]["id"] for r in refs if 1 <= r <= len(items)]


def set_checked(cl: dict, ids, checked: bool) -> dict:
    ids = set(ids)
    out = dict(cl)
    out["items"] = [{**it, "checked": checked} if it["id"] in ids else dict(it)
                    for it in (cl.get("items") or [])]
    return out


def set_all(cl: dict, checked: bool) -> dict:
    out = dict(cl)
    out["items"] = [{**it, "checked": checked} for it in (cl.get("items") or [])]
    return out


def toggle_item(cl: dict, item_id: str) -> dict:
    """Flip one item's checked state (a button click toggles one item). Immutable."""
    out = dict(cl)
    out["items"] = [
        ({**it, "checked": not it.get("checked")} if it["id"] == item_id else dict(it))
        for it in (cl.get("items") or [])
    ]
    return out


# ── conversational toggle grammar ────────────────────────────────────────────
def parse_toggle(text: str) -> tuple[str | None, list[int], bool]:
    """Strictly parse a toggle phrase → (action, refs, is_all).

    action ∈ {"check", "uncheck", None}. Returns (None, [], False) unless the
    *entire* phrase is toggle keywords + numbers + fillers, so ordinary text
    (including checklist item content) is never swallowed."""
    t = _CMD_PREFIX_RE.sub("", (text or "").strip()).strip().lower()
    tokens = _TOKEN_RE.findall(t)
    if not tokens:
        return None, [], False
    action: str | None = None
    is_all = False
    refs: list[int] = []
    for tok in tokens:
        if tok.isdigit():
            refs.append(int(tok))
        elif tok in TOGGLE_OFF:
            action = "uncheck"
        elif tok in TOGGLE_ON:
            action = action or "check"
        elif tok in ALL_WORDS:
            is_all = True
        elif tok in _FILLER:
            continue
        else:
            return None, [], False          # unknown token → not a toggle
    if action is None:
        return None, [], False
    return action, refs, is_all


# ── Block Kit rendering ──────────────────────────────────────────────────────
_DEFAULT_LABELS = {
    "title": "Checklist",
    "empty": "_(no items)_",
    "footer": "✅ {done}/{total} done · tap a button or say `done N`",
}


def render_blocks(cl: dict, labels: dict | None = None) -> list[dict]:
    """One button per item (☐/☑). Button labels re-render for *everyone* on
    chat_update, so state stays in sync across viewers — unlike `checkboxes`,
    whose checked state is per-user input and does NOT sync across clients."""
    lb = {**_DEFAULT_LABELS, **(labels or {})}
    items = cl.get("items") or []
    title = (cl.get("title") or lb["title"]).strip() or lb["title"]
    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"📋 {title}"[:150], "emoji": True},
    }]
    if not items:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": lb["empty"]}})
        return blocks
    for n, it in enumerate(items, 1):
        checked = bool(it.get("checked"))
        label = f"{'☑' if checked else '☐'} {n}. {it['text']}"
        if len(label) > _OPT_TEXT_MAX:
            label = label[:_OPT_TEXT_MAX - 1] + "…"
        button = {"type": "button", "action_id": ACTION_ID,
                  "text": {"type": "plain_text", "text": label, "emoji": True},
                  "value": it["id"]}
        if checked:
            button["style"] = "primary"
        blocks.append({"type": "actions", "block_id": f"chkitem::{it['id']}",
                       "elements": [button]})
    done, total = progress(cl)
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": lb["footer"].format(done=done, total=total)}]})
    return blocks


def fallback_text(cl: dict, title_default: str = "Checklist") -> str:
    done, total = progress(cl)
    return f"📋 {cl.get('title') or title_default} ({done}/{total})"


# ── storage — one JSON file per checklist ────────────────────────────────────
def _safe(s: str) -> str:
    return _SAFE_RE.sub("_", str(s or ""))


def path_for(state_dir: Path, channel: str, message_ts: str) -> Path:
    return Path(state_dir) / f"{_safe(channel)}__{_safe(message_ts)}.json"


def save(state_dir: Path, cl: dict) -> Path:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    p = path_for(state_dir, cl["channel"], cl.get("message_ts") or "pending")
    p.write_text(json.dumps(cl, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def load_by_ts(state_dir: Path, channel: str, message_ts: str) -> dict | None:
    return load(path_for(state_dir, channel, message_ts))


def _all_in_channel(state_dir: Path, channel: str) -> list[dict]:
    state_dir = Path(state_dir)
    if not state_dir.exists():
        return []
    out = []
    for p in state_dir.glob(f"{_safe(channel)}__*.json"):
        cl = load(p)
        if cl:
            out.append(cl)
    out.sort(key=lambda c: c.get("created_at") or 0, reverse=True)
    return out


def find_latest(state_dir: Path, channel: str) -> dict | None:
    got = _all_in_channel(state_dir, channel)
    return got[0] if got else None


def find_target(state_dir: Path, channel: str,
                thread_ts: str | None) -> dict | None:
    """The checklist a conversational toggle in `thread_ts` refers to: match by
    the checklist's own message_ts or its thread_ts; newest wins."""
    if not thread_ts:
        return None
    tt = str(thread_ts)
    for cl in _all_in_channel(state_dir, channel):
        if str(cl.get("message_ts")) == tt or str(cl.get("thread_ts")) == tt:
            return cl
    return None
