"""searchindex — a ranked index tells Loki where to look, never what to say.

What these pin: the section shown is read from the shared file itself, so the
index's own text never reaches the model; a page outside the shared folders is
dropped however high it ranks; and a missing or broken index leaves search
working exactly as before.
"""
import sqlite3

import pytest

from loki.core import allowread, config, searchindex
from tests.test_allowread import SECRET, tree  # noqa: F401  (fixture)


def _fts5_trigram() -> bool:
    try:
        sqlite3.connect(":memory:").execute(
            "CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        return True
    except sqlite3.Error:
        return False


pytestmark = pytest.mark.skipif(not _fts5_trigram(),
                                reason="this SQLite has no FTS5 trigram tokenizer")

# context-mode's knowledge-base layout (src/store.ts)
SCHEMA = """
CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
  chunk_count INTEGER NOT NULL DEFAULT 0, code_chunk_count INTEGER NOT NULL DEFAULT 0,
  indexed_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE VIRTUAL TABLE chunks USING fts5(title, content, source_id UNINDEXED,
  content_type UNINDEXED, tokenize='porter unicode61');
CREATE VIRTUAL TABLE chunks_trigram USING fts5(title, content, source_id UNINDEXED,
  content_type UNINDEXED, tokenize='trigram');
"""

PAGE = ("# Revive\n\nintro line\n\n## Rules\n\nrevive at 50% HP\n\n"
        "### Penalty\n\nrevive costs 1% exp\n\n## Other\n\nunrelated revive note\n")


def _index(db, chunks):
    """``chunks`` = (title, content, source label)."""
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    ids = {}
    for title, content, label in chunks:
        if label not in ids:
            ids[label] = con.execute("INSERT INTO sources(label) VALUES (?)",
                                     (label,)).lastrowid
        for table in ("chunks", "chunks_trigram"):
            con.execute(f"INSERT INTO {table}(title, content, source_id, content_type) "
                        "VALUES (?, ?, ?, 'prose')", (title, content, ids[label]))
    con.commit()
    con.close()


def _rel(p):
    """The drive-relative path a bundle heading carries: ``work/docs/x.md``."""
    return p.resolve().relative_to(p.resolve().anchor).as_posix()


@pytest.fixture
def indexed(tree, tmp_path, monkeypatch):
    page = tree["shared"] / "revive.md"
    page.write_text(PAGE, encoding="utf-8")
    db = tmp_path / "kb.db"
    monkeypatch.setattr(config, "SEARCH_INDEX", str(db))
    monkeypatch.setattr(config, "SEARCH_INDEX_SOURCE", "")
    return {**tree, "page": page, "db": db}


def test_the_section_the_index_ranks_is_shown_before_line_hits(indexed):
    _index(indexed["db"], [(f"[D] {_rel(indexed['page'])} > Rules", "revive HP", "kb")])
    out = allowread.search(indexed["roots"], ["revive"])
    # the chunk the index ranked: ## Rules up to the next heading, from the file
    assert out.startswith("### docs/revive.md > Rules\n## Rules\n\nrevive at 50% HP\n\n"
                          "docs/revive.md:")
    assert "docs/revive.md:15: unrelated revive note" in out   # line hits still follow


def test_a_piece_of_an_oversized_chunk_finds_its_heading(indexed):
    _index(indexed["db"], [(f"[D] {_rel(indexed['page'])} > Rules (2)", "revive", "kb"),
                           (f"[D] {_rel(indexed['page'])} > Rules (1)", "revive", "kb")])
    out = allowread.search(indexed["roots"], ["revive"])
    assert out.startswith("### docs/revive.md > Rules\n## Rules\n")
    assert out.count("### docs/revive.md > Rules") == 1      # one section, not two


def test_the_index_text_never_reaches_the_model(indexed):
    _index(indexed["db"], [(f"[D] {_rel(indexed['page'])} > Rules",
                            "INDEX-ONLY-CANARY revive", "kb")])
    out = allowread.search(indexed["roots"], ["revive"])
    assert "### docs/revive.md > Rules" in out
    assert "INDEX-ONLY-CANARY" not in out


def test_a_page_outside_the_shared_folders_is_dropped_however_it_ranks(indexed):
    secret = indexed["private"] / "secret.md"
    _index(indexed["db"], [
        (f"[D] {_rel(secret)}", "revive revive revive revive", "kb"),
        (f"[D] {_rel(indexed['page'])} > Rules", "revive", "kb")])
    out = allowread.search(indexed["roots"], ["revive"])
    assert SECRET not in out
    assert "secret.md" not in out
    assert out.startswith("### docs/revive.md > Rules")


def test_a_heading_gone_from_the_file_is_skipped(indexed):
    _index(indexed["db"], [(f"[D] {_rel(indexed['page'])} > Renamed", "revive", "kb")])
    out = allowread.search(indexed["roots"], ["revive"])
    assert "###" not in out.splitlines()[0]
    assert "docs/revive.md:7: revive at 50% HP" in out


def test_only_the_configured_source_is_consulted(indexed, monkeypatch):
    _index(indexed["db"], [
        (f"[D] {_rel(indexed['page'])} > Other", "revive revive", "everything"),
        (f"[D] {_rel(indexed['page'])} > Rules", "revive", "docs-only")])
    monkeypatch.setattr(config, "SEARCH_INDEX_SOURCE", "docs-only")
    out = allowread.search(indexed["roots"], ["revive"])
    assert "> Rules" in out
    assert "> Other" not in out


@pytest.mark.parametrize("kind", ["missing", "not a database"])
def test_a_missing_or_broken_index_leaves_search_as_it_was(indexed, kind):
    if kind == "not a database":
        indexed["db"].write_bytes(b"this is not sqlite" * 100)
    out = allowread.search(indexed["roots"], ["revive"])
    assert out.startswith("docs/revive.md:")
    assert indexed["db"].exists() == (kind == "not a database")   # never created


def test_a_breadcrumb_picks_the_heading_under_its_parent():
    text = "## A\n### Setup\nfrom a\n## B\n### Setup\nfrom b\n#### Deeper\nx\n"
    assert searchindex.section(text, ["B", "Setup"]) == "### Setup\nfrom b"
    assert searchindex.section(text, ["A", "Setup"]) == "### Setup\nfrom a"
    assert searchindex.section(text, ["C", "Setup"]) is None
    # A's section ends before any Setup — B's is not A's
    assert searchindex.section("## A\nx\n## B\n### Setup\ny\n", ["A", "Setup"]) is None


def test_a_bare_heading_runs_on_through_its_subsections():
    text = "## Table\n\n### Base\n10\n### Bonus\n20\n## Next\nz\n"
    assert searchindex.section(text, ["Table"]) == "## Table\n\n### Base\n10\n### Bonus\n20"
    assert searchindex.section(text, ["Table", "Base"]) == "### Base\n10"


def test_a_section_already_on_screen_is_not_shown_again(indexed):
    page = indexed["shared"] / "table.md"
    page.write_text("## Table\n\n### Base\nrevive base 10\n### Bonus\nrevive bonus 20\n",
                    encoding="utf-8")
    _index(indexed["db"], [(f"[D] {_rel(page)} > Table", "revive revive", "kb"),
                           (f"[D] {_rel(page)} > Table > Base", "revive", "kb")])
    out = allowread.search(indexed["roots"], ["revive"])
    assert out.startswith("### docs/table.md > Table\n## Table\n\n### Base\nrevive base 10")
    assert "> Table > Base" not in out


def test_a_title_without_headings_is_the_text_before_the_first_one():
    assert searchindex.section("lead\n\n## A\nbody\n", []) == "lead"
    assert searchindex.section("## A\nbody\n", []) is None


def test_a_heading_inside_a_code_block_is_not_a_heading():
    text = "## Real\nbefore\n```\n## fake\n```\nafter\n## Next\n"
    assert searchindex.section(text, ["Real"]) == "## Real\nbefore\n```\n## fake\n```\nafter"
