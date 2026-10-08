"""A ranked search index, asked where to look — never what to say.

Optional (``LOKI_SEARCH_INDEX``): a SQLite full-text knowledge base in
context-mode's layout — ``sources(id, label)`` plus the FTS5 tables ``chunks``
(porter) and ``chunks_trigram`` — whose chunk titles start with the file each
chunk came from: ``[tag] path > heading > subheading``. A knowledge base built
from a bundle with one ``# path`` heading per file has exactly that shape.

:func:`places` ranks (path, headings) for some search words, the way
context-mode itself does: BM25 with titles weighted, porter and trigram results
fused by reciprocal rank. :mod:`loki.core.allowread` then decides whether each
path is shared and reads the section from the file with :func:`section`, so
nothing the index stores reaches the model, and an index a day old can rank a
page too low but cannot put stale words in an answer.

Any failure — no file, a locked or rebuilt database, another layout — yields no
places, and search carries on without them. The database is opened read-only.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import config
from .config import log

FETCH = 30                  # rows per table before fusion
MAX_WORDS = 12
_RRF_K = 60                 # context-mode's constant
_TITLE = re.compile(r"^(?:\[[^\]]*\]\s+)?(.+?)(?: > (.+))?$")
_PART = re.compile(r" \(\d+\)$")       # an oversized chunk's pieces: "Title (2)"
_HEADING = re.compile(r"^(#{1,4})\s+(.+)$")   # what context-mode splits on


def _quote(word: str) -> str:
    return '"' + word.replace('"', '""') + '"'


def _titles(con: sqlite3.Connection, table: str, query: str) -> list[str]:
    where = f"{table} MATCH ?"
    args: tuple = (query,)
    if config.SEARCH_INDEX_SOURCE:
        where += " AND sources.label = ?"
        args += (config.SEARCH_INDEX_SOURCE,)
    sql = (f"SELECT {table}.title FROM {table} "
           f"JOIN sources ON sources.id = {table}.source_id WHERE {where} "
           f"ORDER BY bm25({table}, 5.0, 1.0) LIMIT {FETCH}")
    return [t for (t,) in con.execute(sql, args)]


def _ranked(con: sqlite3.Connection, words: list[str]) -> list[str]:
    """Titles, best first. A prefix match lets ``회의`` find ``회의록``; the
    trigram table finds a word inside a longer one (three characters and up)."""
    queries = [("chunks", " OR ".join(_quote(w) + "*" for w in words))]
    long = [w for w in words if len(w) >= 3]
    if long:
        queries.append(("chunks_trigram", " OR ".join(_quote(w) for w in long)))
    score: dict[str, float] = {}
    for table, query in queries:
        for i, title in enumerate(_titles(con, table, query)):
            score[title] = score.get(title, 0.0) + 1 / (_RRF_K + i + 1)
    return sorted(score, key=lambda t: -score[t])


def places(words: list[str]) -> list[tuple[str, list[str]]]:
    """(path as the index wrote it, headings) for the words, best first."""
    words = list(dict.fromkeys(w for w in words if re.search(r"\w", w)))[:MAX_WORDS]
    if not config.SEARCH_INDEX or not words:
        return []
    try:
        uri = Path(config.SEARCH_INDEX).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2)
    except (OSError, ValueError, sqlite3.Error) as e:
        log.warning("search index unavailable: %s", e)
        return []
    try:
        titles = _ranked(con, words)
    except sqlite3.Error as e:
        log.warning("search index unavailable: %s", e)
        return []
    finally:
        con.close()
    out = []
    for title in titles:
        m = _TITLE.match(_PART.sub("", title.strip()))
        if m:
            heads = [h.strip() for h in (m.group(2) or "").split(" > ") if h.strip()]
            out.append((m.group(1).strip(), heads))
    return out


def _plain(heading: str) -> str:
    return _PART.sub("", " ".join(heading.split())).lower()


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line, level, name) of each heading outside a code block."""
    out, fence = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        m = None if fence else _HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), _plain(m.group(2))))
    return out


def section(text: str, headings: list[str]) -> str | None:
    """The chunk of a Markdown text that the index ranked: the last of
    ``headings`` — each found inside the one before it, so ``B > Setup`` is the
    Setup under B — up to the next heading, as context-mode cuts it. A heading
    with no text of its own runs on to the next one at its level or above, so a
    bare ``## Tables`` shows the tables under it. With no headings, the text
    before the first heading. None when the text no longer has them."""
    lines = text.splitlines()
    heads = _headings(lines)
    if not headings:
        end = heads[0][0] if heads else len(lines)
        return "\n".join(lines[:end]).strip() or None
    want = [_plain(h) for h in headings]
    matched: list[int] = []         # levels of the headings found so far
    for n, (start, level, name) in enumerate(heads):
        while matched and level <= matched[-1]:
            matched.pop()           # left the parent's section: look again from it
        if name != want[len(matched)]:
            continue
        matched.append(level)
        if len(matched) == len(want):
            rest = heads[n + 1:]
            end = rest[0][0] if rest else len(lines)
            if not "".join(lines[start + 1:end]).strip():
                end = next((i for i, lv, _ in rest if lv <= level), len(lines))
            return "\n".join(lines[start:end]).strip()
    return None
