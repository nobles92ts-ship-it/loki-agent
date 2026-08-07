"""Command aliases — markdown parsing, expansion, and owner CRUD."""
import pytest

from loki.core import alias


@pytest.fixture
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"
    (w / "loki").mkdir(parents=True)
    monkeypatch.setattr(alias.config, "WORK_DIR", str(w))
    alias._invalidate()
    return w


def write(work, body):
    alias.aliases_file().write_text(body, encoding="utf-8")
    alias._invalidate()


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_simple_items():
    items = alias.parse("## Aliases\n- standup: summarize commits\n- x: do x\n")
    assert items == {"standup": "summarize commits", "x": "do x"}


def test_parse_ignores_other_sections():
    items = alias.parse("## Notes\n- nope: not an alias\n\n## Aliases\n- yes: ok\n")
    assert items == {"yes": "ok"}


def test_parse_skips_commented_examples():
    items = alias.parse("## Aliases\n<!-- - demo: example -->\n- real: go\n")
    assert items == {"real": "go"}


def test_parse_joins_continuation_lines():
    items = alias.parse("## Aliases\n- report: line one\n  line two\n  line three\n")
    assert items["report"] == "line one\nline two\nline three"


def test_parse_continuation_stops_at_blank_line():
    items = alias.parse("## Aliases\n- a: one\n\n  stray text\n- b: two\n")
    assert items == {"a": "one", "b": "two"}


def test_parse_prompt_may_contain_colons():
    items = alias.parse("## Aliases\n- note: remember: buy milk\n")
    assert items["note"] == "remember: buy milk"


def test_parse_rejects_reserved_and_bad_names():
    items = alias.parse("## Aliases\n- stop: hijack\n- 취소: hijack\n"
                        "- way-too-long-a-name-that-keeps-going-and-going: x\n"
                        "- ok: fine\n")
    assert items == {"ok": "fine"}


def test_parse_first_definition_wins():
    items = alias.parse("## Aliases\n- dup: first\n- dup: second\n")
    assert items["dup"] == "first"


def test_parse_drops_empty_templates():
    assert alias.parse("## Aliases\n- empty:\n- real: x\n") == {"real": "x"}


def test_parse_accepts_korean_names():
    assert alias.parse("## Aliases\n- 보고서: 주간보고 초안\n") == {"보고서": "주간보고 초안"}


def test_parse_junk_never_raises():
    assert alias.parse("") == {}
    assert alias.parse("## Aliases\n- \n-\nrandom text\n") == {}


# ── expansion ────────────────────────────────────────────────────────────────
def test_expand_placeholder_and_append():
    assert alias.expand("review {args} please", "PR 12") == "review PR 12 please"
    assert alias.expand("do the thing", "now") == "do the thing now"
    assert alias.expand("do the thing", "") == "do the thing"
    assert alias.expand("review {args}", "") == "review"


# ── file-backed reads ────────────────────────────────────────────────────────
def test_get_reads_the_file(work):
    write(work, "## Aliases\n- standup: summarize\n")
    assert alias.get("standup") == "summarize"
    assert alias.get("STANDUP") == "summarize"     # case-insensitive
    assert alias.get("ghost") is None


def test_edits_apply_without_restart(work):
    write(work, "## Aliases\n- a: one\n")
    assert alias.get("a") == "one"
    write(work, "## Aliases\n- a: two\n")
    assert alias.get("a") == "two"


def test_missing_file_fails_closed(work):
    assert alias.all_items() == {}


# ── owner CRUD ───────────────────────────────────────────────────────────────
def test_add_creates_and_reads_back(work):
    alias.ensure_file()
    assert alias.add("standup", "summarize yesterday") == "added"
    assert alias.get("standup") == "summarize yesterday"


def test_add_without_existing_file(work):
    assert alias.add("fresh", "go") == "added"
    assert alias.get("fresh") == "go"


def test_add_replaces_without_duplicating(work):
    alias.add("a", "first")
    assert alias.add("a", "second") == "replaced"
    assert alias.get("a") == "second"
    # encoding is explicit: the template Loki writes has non-ASCII in it, and a
    # cp949/cp1252 default read raises rather than compares.
    assert alias.aliases_file().read_text(encoding="utf-8").count("- a:") == 1


def test_add_multiline_round_trips(work):
    assert alias.add("report", "line one\nline two") == "added"
    assert alias.get("report") == "line one\nline two"


def test_add_rejects_reserved_and_bad_names(work):
    assert alias.add("stop", "x") == "reserved"
    assert alias.add("체크", "x") == "reserved"
    assert alias.add("bad name", "x") == "badname"
    assert alias.add("ok", "  ") == "empty"


def test_remove(work):
    alias.add("a", "one\ntwo")
    alias.add("b", "three")
    assert alias.remove("a") is True
    assert alias.get("a") is None and alias.get("b") == "three"
    assert "two" not in alias.aliases_file().read_text(encoding="utf-8")
    assert alias.remove("a") is False


def test_ensure_file_is_idempotent_and_empty(work):
    alias.ensure_file()
    alias.aliases_file().write_text("## Aliases\n- keep: me\n", encoding="utf-8")
    alias.ensure_file()
    assert alias.get("keep") == "me"          # never overwrites an existing file
