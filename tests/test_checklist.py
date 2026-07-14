"""Checklist model — parsing, rendering, toggling, storage."""
from loki.core import checklist as C


# ── parse (body → title, items) ──────────────────────────────────────────────
def test_parse_title_and_items():
    title, items = C.parse("오늘 할 일:\n장보기\n운동\n보고서 제출")
    assert title == "오늘 할 일"
    assert items == ["장보기", "운동", "보고서 제출"]


def test_parse_no_title_each_line_is_item():
    title, items = C.parse("장보기\n운동")
    assert title is None
    assert items == ["장보기", "운동"]


def test_parse_single_line_comma_split():
    title, items = C.parse("장보기, 운동, 보고서 제출")
    assert title is None
    assert items == ["장보기", "운동", "보고서 제출"]


def test_parse_strips_bullets_and_numbers():
    _, items = C.parse("- 장보기\n2. 운동\n[ ] 보고서\n☐ 청소")
    assert items == ["장보기", "운동", "보고서", "청소"]


def test_parse_caps_items():
    _, items = C.parse("\n".join(f"item{i}" for i in range(C.MAX_ITEMS + 20)))
    assert len(items) == C.MAX_ITEMS


# ── construction / progress ──────────────────────────────────────────────────
def test_new_assigns_stable_ids_unchecked():
    cl = C.new("C1", "T", ["a", "b", "c"], "U1", now=1.0)
    assert [it["id"] for it in cl["items"]] == ["i1", "i2", "i3"]
    assert all(not it["checked"] for it in cl["items"])
    assert C.progress(cl) == (0, 3)


# ── mutation is immutable ────────────────────────────────────────────────────
def test_set_checked_returns_new_object():
    cl = C.new("C1", None, ["a", "b"], "U1", now=1.0)
    cl2 = C.set_checked(cl, C.refs_to_ids(cl, [2]), True)
    assert cl["items"][1]["checked"] is False       # original untouched
    assert cl2["items"][1]["checked"] is True
    assert C.progress(cl2) == (1, 2)


def test_refs_to_ids_ignores_out_of_range():
    cl = C.new("C1", None, ["a", "b"], "U1", now=1.0)
    assert C.refs_to_ids(cl, [1, 2, 5, 0]) == ["i1", "i2"]


def test_set_all():
    cl = C.new("C1", None, ["a", "b", "c"], "U1", now=1.0)
    assert C.progress(C.set_all(cl, True)) == (3, 3)
    assert C.progress(C.set_all(C.set_all(cl, True), False)) == (0, 3)


# ── toggle (one button click flips one item) ─────────────────────────────────
def test_toggle_item_flips_one():
    cl = C.new("C1", None, ["a", "b", "c"], "U1", now=1.0)
    cl2 = C.toggle_item(cl, "i2")
    assert {it["id"] for it in cl2["items"] if it["checked"]} == {"i2"}
    assert cl["items"][1]["checked"] is False        # original untouched
    cl3 = C.toggle_item(cl2, "i2")                    # toggle back off
    assert not any(it["checked"] for it in cl3["items"])


def test_toggle_item_unknown_id_noop():
    cl = C.new("C1", None, ["a"], "U1", now=1.0)
    assert C.toggle_item(cl, "nope") == cl


# ── conversational toggle grammar ────────────────────────────────────────────
def test_parse_toggle_check_variants():
    assert C.parse_toggle("완료 2") == ("check", [2], False)
    assert C.parse_toggle("2번 완료") == ("check", [2], False)
    assert C.parse_toggle("done 2 3") == ("check", [2, 3], False)
    assert C.parse_toggle("!check 완료 2") == ("check", [2], False)


def test_parse_toggle_uncheck_and_all():
    assert C.parse_toggle("취소 3") == ("uncheck", [3], False)
    assert C.parse_toggle("2 취소") == ("uncheck", [2], False)
    assert C.parse_toggle("다 완료") == ("check", [], True)


def test_parse_toggle_rejects_ordinary_text():
    # item content that merely contains a keyword must NOT read as a toggle
    assert C.parse_toggle("운동 완료 3회") == (None, [], False)
    assert C.parse_toggle("장보기") == (None, [], False)
    assert C.parse_toggle("보고서 제출") == (None, [], False)


def test_parse_toggle_keyword_without_number_has_no_refs():
    # caller requires refs-or-all, so a bare "완료" is a safe no-op there
    assert C.parse_toggle("완료") == ("check", [], False)


# ── rendering ────────────────────────────────────────────────────────────────
def test_render_blocks_one_button_per_item():
    cl = C.new("C1", "T", [f"a{i}" for i in range(5)], "U1", now=1.0)
    cl = C.set_checked(cl, ["i2"], True)
    blocks = C.render_blocks(cl)
    assert blocks[0]["type"] == "header"
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) == 5                       # one block per item
    assert action_blocks[1]["block_id"] == "chkitem::i2"
    btns = [b["elements"][0] for b in action_blocks]
    assert all(x["type"] == "button" and x["action_id"] == C.ACTION_ID for x in btns)
    assert btns[1]["value"] == "i2"
    assert btns[1]["text"]["text"].startswith("☑")       # checked → ☑ + primary
    assert btns[1].get("style") == "primary"
    assert btns[0]["text"]["text"].startswith("☐")       # unchecked → ☐, no style
    assert "style" not in btns[0]
    assert blocks[-1]["type"] == "context"


def test_render_empty_checklist():
    cl = C.new("C1", "T", [], "U1", now=1.0)
    blocks = C.render_blocks(cl)
    assert blocks[0]["type"] == "header"
    assert not any(b["type"] == "actions" for b in blocks)


def test_render_truncates_long_button_text():
    cl = C.new("C1", None, ["x" * 200], "U1", now=1.0)
    btn = C.render_blocks(cl)[1]["elements"][0]
    assert len(btn["text"]["text"]) <= C._OPT_TEXT_MAX


# ── storage ──────────────────────────────────────────────────────────────────
def test_save_load_roundtrip(tmp_path):
    cl = C.new("C1", "T", ["a", "b"], "U1", now=1.0)
    cl["message_ts"] = "100.1"
    C.save(tmp_path, cl)
    got = C.load_by_ts(tmp_path, "C1", "100.1")
    assert got["title"] == "T" and len(got["items"]) == 2


def test_find_target_by_message_ts(tmp_path):
    cl = C.new("C1", "T", ["a"], "U1", thread_ts=None, now=1.0)
    cl["message_ts"] = "100.1"
    C.save(tmp_path, cl)
    # a reply in the checklist's own thread → thread_ts == its message_ts
    assert C.find_target(tmp_path, "C1", "100.1")["message_ts"] == "100.1"
    assert C.find_target(tmp_path, "C1", "999.9") is None
    assert C.find_target(tmp_path, "C1", None) is None


def test_find_target_by_thread_ts(tmp_path):
    # created inside an existing thread: its own ts differs from thread root
    cl = C.new("C1", "T", ["a"], "U1", thread_ts="200.2", now=2.0)
    cl["message_ts"] = "200.9"
    C.save(tmp_path, cl)
    assert C.find_target(tmp_path, "C1", "200.2")["message_ts"] == "200.9"


def test_find_latest_newest_wins(tmp_path):
    for ts, created in [("1.1", 10.0), ("2.2", 30.0), ("3.3", 20.0)]:
        cl = C.new("C1", ts, ["a"], "U1", now=created)
        cl["message_ts"] = ts
        C.save(tmp_path, cl)
    assert C.find_latest(tmp_path, "C1")["message_ts"] == "2.2"   # created_at 30
    assert C.find_latest(tmp_path, "C2") is None
