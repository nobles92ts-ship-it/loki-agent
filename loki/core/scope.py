"""Guest allowlist — the owner declares guest-readable paths in a manifest.

``<WORK_DIR>/loki/loki.md`` is the single source of truth for what non-owners
may read. Everything else — every other WORK_DIR entry, other drives, user
profiles, ``~/.claude`` — is denied at the tool level via a per-request
``--settings`` JSON (deny rules beat any allow rules, and the list is far too
long for a command line: cmd.exe truncates at 8191 chars).

Fail-closed: a missing or empty manifest means guests can only see the loki
folder itself. Manifest edits apply on the next request — no restart needed.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config

_CLAUDE_HOME_GLOB = os.path.expanduser("~/.claude").replace("\\", "/") + "/**"
_PATH_LINE_RE = re.compile(
    r"^\s*-\s+`?((?:[A-Za-z]:[\\/]|/)[^`\r\n]+?)`?\s*$", re.MULTILINE)

MANIFEST_TEMPLATE = """# Loki guest allowlist

The paths listed below are the ONLY things guests (non-owner channel callers)
can read. Everything else on this machine is tool-denied for them.
Edits apply on the next request — no restart needed.
An empty list means guests can only see this folder (fail-closed).

## Allowed paths
<!-- One per line. Top-level folders under your WORK_DIR, e.g. -->
<!-- - C:\\work\\docs -->
<!-- - C:\\work\\shared-reports -->

## Guest guide
<!-- Loki reads this file when answering guests — describe what each shared
     folder contains so it can point people to the right place. -->

## Rules
- A listed folder is shared IN ITS ENTIRETY — don't list folders with secrets.
- Owner DMs are unaffected; this only scopes guests.
"""


def loki_dir() -> Path:
    return Path(config.WORK_DIR) / "loki"


def manifest_file() -> Path:
    return loki_dir() / "loki.md"


def guest_settings_file() -> Path:
    return config.STATE / "guest_settings.json"


def ensure_manifest() -> None:
    """Create the folder + a template manifest on first boot (empty allowlist)."""
    d = loki_dir()
    d.mkdir(exist_ok=True)
    if not manifest_file().exists():
        manifest_file().write_text(MANIFEST_TEMPLATE, encoding="utf-8")
        config.log.info("guest manifest template created at %s", manifest_file())


def guest_scope() -> tuple[list[str], str]:
    """Parse the manifest → (deny pattern list, manifest text for the prompt)."""
    try:
        manifest = manifest_file().read_text(encoding="utf-8")
    except Exception:
        manifest = ""
    work = str(Path(config.WORK_DIR).resolve()).replace("\\", "/")
    allowed = {"loki"}                        # the manifest folder is always visible
    for m in _PATH_LINE_RE.finditer(manifest):
        p = m.group(1).replace("\\", "/").rstrip("/")
        try:
            rp = str(Path(p).resolve()).replace("\\", "/")
        except Exception:
            continue
        if rp.lower().startswith(work.lower() + "/"):
            allowed.add(rp[len(work) + 1:].split("/")[0])    # top-level name only

    # static denies: no skills/shell/subagents, no ~/.claude, no other roots
    pats = ["Skill", "Bash", "Task",
            f"Read({_CLAUDE_HOME_GLOB})", f"Grep({_CLAUDE_HOME_GLOB})",
            f"Glob({_CLAUDE_HOME_GLOB})"]
    for root in ("C:/Users/**", "C:/ProgramData/**", "C:/Windows/**",
                 "D:/**", "E:/**"):
        pats += [f"Read({root})", f"Grep({root})", f"Glob({root})"]
    # dynamic denies: everything under WORK_DIR the manifest didn't allow
    try:
        entries = os.listdir(work)
    except Exception:
        entries = []
    for name in entries:
        if name in allowed:
            continue
        glob = (f"{work}/{name}/**" if os.path.isdir(os.path.join(work, name))
                else f"{work}/{name}")
        pats += [f"Read({glob})", f"Grep({glob})", f"Glob({glob})"]
    return pats, manifest


def write_guest_settings() -> tuple[str, str]:
    """Build denies, persist the per-request settings JSON.

    Returns (settings_file_path, manifest_text)."""
    denies, manifest = guest_scope()
    guest_settings_file().write_text(
        json.dumps({"permissions": {"deny": denies}}), encoding="utf-8")
    return str(guest_settings_file()), manifest
