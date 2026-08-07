"""Command aliases — name a prompt once, then run it with `!name`.

``<WORK_DIR>/loki/aliases.md`` is the single source of truth, same philosophy
as ``loki.md`` and the org files: human-editable markdown, re-read whenever it
changes, fail-closed when it doesn't parse::

    ## Aliases
    - standup: Summarize yesterday's commits in WORK_DIR
    - review: Review {args} and list the risky changes
    - report: Draft this week's report from my notes
        Keep it under a page and lead with blockers.

``{args}`` is replaced by whatever follows the command; a template without the
placeholder gets the arguments appended instead. Indented lines continue the
previous alias, so long templates stay readable in the file.

Owners may run any alias. Guests may run only the ones their org was granted
(``!org allow <org> <name>``) — an alias is a fixed prompt, not a new power,
but it still spends the subscription, so it stays opt-in per org.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from . import config
from .config import log

NAME_RE = re.compile(r"^[A-Za-z0-9가-힣_-]{1,32}$")
MAX_TEMPLATE = 4000                     # a template is a prompt, not a document

# Names an alias may never take. Two jobs, and only the first one needs this
# list to be complete: names held for commands that DON'T EXIST YET (`help`,
# `model`, `health`, …), so shipping one later can't take over a name someone
# is already using. Names that *do* exist are found in the live dispatch by
# core.registry — which also sees this fork's commands and anything in
# plugins/, where a hand-kept copy of the dispatch table always drifted. The
# shipped names below stay as a floor for the case where that probe can't run.
RESERVED = {
    "stop", "cancel", "jobs", "usage", "schedule", "learn", "send", "block",
    "unblock", "summary", "listen", "unlisten", "listening", "org", "check",
    "todo", "alias", "budget", "bot", "help", "model", "health", "new",
    "plugins", "status", "doctor", "gateway", "reset",
    "중지", "취소", "작업목록", "사용량", "예약", "학습", "전송", "파일",
    "차단", "차단해제", "채널요약", "청취", "청취해제", "청취목록", "조직",
    "체크", "별칭", "예산", "봇", "도움말", "새대화", "리셋", "플러그인",
}

# `!name [args]` — an alias call. Built-in commands and plugins are matched
# before this, and an unknown name falls through to the brain as ordinary text.
CALL_RE = re.compile(r"^!([A-Za-z0-9가-힣_-]{1,32})(?:\s+(.*))?$",
                     re.IGNORECASE | re.DOTALL)

_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_ITEM_RE = re.compile(r"^-\s+`?([^\s:`]{1,32})`?\s*:\s*(.*)$")
_COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")
_SECTION = "aliases"

TEMPLATE = """# Loki command aliases

One line per alias: `- name: the prompt Loki should run`.
Indented lines continue the alias above them.
`{args}` is replaced with whatever you type after the command; without it,
your arguments are appended to the end.

Edits apply on the next request — no restart needed.

