"""Attachments — what Loki accepts in, and what it may send out.

**Inbound** is a deny-by-default extension allowlist. A chat platform lets
anyone attach anything, so Loki only downloads types Claude can actually read
as pixels or text — never compiled binaries, installers or archives (opaque,
sometimes huge, and nothing Claude can usefully open anyway).

**Outbound** (`!send <path>`) resolves against ``WORK_DIR`` and refuses anything
that lands outside it. The fence is checked on the *resolved* path, so a
symlink inside WORK_DIR pointing elsewhere is rejected too.

Both directions are owner-only in the adapters; this module only decides what
is acceptable, never who is asking. (A document already posted in a thread is
another matter: the adapter reads it as part of that thread, for whoever asks
there — see :func:`document_text`.)
"""
from __future__ import annotations

import glob as _glob
import io
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import config
from .config import log

# Per-file cap for both directions. Slack allows far more; this keeps a stray
# attachment from filling the disk (inbound) or timing out a post (outbound).
MAX_FILE_MB = max(1, int(os.environ.get("LOKI_MAX_FILE_MB", "20")))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

MAX_SEND_FILES = 4          # files one !send may upload
MAX_INBOUND_FILES = 8       # attachments read from one message
INBOX_RETENTION_DAYS = 7    # downloaded attachments are pruned after this

IMAGE_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg",
}

# Documents and data Claude reads directly (PDF included — the Read tool
# handles it), plus source text worth reviewing.
DOC_EXTS = {
    # documents / data
    "pdf", "txt", "text", "md", "markdown", "rst", "csv", "tsv", "json",
    "jsonl", "ndjson", "yaml", "yml", "xml", "html", "htm", "log", "ics",
    "srt", "vtt", "diff", "patch",
    # office (readable through the matching Claude skill)
    "docx", "xlsx", "xlsm", "xls", "pptx", "ppt", "doc",
    # source text
    "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "kt", "swift",
    "c", "h", "cpp", "hpp", "cc", "cs", "go", "rs", "rb", "php", "pl",
    "lua", "r", "scala", "sh", "bash", "zsh", "ps1", "sql", "toml", "ini",
    "cfg", "conf", "properties", "gradle", "tf", "hcl", "vue", "svelte",
    "css", "scss", "less", "graphql", "proto", "dockerfile", "makefile",
}

# Never downloaded, whatever the mimetype claims: nothing here is readable as
# text, and several are directly executable.
BLOCKED_EXTS = {
    "exe", "dll", "msi", "bat", "cmd", "com", "scr", "vbs", "vbe", "js~",
    "jar", "apk", "app", "dmg", "iso", "img", "so", "dylib", "bin", "deb",
    "rpm", "pkg", "zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar",
    "cab", "lnk", "reg", "pyc", "pyo", "class", "o", "obj",
}

# Extensions the *outbound* auto-detector will pick out of a reply's text.
# `!send` is stricter about paths but looser about types: the owner naming a
# file explicitly is intent, an absolute path appearing mid-sentence is a guess.
REPLY_UPLOAD_EXTS = {
    "html", "htm", "png", "jpg", "jpeg", "gif", "svg", "pdf", "csv", "md",
    "txt", "json", "xlsx", "xls", "docx", "doc",
}

_SAFE_NAME_RE = re.compile(r"[^\w.\-]")
_GLOB_MAGIC_RE = re.compile(r"[*?\[]")
# Slack wraps things it thinks are links: <http://…|label> / <file:///c:/x>
_SLACK_LINK_RE = re.compile(r"^<(?:file://)?([^|>]+)(?:\|[^>]*)?>$")


def ext_of(name: str) -> str:
    """Lowercase extension without the dot ('' when there is none)."""
    base = os.path.basename((name or "").strip().rstrip("/\\"))
    if "." not in base:
        # Extensionless build files still classify (Dockerfile, Makefile).
        return base.lower() if base.lower() in ("dockerfile", "makefile") else ""
    return base.rsplit(".", 1)[1].lower()


def classify_inbound(name: str, mimetype: str = "") -> str | None:
    """'image' | 'doc' | None (rejected). Extension decides; mimetype only
    rescues extensionless text and images."""
    ext = ext_of(name)
    mt = (mimetype or "").lower()
    if ext in BLOCKED_EXTS:
        return None
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS:
        return "doc"
    if ext:
        return None                      # unknown extension → fail closed
    if mt.startswith("image/"):
        return "image"
    if mt.startswith("text/") or mt in ("application/json", "application/pdf"):
        return "doc"
    return None


