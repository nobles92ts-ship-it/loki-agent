"""Organizations — per-company permission tiers.

One markdown file per org under ``<WORK_DIR>/loki/orgs/`` — the file IS the
config (same philosophy as ``loki.md``): human-readable, edits apply on the
next request (mtime cache), fail-closed when missing or unparseable.

Sections::

    ## Members        - U0…   explicit Slack user ids
    ## Channels       - C0…   bound channels (every non-owner there = this org)
    ## Commands       - name  fixed !commands this org may trigger
    ## Settings       - rate: N    per-hour override of GUEST_RATE_PER_HOUR
    ## Allowed paths  - <path>     readable WORK_DIR top-level folders (scope.py)
    ## Org guide      free text Loki reads when answering this org

Resolution ladder (the adapter checks the owner before orgs):
explicit member > bound channel > None (unaffiliated guest → global loki.md).
A user/channel claimed by two orgs → boot warning + alphabetically-first wins.
Orgs never change the permission mode: members stay read-only like any guest.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from . import config
from .config import log

NAME_RE = re.compile(r"^[A-Za-z0-9가-힣_-]{1,32}$")
_USER_RE = re.compile(r"^[UW][A-Z0-9]{4,}$")
_CHAN_RE = re.compile(r"^[CG][A-Z0-9]{4,}$")
_CMD_TOKEN_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_RATE_RE = re.compile(r"^\s*-\s*rate\s*:\s*(\d{1,5})\s*$",
                      re.IGNORECASE | re.MULTILINE)
_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_ITEM_RE = re.compile(r"^\s*-\s+`?(\S+?)`?(?:\s|$)")

ORG_TEMPLATE = """# Org: {name}

## Members
<!-- explicit members — one Slack user id per line, e.g. -->
<!-- - U012ABCDEF -->

## Channels
<!-- bound channels — every non-owner calling from these = this org, e.g. -->
<!-- - C012ABCDEF -->

## Commands
<!-- fixed !commands this org may trigger; empty = chat (read-only) only -->

## Settings
- rate: {rate}

## Allowed paths
<!-- WORK_DIR top-level folders this org may read (shared in FULL), e.g. -->
<!-- - C:\\work\\shared-{name} -->

## Org guide
<!-- notes Loki reads when answering this org — what each shared folder
     contains, preferred language/tone, who to contact. -->
"""

_EMPTY = {"members": [], "channels": [], "commands": [], "rate": None, "guide": ""}

_lock = threading.Lock()
_cache: dict = {"stamp": None, "orgs": {}, "member_index": {}, "channel_index": {}}


def orgs_dir() -> Path:
    return Path(config.WORK_DIR) / "loki" / "orgs"


def org_file(name: str) -> Path:
    return orgs_dir() / f"{name}.md"


# ── parsing (tolerant, fail-closed) ─────────────────────────────────────────
def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = _HEAD_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(line)
    return out


def _items(lines: list[str], pattern: re.Pattern, lower: bool = False) -> list[str]:
    found: list[str] = []
    for ln in lines:
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        tok = m.group(1)
        if lower:
            tok = tok.lower()
        if pattern.match(tok) and tok not in found:
            found.append(tok)
    return found


def _parse(text: str) -> dict:
    s = _sections(text)
    rate = None
    m = _RATE_RE.search("\n".join(s.get("settings", [])))
    if m:
        rate = max(0, int(m.group(1)))
    return {
        "members": _items(s.get("members", []), _USER_RE),
        "channels": _items(s.get("channels", []), _CHAN_RE),
        "commands": _items(s.get("commands", []), _CMD_TOKEN_RE, lower=True),
        "rate": rate,
        "guide": "\n".join(s.get("org guide", [])).strip(),
    }


def _invalidate() -> None:
    """Force a re-read on the next access — called after our own writes, so
    correctness never depends on filesystem timestamp resolution (Windows
    mtime ticks are coarse enough for same-tick create→edit sequences)."""
    with _lock:
        _cache["stamp"] = None


def _load() -> None:
    """Re-read org files when anything changed (mtime+size stamp)."""
    try:
        files = sorted(p for p in orgs_dir().glob("*.md") if p.is_file())
        stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                      for p in files)
    except Exception:
        files, stamp = [], ()
    with _lock:
        if _cache["stamp"] == stamp:
            return
    orgs: dict[str, dict] = {}
    for p in files:
        try:
            orgs[p.stem] = _parse(p.read_text(encoding="utf-8"))
        except Exception:
            log.exception("org file unreadable — fail-closed empty: %s", p)
            orgs[p.stem] = dict(_EMPTY)
    member_index: dict[str, str] = {}
    channel_index: dict[str, str] = {}
    for name in sorted(orgs):                     # alphabetical-first wins
        for uid in orgs[name]["members"]:
            if uid in member_index and member_index[uid] != name:
                log.warning("org overlap: user %s in '%s' and '%s' — using '%s'",
                            uid, member_index[uid], name, member_index[uid])
            member_index.setdefault(uid, name)
        for cid in orgs[name]["channels"]:
            if cid in channel_index and channel_index[cid] != name:
                log.warning("org overlap: channel %s in '%s' and '%s' — using '%s'",
                            cid, channel_index[cid], name, channel_index[cid])
            channel_index.setdefault(cid, name)
    with _lock:
        _cache.update(stamp=stamp, orgs=orgs,
                      member_index=member_index, channel_index=channel_index)


# ── read API ────────────────────────────────────────────────────────────────
def resolve(user: str | None, channel: str | None) -> str | None:
    """Explicit member first, then bound channel, else None (unaffiliated)."""
    _load()
    with _lock:
        if user and user in _cache["member_index"]:
            return _cache["member_index"][user]
        if channel and channel in _cache["channel_index"]:
            return _cache["channel_index"][channel]
    return None


def get(name: str) -> dict | None:
    _load()
    with _lock:
        o = _cache["orgs"].get(name)
        return dict(o) if o is not None else None


def names() -> list[str]:
    _load()
    with _lock:
        return sorted(_cache["orgs"])


def allows_command(org: str | None, command: str) -> bool:
    if not org or not command:
        return False
    o = get(org)
    return bool(o) and command.lower() in o["commands"]


def rate(org: str | None) -> int | None:
    """Org's per-hour override, or None → use GUEST_RATE_PER_HOUR."""
    if not org:
        return None
    o = get(org)
    return o["rate"] if o else None


