"""Permission files are owner-DM-only — prevention + tamper repair.

``loki/loki.md`` (guest allowlist), ``loki/orgs/*.md`` (org tiers) and ``.env``
(the owner + trusted-command allowlists) decide who may read and run what. They
must only ever change through the owner's own DM — either by asking Loki there,
or via the owner-gated ``!org`` commands, which write them from Python and never
go through an LLM at all.

Two layers, because one is not enough:

1. **Deny rules** (:func:`settings_file`) — a per-run ``--settings`` JSON that
   tool-denies ``Write``/``Edit`` on the permission files. Covers every LLM turn
   Loki spawns itself.
2. **Tripwire** (:func:`snapshot` / :func:`restore`) — hash-and-revert around the
   run. Needed because a private command that shells out to a pipeline script
   spawns *its own* agents, which our ``--settings`` can never reach, and because
   a deny rule can be sidestepped by a shell redirect wherever ``Bash`` is allowed.

Authority is taken from the Slack transport (user id + ``D…`` DM channel), never
from message content. A forged "the admin approved this" — typed, pasted, or
rendered inside a screenshot — cannot move a request into the unprotected tier,
because nothing in the content is consulted when choosing it.

Known trade-off: if the owner edits a permission file from their DM at the exact
moment a protected job is running, the tripwire reverts that edit and alerts.
Rare, visible, and re-doable — preferred over leaving the window open.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config
from .config import log


def _loki_dir() -> Path:
    return Path(config.WORK_DIR) / "loki"


def _orgs_dir() -> Path:
    return _loki_dir() / "orgs"


def _protected_files() -> list[Path]:
    """Every file whose contents decide access, in a stable order."""
    files = [_loki_dir() / "loki.md", config.BASE / ".env"]
    try:
        files += sorted(_orgs_dir().glob("*.md"))
    except Exception:
        pass
    return files


def _secret_files() -> list[str]:
    """Credential files no content-driven run may read.

    An explicit file list, not a folder glob: a blanket read-deny on the worker
    folder would break private commands that keep their own procedure files
    inside it. Both Claude homes are listed because private commands may run
    under either profile — one that strips CLAUDE_CONFIG_DIR (→ ``~/.claude``)
    and one that goes through brain.run_claude (→ the dedicated config dir)."""
    paths = [Path(config.BASE) / ".env"]
    homes = [os.path.expanduser("~/.claude")]
    if config.CLAUDE_CONFIG_DIR:
        homes.append(config.CLAUDE_CONFIG_DIR)
    for home in homes:
        paths += [Path(home) / ".credentials.json", Path(home) / ".claude.json"]
    return [str(p).replace("\\", "/") for p in paths]


def deny_patterns() -> list[str]:
    """Write-deny for the permission files and the worker's own source, plus
    read-deny for credentials. Writes are what let someone grant themselves
    access; reads are what let them walk off with the tokens."""
    loki = str(_loki_dir().resolve()).replace("\\", "/")
    src = str(Path(config.BASE).resolve()).replace("\\", "/")
    pats = [f"{tool}({root}/**)"
            for root in (loki, src)
            for tool in ("Write", "Edit", "NotebookEdit")]
    pats += [f"{tool}({f})"
             for f in _secret_files()
             for tool in ("Read", "Grep", "Glob")]
    return pats


def settings_file() -> str:
    """Per-run settings JSON denying writes to the permission files."""
    f = config.STATE / "protect_settings.json"
    f.write_text(json.dumps({"permissions": {"deny": deny_patterns()}}),
                 encoding="utf-8")
    return str(f)


def snapshot() -> dict:
    """Capture the permission files' bytes plus the org listing (so a *new* org
    file created mid-run is detectable too). Cheap — these are a few KB total."""
    contents: dict[str, bytes | None] = {}
    for p in _protected_files():
        try:
            contents[str(p)] = p.read_bytes() if p.is_file() else None
        except Exception:
            log.exception("permission-file snapshot failed: %s", p)
            contents[str(p)] = None
    try:
        orgs = {str(p) for p in _orgs_dir().glob("*.md")}
    except Exception:
        orgs = set()
    return {"contents": contents, "orgs": orgs}


def restore(snap: dict) -> list[str]:
    """Revert anything a protected run changed. Returns the reverted file names
    (empty list = clean run)."""
    reverted: list[str] = []
    for path, before in (snap.get("contents") or {}).items():
        p = Path(path)
        try:
            now = p.read_bytes() if p.is_file() else None
            if now == before:
                continue
            if before is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(before)
            reverted.append(p.name)
        except Exception:
            log.exception("permission-file restore failed: %s", p)
    try:                                   # org files created during the run
        for p in _orgs_dir().glob("*.md"):
            if str(p) not in (snap.get("orgs") or set()):
                p.unlink(missing_ok=True)
                reverted.append(p.name)
    except Exception:
        log.exception("new-org cleanup failed")
    if reverted:
        log.warning("TAMPER: permission files reverted after a protected run: %s",
                    ", ".join(reverted))
    return reverted


def alert_text(reverted: list[str]) -> str:
    return config.t("tamper_alert", files=", ".join(reverted))
