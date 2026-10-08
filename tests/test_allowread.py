"""allowread — a sealed guest run reads shared folders through Loki, not tools.

The containment rule is what these pin: a path is judged by where it resolves,
so traversal, symlinks and junctions that lead out of a granted folder are
refused, and the worker's own tree and secret-looking files stay unreadable
even inside one. The model's request is data and cannot add a root.
"""
import os
import subprocess
from pathlib import Path

import pytest

from loki.core import allowread, config, providers, scope
from loki.core.providers import codex
from tests.test_thread_documents import XLSX

SECRET = "CANARY-OUTSIDE-5e1f"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    work = tmp_path / "work"
    shared = work / "docs"
    (shared / "sub").mkdir(parents=True)
    (shared / "guide.md").write_text("onboarding guide: build with make\n", encoding="utf-8")
    (shared / "sub" / "notes.txt").write_text("release notes 1.2\n", encoding="utf-8")
    (shared / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    (shared / "api_token.txt").write_text("x\n", encoding="utf-8")
    (shared / "image.png").write_bytes(b"\x89PNG")
    private = work / "private"
    private.mkdir()
    (private / "secret.md").write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "BASE", tmp_path / "worker")
    monkeypatch.setattr(config, "CLAUDE_CONFIG_DIR", "")
    return {"work": work, "shared": shared, "private": private,
            "roots": [shared]}


def _junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except OSError:
            return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True)
    return r.returncode == 0


# ── containment ─────────────────────────────────────────────────────────────
def test_a_shared_text_file_is_readable(tree):
    assert allowread.inside(tree["shared"] / "guide.md", tree["roots"])


def test_traversal_out_of_the_root_is_refused(tree):
    assert allowread.inside(tree["shared"] / ".." / "private" / "secret.md",
                            tree["roots"]) is None
    out = allowread.read(tree["roots"], ["docs/../private/secret.md"])
    assert SECRET not in out and "not readable" in out


def test_a_junction_out_of_the_root_is_refused(tree):
    link = tree["shared"] / "escape"
    if not _junction(link, tree["private"]):
        pytest.skip("cannot create a junction/symlink here")
    assert allowread.inside(link / "secret.md", tree["roots"]) is None
    assert SECRET not in allowread.read(tree["roots"], ["docs/escape/secret.md"])
    assert "escape" not in allowread.listing(tree["roots"])
    assert SECRET not in allowread.search(tree["roots"], ["CANARY"])


def test_a_granted_root_replaced_by_a_junction_is_refused(tree):
    manifest = f"## Allowed paths\n- {tree['shared']}\n"
    roots = scope.read_roots(manifest)
    original = tree["work"] / "docs_original"
    tree["shared"].rename(original)
    if not _junction(tree["shared"], tree["private"]):
        pytest.skip("cannot create a junction/symlink here")
    assert allowread.inside(tree["shared"] / "secret.md", roots) is None
    assert SECRET not in allowread.search(roots, ["CANARY"])
    assert scope.read_roots(manifest) == []


def test_a_file_symlink_out_of_the_root_is_refused(tree):
    link = tree["shared"] / "linked.md"
    try:
        os.symlink(tree["private"] / "secret.md", link)
    except OSError:
        pytest.skip("symlinks need developer mode/admin here")
    assert allowread.inside(link, tree["roots"]) is None
    assert SECRET not in allowread.read(tree["roots"], ["docs/linked.md"])


def test_secret_names_and_binaries_are_never_readable(tree):
    for name in (".env", "api_token.txt", "image.png"):
        assert allowread.inside(tree["shared"] / name, tree["roots"]) is None
    listed = allowread.listing(tree["roots"])
    assert "guide.md" in listed and ".env" not in listed and "api_token" not in listed


def test_direct_read_cannot_bypass_hidden_or_private_directories(tree):
    for name in (".git", "private", "credentials"):
        folder = tree["shared"] / name
        folder.mkdir()
        (folder / "notes.md").write_text(SECRET, encoding="utf-8")
        assert allowread.inside(folder / "notes.md", tree["roots"]) is None
        assert SECRET not in allowread.read(tree["roots"], [f"docs/{name}/notes.md"])


