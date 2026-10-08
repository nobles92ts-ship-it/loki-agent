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
granted folder, and only text files and spreadsheets (read as text) are
readable. A manifest folder outside WORK_DIR is a root here and nowhere else
(:func:`loki.core.scope.outside_root`).
"""
from __future__ import annotations

import itertools
import json
import os
import re
from pathlib import Path

from . import config, files, guard, scope
from .config import log

MAX_LISTED = 300            # entries shown to the model in turn 1
MAX_WALK = 5000             # files visited per walk, so a huge share can't stall a reply
MAX_READ_FILES = 6
MAX_FILE_CHARS = 12000
MAX_SEARCH_HITS = 40
MAX_HITS_PER_FILE = 8       # so one log or json cannot take every hit
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SHEET_BYTES = 8 * 1024 * 1024     # zipped: a 2 MB table unzips to far more
MAX_SEARCH_BYTES = 64 * 1024 * 1024   # a whole share, not just what sorts first
MAX_HIT_CHARS = 600                   # a table row together with its column names
MAX_TOTAL_CHARS = 40000
MAX_KEYWORDS = 5
MAX_REQUESTS = 2            # a second search can use the words the first one found

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml",
                 ".html", ".htm", ".xml", ".log", ".ini", ".toml", ".rst"}
SHEET_SUFFIXES = {".xlsx", ".xlsm"}
_sheets: dict = {}                    # (path, mtime, size) → rendered text
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
    """Reject a grant whose top-level folder became a link or left WORK_DIR —
    unless it is a folder outside WORK_DIR that the manifest may name."""
    try:
        work = Path(config.WORK_DIR).resolve(strict=True)
        if root.is_symlink() or _isjunction(str(root)):
            return None
        rr = root.resolve(strict=True)
        if not rr.is_dir():
            return None
        if is_under(rr, work):
            return rr
        return scope.outside_root(str(root))
    except (OSError, RuntimeError, ValueError):
        return None


def _valid_roots(roots: list[Path]) -> list[Path]:
    return [rr for r in roots if (rr := _valid_root(r)) is not None]


def inside(path: Path | str, roots: list[Path], _fixed=None) -> Path | None:
    """The resolved file when it is a readable text file under a root, else None.

    Resolution happens first, so a symlink or junction is judged by where it
    leads, not by where it sits. ``_fixed`` is a walk's ``(valid roots,
    excluded)``, worked out once per walk instead of once per file."""
    try:
        rp = Path(path).resolve(strict=True)
    except Exception:
        return None
    if not rp.is_file():
        return None
    valid, excluded = _fixed or (_valid_roots(roots), _excluded())
    grants = [rr for rr in valid if is_under(rp, rr)]
    if not grants:
        return None
    if any(is_under(rp, x) for x in excluded):
        return None
    # Direct read requests must obey the same hidden-directory boundary as
    # listing/search; otherwise a guessed path could reach .git or a vault.
    rel_parts = rp.relative_to(grants[0]).parts
    sheet = rp.suffix.lower() in SHEET_SUFFIXES
    if (any(_sensitive_part(part) for part in rel_parts)
            or rp.name.startswith("~")      # Office lock files, stray drafts
            or not (sheet or rp.suffix.lower() in TEXT_SUFFIXES)):
        return None
    try:
        if rp.stat().st_size > (MAX_SHEET_BYTES if sheet else MAX_FILE_BYTES):
            return None
    except OSError:
        return None
    return rp


def _render_sheets(sheets) -> str:
    """One line per row, each cell named by its column's first-row header, so a
    single search hit says which number is which."""
    out = []
    for name, rows in sheets:
        if not rows:
            continue
        head = [" ".join(h.split()) for h in rows[0][1]]
        out.append(f"[{name} row {rows[0][0]}] " + " | ".join(h for h in head if h))
        for num, cells in rows[1:]:
            pairs = [f"{head[i] if i < len(head) and head[i] else _letters(i)}="
                     f"{' '.join(v.split())}" for i, v in enumerate(cells) if v.strip()]
            if pairs:
                out.append(f"[{name} row {num}] " + " | ".join(pairs))
    return "\n".join(out)


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _text(rp: Path) -> str | None:
    """A readable file's text — a spreadsheet rendered by :func:`_render_sheets`
    and kept until the file changes — or None if it changed, grew past its cap
    or cannot be parsed."""
    try:
        if rp.suffix.lower() not in SHEET_SUFFIXES:
            with rp.open("rb") as f:
                data = f.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                return None
            return data.decode("utf-8", errors="replace")
        st = rp.stat()
        key = (str(rp), st.st_mtime_ns, st.st_size)
        if key not in _sheets:
            with rp.open("rb") as f:
                data = f.read(MAX_SHEET_BYTES + 1)
            if len(data) > MAX_SHEET_BYTES:
                return None
            if len(_sheets) >= 256:
                _sheets.clear()
            _sheets[key] = _render_sheets(files.sheet_rows(data))
        return _sheets[key]
    except Exception:                       # gone, locked, or not really a zip
        return None


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
    fixed = (_valid_roots(roots), _excluded())
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
                rp = inside(Path(dirpath) / name, roots, fixed)
                if rp is not None:
                    yield rp


def _isjunction(p: str) -> bool:
    fn = getattr(os.path, "isjunction", None)
    return bool(fn and fn(p))


def listing(roots: list[Path]) -> str:
    """Up to MAX_LISTED files shared fairly between the roots: a small folder is
    listed whole and leaves its unused slots to the bigger ones."""
    found = [list(itertools.islice(_walk([r]), MAX_LISTED + 1)) for r in roots]
    take = [0] * len(found)
    left = MAX_LISTED
    order = sorted(range(len(found)), key=lambda i: len(found[i]))
    for n, i in enumerate(order):
        take[i] = min(len(found[i]), left // (len(order) - n))
        left -= take[i]
    names = []
    for root, rps, k in zip(roots, found, take):
        names += [_label(rp, roots) for rp in rps[:k]]
        if len(rps) > k:
            names.append(f"… ({root.name}: more not listed — use search)")
    return "\n".join(names)


def _squash(s: str) -> str:
    """``회의 노트`` and ``회의_노트.md`` compare equal."""
    return re.sub(r"[\s_\-]+", "", s.lower())


def _scores(line: str, lows: list[str], words: list[list[str]]) -> list[int]:
    """Per keyword: 2 when the line has it as written, 1 when it has all of its
    words apart ("가입 회비" in "가입 월 회비"), else 0."""
    low = line.lower()
    return [2 if k in low else 1 if all(w in low for w in ws) else 0
            for k, ws in zip(lows, words)]


def search(roots: list[Path], keywords: list) -> str:
    """Matching lines, most relevant file first: the one whose name carries the
    most keywords, then the one matching the most different keywords, then the
    most lines. Walk order only breaks ties. Within a file, the lines matching
    the most keywords, most exactly, come first."""
    kws = [str(k).strip()[:60] for k in keywords[:MAX_KEYWORDS] if str(k).strip()]
    if not kws:
        return ""
    lows = [k.lower() for k in kws]
    words = [k.split() for k in lows]
    squashed = [s for k in lows if (s := _squash(k))]
    ranked = []
    scanned = 0
    for rp in _walk(roots):
        remaining = MAX_SEARCH_BYTES - scanned
        if remaining <= 0:
            break
        text = _text(rp)
        if text is None:
            continue
        text = text[:remaining]
        scanned += len(text)
        low = text.lower()
        if not any(all(w in low for w in ws) for ws in words):
            continue                        # most files: no line to look at
        found = [(s, i, line) for i, line in enumerate(text.splitlines())
                 if any(s := _scores(line, lows, words))]
        if found:
            named = sum(k in _squash(rp.name) for k in squashed)
            kinds = sum(any(s[j] for s, _, _ in found) for j in range(len(lows)))
            ranked.append((-named, -kinds, -len(found), len(ranked), rp, found))
    hits: list[str] = []
    for *_, rp, found in sorted(ranked):
        best = sorted(found, key=lambda f: (-sum(f[0]), f[1]))
        for _, i, line in best[:MAX_HITS_PER_FILE]:
            hits.append(f"{_label(rp, roots)}:{i + 1}: {line.strip()[:MAX_HIT_CHARS]}")
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
        text = _text(rp)
        if text is None:
            blocks.append(f"### {label}\n(not readable: file changed, unavailable, "
                          "past the size limit or not parseable)")
            continue
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

    Turn 1 sees the file listing and either answers or asks; each later turn
    sees what Loki read and answers — or, once, asks again with other words the
    first results taught it. A failed turn is returned as is. ``images`` ride
    on turn 1 with the request; later turns resume that session."""
    files = listing(roots)
    note = config.t("allowread_note", listing=files or "(empty)")
    res = mod.run_sealed(note + prompt, resume_id, job=job, images=images)
    for round_no in range(1, MAX_REQUESTS + 1):
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
        last = round_no == MAX_REQUESTS
        res = mod.run_sealed(config.t("allowread_result" if last else "allowread_more",
                                      material=body),
                             res.get("session_id"), job=job)
    return res