def safe_filename(name: str, index: int = 0) -> str:
    """Platform-supplied name → a name safe to write into the inbox."""
    base = _SAFE_NAME_RE.sub("_", os.path.basename(name or "")).lstrip(".")
    base = base[-60:] or f"file{index}"
    return f"{int(time.time())}_{index}_{base}"


def prune_old(directory: Path, days: int = INBOX_RETENTION_DAYS) -> int:
    """Delete inbox files older than `days`. Returns how many were removed.

    Downloaded attachments are scratch data — Claude has already read them by
    the time the next request arrives — so the inbox never needs to grow.
    """
    cutoff = time.time() - max(1, days) * 86400
    removed = 0
    try:
        entries = list(directory.iterdir())
    except Exception:
        return 0
    for p in entries:
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except Exception:
            continue
    return removed


def _fence(path: Path, work: Path) -> bool:
    """True when `path` (already resolved) lives under `work`."""
    try:
        path.relative_to(work)
        return True
    except ValueError:
        return False


def resolve_outbound(spec: str, work_dir: str | None = None,
                     limit: int = MAX_SEND_FILES) -> tuple[list[Path], str, str]:
    """Resolve a `!send` argument to real files under WORK_DIR.

    Accepts an absolute path, a path relative to WORK_DIR, or a glob. Returns
    ``(paths, error_key, detail)`` — ``error_key`` is '' on success and an i18n
    key otherwise. Every returned path is an existing regular file, inside
    WORK_DIR, and within the size cap.
    """
    raw = (spec or "").strip()
    m = _SLACK_LINK_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    raw = raw.strip("\"'`").strip()
    if not raw:
        return [], "send_usage", ""

    try:
        work = Path(work_dir or config.WORK_DIR).resolve()
    except Exception:
        return [], "send_usage", ""

    candidate = Path(raw)
    pattern = str(candidate if candidate.is_absolute() else work / raw)

    if _GLOB_MAGIC_RE.search(raw):
        try:
            hits = sorted(_glob.glob(pattern, recursive=True))
        except Exception:
            hits = []
        if not hits:
            return [], "send_not_found", raw
    else:
        hits = [pattern]

    out: list[Path] = []
    too_big: Path | None = None
    for hit in hits:
        try:
            rp = Path(hit).resolve()
        except Exception:
            continue
        if not _fence(rp, work):
            return [], "send_outside", str(rp)
        try:
            if not rp.is_file():
                continue
            if rp.stat().st_size > MAX_FILE_BYTES:
                too_big = rp
                continue
        except Exception:
            continue
        if rp not in out:
            out.append(rp)
        if len(out) >= limit:
            break

    if not out:
        if too_big is not None:
            return [], "send_too_big", too_big.name
        return [], "send_not_found", raw
    return out, "", ""


# Absolute paths (Windows or POSIX) ending in an output-ish extension.
_REPLY_PATH_RE = re.compile(
    r'(?:[A-Za-z]:\\|/)[^\s"\'`<>|]+?\.(?:' +
    "|".join(sorted(REPLY_UPLOAD_EXTS)) + r')\b',
    re.IGNORECASE)


def find_reply_files(text: str, work_dir: str | None = None,
                     limit: int = MAX_SEND_FILES) -> list[Path]:
    """Absolute paths a reply mentions that are safe to auto-upload.

    Deliberately narrower than `!send`: this is inference, not instruction, so
    only known output extensions under WORK_DIR qualify.
    """
    try:
        work = Path(work_dir or config.WORK_DIR).resolve()
    except Exception:
        return []
    out: list[Path] = []
    for m in _REPLY_PATH_RE.finditer(text or ""):
        if len(out) >= limit:
            break
        try:
            rp = Path(m.group(0)).resolve()
        except Exception:
            continue
        if rp in out or ext_of(rp.name) not in REPLY_UPLOAD_EXTS:
            continue
        if not _fence(rp, work):
            continue
        try:
            if not rp.is_file() or rp.stat().st_size > MAX_FILE_BYTES:
                continue
        except Exception:
            continue
        out.append(rp)
    return out