def test_the_worker_tree_stays_out_even_inside_a_grant(tree, monkeypatch):
    worker = tree["shared"] / "loki-agent"
    worker.mkdir()
    (worker / "state.md").write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(config, "BASE", worker)
    assert allowread.inside(worker / "state.md", tree["roots"]) is None
    assert SECRET not in allowread.search(tree["roots"], ["CANARY"])


def test_reads_are_capped(tree, monkeypatch):
    (tree["shared"] / "big.md").write_text("x" * 50000, encoding="utf-8")
    monkeypatch.setattr(allowread, "MAX_FILE_CHARS", 1000)
    out = allowread.read(tree["roots"], ["docs/big.md"])
    assert "truncated" in out and len(out) < 1200
    many = [f"docs/guide.md"] * 20
    assert allowread.read(tree["roots"], many).count("### ") == allowread.MAX_READ_FILES


def test_search_has_a_total_byte_budget(tree, monkeypatch):
    monkeypatch.setattr(allowread, "MAX_SEARCH_BYTES", 10)
    assert allowread.search(tree["roots"], ["release"]) == ""


def test_the_most_relevant_file_comes_first_wherever_it_sits(tree):
    # 2026-10-08: asked what happens at HP 0, a guest's search filled its 40
    # hits with TC artifacts that sort first; the design page itself, deep in
    # the walk, never appeared. Hits are ranked by relevance, not walk order.
    early = tree["shared"] / "aaa"
    early.mkdir()
    for i in range(50):
        (early / f"{i:02}.md").write_text("death mentioned once\n", encoding="utf-8")
    late = tree["shared"] / "zz_system"
    late.mkdir()
    (late / "design.md").write_text(
        "# death and revive\n" + "revive at town after death\n" * 3, encoding="utf-8")
    hits = allowread.search(tree["roots"], ["death", "revive"]).splitlines()
    assert hits[0].startswith("docs/zz_system/design.md:")


def test_a_page_named_for_the_topic_beats_a_dump_of_everything(tree):
    # The share also holds the whole wiki concatenated into one file; it matches
    # every keyword more often than the page that is actually about the topic.
    (tree["shared"] / "aaa_full.md").write_text("death and revive, revive\n" * 50,
                                                encoding="utf-8")
    page = tree["shared"] / "zz_system"
    page.mkdir()
    (page / "death_and_revive.md").write_text("# death and revive\nrevive in town\n",
                                              encoding="utf-8")
    hits = allowread.search(tree["roots"], ["death and revive", "revive"]).splitlines()
    assert hits[0].startswith("docs/zz_system/death_and_revive.md:")


def test_a_keyword_matches_its_words_apart(tree):
    # Asked for "가입 회비", the table said "동호회 가입 월 회비" — never adjacent.
    (tree["shared"] / "define.md").write_text(
        "ClubFee | 동호회 가입 월 회비 | 5000\n", encoding="utf-8")
    assert "5000" in allowread.search(tree["roots"], ["가입 회비"])


def test_the_best_line_of_a_file_comes_first(tree):
    (tree["shared"] / "guild.md").write_text(
        "guild member\n" * 20 + "guild setup cost 100\n", encoding="utf-8")
    hits = allowread.search(tree["roots"], ["guild", "setup cost"]).splitlines()
    assert hits[0] == "docs/guild.md:21: guild setup cost 100"


def test_one_file_cannot_take_every_search_hit(tree):
    (tree["shared"] / "aaa_log.md").write_text("release\n" * 100, encoding="utf-8")
    out = allowread.search(tree["roots"], ["release"])
    assert "docs/sub/notes.txt:1:" in out


def test_manifest_grants_become_roots(tree):
    manifest = f"## Allowed paths\n- {tree['shared']}\\sub\n"
    assert scope.read_roots(manifest) == [Path(str(tree["work"].resolve())) / "docs"]
    assert scope.read_roots("## Allowed paths\n") == []


