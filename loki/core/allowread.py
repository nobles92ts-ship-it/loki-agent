"""Guest file access for sealed runs — Loki reads, the model never touches disk.

A sealed run (``providers.codex.run_sealed``) has no tools, which is what makes
it safe and also what makes it useless for "what's in the shared docs folder?".
This module gives that back without giving a tool back: Python lists, searches
and reads files *only under the folders the guest manifest grants*, applies
hard caps, and hands the result to the model as text in the prompt.

The containment rule is one function, :func:`inside`. Every path is resolved —
symlinks and junctions followed — before it is compared with the resolved
roots, so a link inside a shared folder that points out of it resolves out of
it and is refused. Directory walks do not follow links at all.

What the model still decides is *what to ask for*, through a two-turn exchange
(:func:`run`): it either answers, or replies with a JSON request naming listed
paths to read and keywords to search. The request is data; nothing in it can
widen the roots, which come only from the manifest.

Narrower than Claude's deny rules on purpose: the worker's own tree, the org
registry, credential files and secret-looking names are excluded even inside a
granted folder, and only text files are readable.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import config, guard
from .config import log

MAX_LISTED = 300            # entries shown to the model in turn 1
MAX_WALK = 5000             # files visited per walk, so a huge share can't stall a reply
MAX_READ_FILES = 6
MAX_FILE_CHARS = 12000
MAX_SEARCH_HITS = 40
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_BYTES = 8 * 1024 * 1024
MAX_TOTAL_CHARS = 40000
MAX_KEYWORDS = 5

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml",
                 ".html", ".htm", ".xml", ".log", ".ini", ".toml", ".rst"}
_SECRET_NAME = re.compile(
    r"(^\.env|\.pem$|\.key$|\.p12$|\.pfx$|\.kdbx$|id_rsa|id_ed25519|credential|"
    r"secret|token|password|\.sqlite|\.db$)", re.I)


def _sensitive_part(name: str) -> bool:
    return (name.startswith(".") or bool(_SECRET_NAME.search(name))
            or name.lower() in {"private", "_no_sync"})


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


def is_under(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([_norm(path), _norm(root)]) == _norm(root)
    except ValueError:                      # different drives
        return False


def _excluded() -> list[Path]:
    """Never readable, even inside a granted folder."""
    out = [Path(config.WORK_DIR) / "loki" / "orgs", Path(config.BASE),
           Path(os.path.expanduser("~/.claude"))]
    if config.CLAUDE_CONFIG_DIR:
        out.append(Path(config.CLAUDE_CONFIG_DIR))
    out += [Path(f) for f in guard._secret_files()]
    resolved = []
    for p in out:
        try:
            resolved.append(p.resolve())
        except Exception:
            continue
    return resolved


def _valid_root(root: Path) -> Path | None:
    """Reject a grant whose top-level folder became a link or left WORK_DIR."""
    try:
        work = Path(config.WORK_DIR).resolve(strict=True)
        if root.is_symlink() or _isjunction(str(root)):
            return None
        rr = root.resolve(strict=True)
        if not rr.is_dir() or not is_under(rr, work):
            return None
        return rr
    except (OSError, RuntimeError, ValueError):
        return None


def inside(path: Path | str, roots: list[Path]) -> Path | None:
    """The resolved file when it is a readable text file under a root, else None.

    Resolution happens first, so a symlink or junction is judged by where it
    leads, not by where it sits."""
    try:
        rp = Path(path).resolve(strict=True)
    except Exception:
        return None
    if not rp.is_file():
        return None
    grants = [rr for r in roots if (rr := _valid_root(r)) is not None
              and is_under(rp, rr)]
    if not grants:
        return None
    if any(is_under(rp, x) for x in _excluded()):
        return None
    # Direct read requests must obey the same hidden-directory boundary as
    # listing/search; otherwise a guessed path could reach .git or a vault.
    rel_parts = rp.relative_to(grants[0]).parts
    if (any(_sensitive_part(part) for part in rel_parts)
            or rp.suffix.lower() not in TEXT_SUFFIXES):
        return None
    try:
        if rp.stat().st_size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    return rp


def _label(rp: Path, roots: list[Path]) -> str:
    for r in roots:
        rr = _valid_root(r)
        if rr is not None and is_under(rp, rr):
            return (r.name + "/" + os.path.relpath(rp, rr)).replace("\\", "/")
    return rp.name


def _from_label(label: str, roots: list[Path]) -> Path | None:
    """``<root name>/<relative path>`` → the file, or None if it leaves the root."""
    label = str(label).replace("\\", "/").strip().lstrip("/")
    head, _, rest = label.partition("/")
    for r in roots:
        if r.name.lower() == head.lower() and rest:
            return inside(r / rest, roots)
    return None


def _walk(roots: list[Path]):
    """Readable files under the roots, never following a link out."""
    seen = 0
    for root in roots:
        if _valid_root(root) is None:
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if not _sensitive_part(d)
                and not os.path.islink(os.path.join(dirpath, d))
                and not _isjunction(os.path.join(dirpath, d)))
            for name in sorted(filenames):
                seen += 1
                if seen > MAX_WALK:
                    return
                rp = inside(Path(dirpath) / name, roots)
                if rp is not None:
                    yield rp


def _isjunction(p: str) -> bool:
    fn = getattr(os.path, "isjunction", None)
    return bool(fn and fn(p))


def listing(roots: list[Path]) -> str:
    names = []
    for rp in _walk(roots):
        names.append(_label(rp, roots))
        if len(names) >= MAX_LISTED:
            names.append("… (more not listed — use search)")
            break
    return "\n".join(names)


def search(roots: list[Path], keywords: list) -> str:
    kws = [str(k).strip()[:60] for k in keywords[:MAX_KEYWORDS] if str(k).strip()]
    if not kws:
        return ""
    lows = [k.lower() for k in kws]
    hits: list[str] = []
    scanned = 0
    for rp in _walk(roots):
        remaining = MAX_SEARCH_BYTES - scanned
        if remaining <= 0:
            break
        try:
            with rp.open("rb") as f:
                data = f.read(min(MAX_FILE_BYTES, remaining) + 1)
            scanned += len(data)
            lines = data[:remaining].decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if any(k in line.lower() for k in lows):
                hits.append(f"{_label(rp, roots)}:{i + 1}: {line.strip()[:240]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    return "\n".join(hits)
    return "\n".join(hits)


def read(roots: list[Path], labels: list) -> str:
    blocks = []
    for label in labels[:MAX_READ_FILES]:
        rp = _from_label(label, roots)
        if rp is None:
            blocks.append(f"### {label}\n(not readable: outside the shared folders, "
                          "not a text file, or not found)")
            continue
        try:
            with rp.open("rb") as f:
                data = f.read(MAX_FILE_BYTES + 1)
        except OSError:
            blocks.append(f"### {label}\n(not readable: file changed or unavailable)")
            continue
        if len(data) > MAX_FILE_BYTES:
            blocks.append(f"### {label}\n(not readable: file grew beyond the size limit)")
            continue
        text = data.decode("utf-8", errors="replace")
        cut = "" if len(text) <= MAX_FILE_CHARS else "\n… (truncated)"
        blocks.append(f"### {_label(rp, roots)}\n{text[:MAX_FILE_CHARS]}{cut}")
    return "\n\n".join(blocks)


def _request(text: str) -> dict | None:
    """Turn 1's reply, when it is a file request rather than an answer."""
    s = (text or "").strip()
    if s.startswith("```"):                 # ```json … ``` around the object
        s = s.strip("`").partition("\n")[2].strip()
    if not s.startswith("{"):
        return None                         # prose → it is the answer
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if not isinstance(obj, dict) or not ({"read", "search"} & obj.keys()):
        return None
    return {"read": [x for x in obj.get("read") or [] if isinstance(x, str)],
            "search": [x for x in obj.get("search") or [] if isinstance(x, str)]}


def run(mod, prompt: str, resume_id: str | None, roots: list[Path],
        job: dict | None = None, images: tuple = ()) -> dict:
    """A sealed turn that may read the shared folders through Loki.

    Turn 1 sees the file listing and either answers or asks; turn 2 (only when
    asked) sees what Loki read and answers. A failed turn 1 is returned as is.
    ``images`` ride on turn 1 with the request; turn 2 resumes that session."""
    files = listing(roots)
    note = config.t("allowread_note", listing=files or "(empty)")
    res = mod.run_sealed(note + prompt, resume_id, job=job, images=images)
    if res.get("error"):
        return res
    req = _request(res.get("text", ""))
    if req is None:
        return res
    material = []
    if req["search"]:
        material.append("## search\n" + (search(roots, req["search"]) or "(no hits)"))
    if req["read"]:
        material.append("## files\n" + read(roots, req["read"]))
    body = "\n\n".join(material)[:MAX_TOTAL_CHARS] or "(nothing requested)"
    log.info("allowread: %d search terms, %d files, %d chars",
             len(req["search"]), len(req["read"]), len(body))
    return mod.run_sealed(config.t("allowread_result", material=body),
                          res.get("session_id"), job=job)