## Aliases
<!-- - standup: Summarize yesterday's commits in WORK_DIR -->
<!-- - review: Review {args} and list the risky changes -->
"""

_lock = threading.Lock()
_cache: dict = {"stamp": None, "items": {}}


def aliases_file() -> Path:
    return Path(config.WORK_DIR) / "loki" / "aliases.md"


def ensure_file() -> None:
    """Create the template on first boot (empty list → nothing to shadow)."""
    f = aliases_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text(TEMPLATE, encoding="utf-8")
            log.info("alias template created at %s", f)
    except Exception:
        log.exception("alias template create failed")


# ── parsing ─────────────────────────────────────────────────────────────────
def parse(text: str) -> dict[str, str]:
    """Markdown → {name: template}. Unknown sections and junk are skipped;
    a malformed line is dropped rather than poisoning the whole file."""
    items: dict[str, str] = {}
    in_section = False
    current: str | None = None
    for raw in (text or "").splitlines():
        head = _HEAD_RE.match(raw)
        if head:
            in_section = head.group(1).strip().lower() == _SECTION
            current = None
            continue
        if not in_section or _COMMENT_RE.match(raw):
            continue
        item = _ITEM_RE.match(raw.strip()) if raw.strip().startswith("-") else None
        if item:
            name, body = item.group(1).lower(), item.group(2).strip()
            current = None
            if NAME_RE.match(name) and name not in RESERVED and name not in items:
                items[name] = body[:MAX_TEMPLATE]
                current = name
            continue
        if current and raw.strip() and raw[:1].isspace():      # continuation
            items[current] = (items[current] + "\n" + raw.strip())[:MAX_TEMPLATE]
        elif not raw.strip():
            current = None
    return {k: v for k, v in items.items() if v}


def _load() -> None:
    f = aliases_file()
    try:
        st = f.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except Exception:
        stamp = None
    with _lock:
        if _cache["stamp"] == stamp and stamp is not None:
            return
    items: dict[str, str] = {}
    if stamp is not None:
        try:
            items = parse(f.read_text(encoding="utf-8"))
        except Exception:
            log.exception("aliases.md unreadable — fail-closed empty")
    with _lock:
        _cache.update(stamp=stamp, items=items)


def _invalidate() -> None:
    """Force a re-read after our own write (never trust mtime resolution)."""
    with _lock:
        _cache["stamp"] = None


def all_items() -> dict[str, str]:
    _load()
    with _lock:
        return dict(_cache["items"])


def get(name: str) -> str | None:
    return all_items().get((name or "").lower())


def shadowed_by(name: str) -> str | None:
    """Which live command would swallow ``!name`` first, if any.

    Local import: :mod:`core.registry` probes the loaded modules, and the
    command modules import this one.
    """
    from . import registry                   # local: avoids an import cycle
    try:
        return registry.shadowed_by(name)
    except Exception:
        log.exception("registry probe failed — treating the name as free")
        return None


def expand(template: str, args: str = "") -> str:
    """Fill a template. `{args}` wins; otherwise arguments are appended."""
    args = (args or "").strip()
    if "{args}" in template:
        return template.replace("{args}", args).strip()
    return f"{template} {args}".strip() if args else template


def resolve(text: str, is_owner: bool, org: str | None,
            allows) -> tuple[str, str] | None:
    """`!name args` → (expanded prompt, reply prefix), or None if not an alias.

    Unlike a command this returns no reply — the expansion becomes the request
    and runs through the ordinary path, so the queue, throttle and guest scope
    all still apply. An ungranted guest gets None *and* silence: confirming
    that an alias exists would leak the owner's command surface.
    """
    m = CALL_RE.match((text or "").strip())
    if not m:
        return None
    name = m.group(1).lower()
    template = get(name)
    if template is None:
        return None
    if not (is_owner or allows(org, name)):
        return "", ""                       # known alias, not granted → drop
    from .config import t                    # local: avoids an import cycle
    return expand(template, m.group(2) or ""), t("alias_fired", name=name)


# ── owner CRUD (the file stays the single source of truth) ──────────────────
def add(name: str, template: str) -> str:
    """'added' | 'replaced' | 'badname' | 'reserved' | 'empty' | 'error', or
    'shadowed:<owner>' when a live command already answers to the name."""
    name = (name or "").lower()
    body = (template or "").strip()
    if not NAME_RE.match(name):
        return "badname"
    if name in RESERVED:
        return "reserved"                   # a shipped name: "built-in" says it
    owner = shadowed_by(name)
    if owner:
        return f"shadowed:{owner}"          # a fork's or a plugin's: name it
    if not body:
        return "empty"
    existing = get(name)
    lines = [ln for ln in body.splitlines() if ln.strip()][:40]
    block = [f"- {name}: {lines[0].strip()[:MAX_TEMPLATE]}"]
    block += [f"  {ln.strip()}" for ln in lines[1:]]
    if existing is not None and not remove(name):
        return "error"
    if not _append(block):
        return "error"
    return "replaced" if existing is not None else "added"


def _append(block: list[str]) -> bool:
    f = aliases_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        text = f.read_text(encoding="utf-8") if f.exists() else TEMPLATE
    except Exception:
        log.exception("aliases.md read failed")
        return False
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        head = _HEAD_RE.match(ln)
        if head and head.group(1).strip().lower() == _SECTION:
            start = i
            break
    if start is None:
        lines += ["", "## Aliases"]
        at = len(lines) - 1
    else:
        at = start
        for i in range(start + 1, len(lines)):
            if _HEAD_RE.match(lines[i]):
                break
            if lines[i].strip():
                at = i
    lines[at + 1:at + 1] = block
    try:
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        log.exception("aliases.md write failed")
        return False
    _invalidate()
    return True


def remove(name: str) -> bool:
    """Drop an alias and any continuation lines under it."""
    name = (name or "").lower()
    f = aliases_file()
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    out: list[str] = []
    dropping = False
    changed = False
    for raw in lines:
        if dropping:
            if raw.strip() and raw[:1].isspace() and not raw.strip().startswith("-"):
                continue                                   # continuation line
            dropping = False
        item = _ITEM_RE.match(raw.strip()) if raw.strip().startswith("-") else None
        if item and item.group(1).lower() == name and not _COMMENT_RE.match(raw):
            dropping = True
            changed = True
            continue
        out.append(raw)
    if not changed:
        return False
    try:
        f.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        log.exception("aliases.md write failed")
        return False
    _invalidate()
    return True