# ── spreadsheets ────────────────────────────────────────────────────────────
def test_a_spreadsheet_row_is_found_with_its_column_names(tree):
    # A row alone is a list of numbers; the hit has to say which is which.
    (tree["shared"] / "table.xlsx").write_bytes(XLSX)
    assert "docs/table.xlsx:" in allowread.search(tree["roots"], ["PASS"])
    assert "[TC row 2] 항목=1 | 결과=PASS" in allowread.search(tree["roots"], ["PASS"])
    assert "결과=PASS" in allowread.read(tree["roots"], ["docs/table.xlsx"])
    assert "docs/table.xlsx" in allowread.listing(tree["roots"])


def test_office_temp_files_are_not_read(tree):
    # `~$x.xlsx` is Excel's lock file; `~x.xlsx` a stray draft beside the real one.
    for name in ("~$table.xlsx", "~table(draft).xlsx", "~notes.md"):
        (tree["shared"] / name).write_bytes(XLSX)
        assert allowread.inside(tree["shared"] / name, tree["roots"]) is None
    assert "~" not in allowread.listing(tree["roots"])


def test_a_broken_spreadsheet_is_skipped_not_fatal(tree):
    (tree["shared"] / "broken.xlsx").write_bytes(b"not a zip")
    assert "release notes" in allowread.search(tree["roots"], ["release"])
    assert "not readable" in allowread.read(tree["roots"], ["docs/broken.xlsx"])


# ── a folder outside WORK_DIR ───────────────────────────────────────────────
@pytest.fixture
def outside(tree, tmp_path):
    tables = tmp_path / "data" / "tables"
    tables.mkdir(parents=True)
    (tables / "Sample.xlsx").write_bytes(XLSX)
    (tmp_path / "data" / "beside.md").write_text(SECRET, encoding="utf-8")
    return tables


def test_a_folder_outside_work_dir_is_granted_exactly(tree, outside):
    roots = scope.read_roots(f"- {tree['shared']}\n- {outside}\n")
    assert [r.name for r in roots] == ["docs", "tables"]
    assert "결과=PASS" in allowread.search(roots, ["PASS"])
    assert "tables/Sample.xlsx" in allowread.listing(roots)
    # exactly that folder — not its parent
    assert allowread.inside(outside.parent / "beside.md", roots) is None
    assert SECRET not in allowread.read(roots, ["tables/../beside.md"])