def inbox_dir(name: str) -> Path:
    """state/<name>/ — created on demand, pruned on write."""
    d = config.STATE / name
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.exception("inbox dir create failed: %s", d)
    return d


# ─────────────── reading a document for a run that cannot ───────────────
# A sealed run has no tools: a file it is shown by path is a file it cannot
# open. Loki reads what has a standard-library reader and hands over the text;
# the rest is named, not guessed at.
DOC_TEXT_CHARS = 12000                  # one document's share of a prompt
NO_TEXT_READER = {"pdf", "doc", "xls", "ppt"}   # would need a parser installed
_PART_MAX_BYTES = 20 * 1024 * 1024      # one unzipped XML part (zip bombs stop)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def document_text(data: bytes, name: str) -> str | None:
    """A document's text, or None when there is no reader for it here.

    Text is decoded as UTF-16 when it carries that BOM, else UTF-8, else CP949
    — Korean Windows tools still write logs in it. docx, xlsx and pptx are
    zipped XML, read with the standard library. pdf and the legacy binary
    office formats would need a parser this install does not have.
    """
    ext = ext_of(name)
    if ext in NO_TEXT_READER:
        return None
    if ext in ("docx", "xlsx", "xlsm", "pptx"):
        try:
            return _ooxml_text(data, ext)
        except Exception:                   # not really a zip, or a part is off
            return None
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", errors="replace")
    else:
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("utf-8", errors="replace")
    return None if "\x00" in text else text      # binary behind a text name


def clip_text(text: str, limit: int) -> str:
    """Keep a long text's start and its end — a log's error is usually last."""
    if len(text) <= limit:
        return text
    head = limit // 3
    return (f"{text[:head]}\n…[{len(text) - limit} chars omitted]…\n"
            f"{text[-(limit - head):]}")


def _part(z: zipfile.ZipFile, name: str) -> ET.Element:
    if z.getinfo(name).file_size > _PART_MAX_BYTES:
        raise ValueError(f"{name} is too large to unzip")
    return ET.fromstring(z.read(name))


def _col(ref: str) -> int:
    """A cell reference's column: 'C5' → 2."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _ooxml_text(data: bytes, ext: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if ext == "docx":
            body = _part(z, "word/document.xml")
            return "\n".join("".join(t.text or "" for t in p.iter(_W + "t"))
                             for p in body.iter(_W + "p"))
        if ext == "pptx":
            slides = sorted((n for n in z.namelist()
                             if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
            return "\n\n".join(
                f"[slide {i}]\n"
                + "\n".join(t.text or "" for t in _part(z, n).iter(_A + "t"))
                for i, n in enumerate(slides, 1))
        # xlsx / xlsm: each sheet as tab-separated rows, cells in their columns
        return "\n\n".join(f"[sheet {name}]\n" + "\n".join("\t".join(c) for _, c in rows)
                           for name, rows in _sheet_rows(z))


def sheet_rows(data: bytes) -> list[tuple[str, list[tuple[int, list[str]]]]]:
    """An xlsx's sheets as ``(name, rows)``; a row is ``(row number, cells)``
    with each cell in its column. Empty rows are absent, so numbers can skip."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return _sheet_rows(z)


def _sheet_rows(z: zipfile.ZipFile) -> list[tuple[str, list[tuple[int, list[str]]]]]:
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(_X + "t"))
                  for si in _part(z, "xl/sharedStrings.xml").iter(_X + "si")]
    targets = {r.get("Id"): r.get("Target", "")
               for r in _part(z, "xl/_rels/workbook.xml.rels")}
    sheets = []
    for sheet in _part(z, "xl/workbook.xml").iter(_X + "sheet"):
        target = targets.get(sheet.get(_R + "id"), "")
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        rows = []
        for row in _part(z, path).iter(_X + "row"):
            cells: list[str] = []
            for c in row.iter(_X + "c"):
                if c.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(_X + "t"))
                else:
                    v = c.find(_X + "v")
                    value = (v.text or "") if v is not None else ""
                    if c.get("t") == "s" and value:
                        value = shared[int(value)]
                col = _col(c.get("r")) if c.get("r") else len(cells)
                cells += [""] * (col + 1 - len(cells))
                cells[col] = value
            num = row.get("r")
            prev = rows[-1][0] if rows else 0
            rows.append((int(num) if num and num.isdigit() else prev + 1, cells))
        sheets.append((sheet.get("name"), rows))
    return sheets