def manifest_text(org: str) -> str:
    try:
        return org_file(org).read_text(encoding="utf-8")
    except Exception:
        return ""


# ── owner CRUD (the file stays the single source of truth) ──────────────────
def create(name: str) -> str:
    """Returns 'created' | 'exists' | 'badname'."""
    if not NAME_RE.match(name or ""):
        return "badname"
    orgs_dir().mkdir(parents=True, exist_ok=True)
    f = org_file(name)
    if f.exists():
        return "exists"
    f.write_text(ORG_TEMPLATE.format(name=name, rate=config.GUEST_RATE_PER_HOUR),
                 encoding="utf-8")
    _invalidate()
    log.info("org created: %s", f)
    return "created"


def _edit_section(name: str, section: str, add: str | None = None,
                  drop_token: str | None = None) -> bool:
    """Append a '- …' line to a section and/or drop lines whose first token
    matches. Returns True if the file changed."""
    f = org_file(name)
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    start = end = None
    for i, ln in enumerate(lines):
        m = _HEAD_RE.match(ln)
        if m and start is None and m.group(1).strip().lower() == section:
            start = i
        elif m and start is not None:
            end = i
            break
    if start is None:                              # section missing → append it
        lines += ["", f"## {section.title()}"]
        start = len(lines) - 1
    if end is None:
        end = len(lines)
    changed = False
    if drop_token:
        keep, dropped = [], 0
        for i, ln in enumerate(lines):
            if start < i < end:
                m = _ITEM_RE.match(ln)
                if m and m.group(1) == drop_token:
                    dropped += 1
                    changed = True
                    continue
            keep.append(ln)
        lines, end = keep, end - dropped
    if add is not None:
        at = start                                 # after last non-blank line
        for i in range(start + 1, end):
            if lines[i].strip():
                at = i
        lines.insert(at + 1, add)
        changed = True
    if changed:
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _invalidate()
    return changed


def add_member(name: str, uid: str, label: str = "") -> bool:
    o = get(name)
    if o is None or not _USER_RE.match(uid or "") or uid in o["members"]:
        return False
    note = f"   <!-- {label} -->" if label else ""
    return _edit_section(name, "members", add=f"- {uid}{note}")


def remove_member(name: str, uid: str) -> bool:
    return get(name) is not None and _edit_section(name, "members", drop_token=uid)


def bind(name: str, cid: str) -> bool:
    o = get(name)
    if o is None or not _CHAN_RE.match(cid or "") or cid in o["channels"]:
        return False
    return _edit_section(name, "channels", add=f"- {cid}")


def unbind(name: str, cid: str) -> bool:
    return get(name) is not None and _edit_section(name, "channels", drop_token=cid)


def allow_command(name: str, command: str) -> bool:
    o = get(name)
    cmd = (command or "").lower()
    if o is None or not _CMD_TOKEN_RE.match(cmd) or cmd in o["commands"]:
        return False
    return _edit_section(name, "commands", add=f"- {cmd}")


def deny_command(name: str, command: str) -> bool:
    return get(name) is not None and _edit_section(
        name, "commands", drop_token=(command or "").lower())