def test_a_big_folder_cannot_hide_a_small_one_in_the_listing(tree, outside, monkeypatch):
    # The design wiki alone filled all 300 listing slots, so the tables granted
    # beside it never showed up in turn 1.
    for i in range(30):
        (tree["shared"] / f"{i:02}.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(allowread, "MAX_LISTED", 10)
    roots = scope.read_roots(f"- {tree['shared']}\n- {outside}\n")
    listed = allowread.listing(roots).splitlines()
    assert "tables/Sample.xlsx" in listed
    assert sum(not line.startswith("…") for line in listed) == 10
    assert any(line.startswith("… (docs:") for line in listed)


def test_an_outside_grant_cannot_hold_work_dir(tree, tmp_path):
    # One line naming a parent would reopen every folder the list keeps shut.
    for grant in (tmp_path, Path(tmp_path.anchor)):
        roots = scope.read_roots(f"- {grant}\n")
        assert roots == []
        assert SECRET not in allowread.search([Path(grant)], ["CANARY"])


def test_an_outside_grant_through_a_link_is_refused(tree, outside, tmp_path):
    link = tmp_path / "linked_tables"
    if not _junction(link, outside):
        pytest.skip("cannot create a junction/symlink here")
    assert scope.read_roots(f"- {link}\n") == []
    assert allowread.search([link], ["PASS"]) == ""


def test_an_outside_grant_stays_denied_to_a_tool_using_guest(tree, outside, monkeypatch):
    # Claude carries the deny list instead of reading through Loki: other
    # drives and user profiles stay closed there, granted or not.
    # The deny list lists WORK_DIR's ancestors, here inside the system temp
    # folder, which other programs keep changing: both calls see one snapshot.
    listdir, seen = os.listdir, {}
    monkeypatch.setattr(os, "listdir", lambda p: seen.setdefault(str(p), listdir(p)))
    denies = scope._denies_for(f"- {outside}\n")
    assert scope._allowed_names(f"- {outside}\n",
                                str(tree["work"].resolve()).replace("\\", "/")) == set()
    assert denies == scope._denies_for("")


# ── the two-turn exchange ───────────────────────────────────────────────────
class _Sealed:
    """A stand-in provider recording each sealed turn."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def run_sealed(self, prompt, resume_id, cwd=None, job=None, images=()):
        self.prompts.append(prompt)
        r = self.replies.pop(0)
        return r if isinstance(r, dict) else {"text": r, "session_id": "s1",
                                              "error": False, "reason": "ok",
                                              "provider": "codex"}


def test_an_answer_in_turn_one_is_final(tree):
    mod = _Sealed("hello")
    assert allowread.run(mod, "hi", None, tree["roots"])["text"] == "hello"
    assert len(mod.prompts) == 1 and "guide.md" in mod.prompts[0]


def test_a_request_gets_a_second_turn_with_only_shared_content(tree):
    req = ('{"read": ["docs/guide.md", "private/secret.md", "C:/Windows/win.ini"],'
           ' "search": ["release"]}')
    mod = _Sealed(req, "answer")
    res = allowread.run(mod, "what's in the guide?", None, tree["roots"])
    assert res["text"] == "answer" and len(mod.prompts) == 2
    material = mod.prompts[1]
    assert "build with make" in material and "release notes 1.2" in material
    assert SECRET not in material and "not readable" in material


def test_a_second_search_may_use_words_the_first_one_found(tree):
    # Asked what joining costs, the model searched one word for it; the docs it
    # got back used another, and only that word finds the table row.
    first = '{"search": ["guide"]}'
    second = '{"search": ["release"]}'
    mod = _Sealed(first, second, "answer")
    res = allowread.run(mod, "q", None, tree["roots"])
    assert res["text"] == "answer" and len(mod.prompts) == 3
    assert "onboarding guide" in mod.prompts[1] and "release notes" in mod.prompts[2]
    assert "more" in mod.prompts[1].lower() or "더" in mod.prompts[1]


def test_requests_stop_after_the_second(tree):
    req = '{"search": ["guide"]}'
    mod = _Sealed(req, req, req, "never asked")
    assert allowread.run(mod, "q", None, tree["roots"])["text"] == req
    assert len(mod.prompts) == 3


def test_a_failed_first_turn_is_returned_without_a_second(tree):
    mod = _Sealed({"text": "limit", "session_id": None, "error": True,
                   "reason": "quota", "provider": "codex"})
    assert allowread.run(mod, "hi", None, tree["roots"])["reason"] == "quota"
    assert len(mod.prompts) == 1


def test_prose_around_json_is_an_answer_not_a_request():
    assert allowread._request('Sure! {"read": ["docs/a.md"]}') is None
    assert allowread._request('```json\n{"read": ["docs/a.md"]}\n```') == \
        {"read": ["docs/a.md"], "search": []}


def test_sealed_guest_routing_uses_allowread(tree, tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "sealed")
    providers.set_current("codex")
    seen = {}
    monkeypatch.setattr(allowread, "run",
                        lambda mod, p, r, roots, job=None, images=():
                        seen.update(roots=roots) or {"provider": "codex"})
    providers.run("hi", None, "plan", settings_file="C:/deny.json",
                  read_roots=tree["roots"])
    assert seen["roots"] == tree["roots"]
    monkeypatch.setattr(codex, "run_sealed",
                        lambda *a, **k: {"provider": "codex", "plain": True})
    assert providers.run("hi", None, "plan", settings_file="C:/deny.json")["plain"]
