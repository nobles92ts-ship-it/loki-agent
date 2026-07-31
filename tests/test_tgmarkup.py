"""Markdown → Telegram HTML, and chunking that never leaves a tag open."""
from loki.core import tgmarkup


def h(s):
    return tgmarkup.to_html(s)


# ── escaping ─────────────────────────────────────────────────────────────────
def test_html_is_escaped():
    assert h("5 < 6 & 7 > 2") == "5 &lt; 6 &amp; 7 &gt; 2"


def test_model_html_cannot_inject_tags():
    assert "<script>" not in h("<script>alert(1)</script>")
    assert "&lt;script&gt;" in h("<script>alert(1)</script>")


def test_code_contents_are_escaped_too():
    assert h("`<b>x</b>`") == "<code>&lt;b&gt;x&lt;/b&gt;</code>"


# ── markup ───────────────────────────────────────────────────────────────────
def test_bold_italic_strike():
    assert h("**bold**") == "<b>bold</b>"
    assert h("__bold__") == "<b>bold</b>"
    assert h("*it*") == "<i>it</i>"
    assert h("_it_") == "<i>it</i>"
    assert h("~~gone~~") == "<s>gone</s>"


def test_headings_become_bold():
    assert h("## Title") == "<b>Title</b>"


def test_links():
    assert h("[docs](https://x.dev)") == '<a href="https://x.dev">docs</a>'


def test_underscores_in_urls_survive():
    out = h("[a](https://x.dev/some_page_name)")
    assert "some_page_name" in out and "<i>" not in out


def test_snake_case_is_not_italic():
    assert "<i>" not in h("call load_user_data() first")


def test_bullets():
    assert h("- one\n- two") == "• one\n• two"


def test_fenced_code_block():
    assert h("```python\nprint(1)\n```") == "<pre>print(1)</pre>"


def test_markup_inside_code_is_left_alone():
    assert h("`**not bold**`") == "<code>**not bold**</code>"
    assert h("```\n**keep**\n```") == "<pre>**keep**</pre>"


def test_tables_become_preformatted():
    out = h("| a | b |\n|---|---|\n| 1 | 2 |")
    assert out.startswith("<pre>") and "| a | b |" in out


def test_empty_input():
    assert h("") == ""
    assert h(None) is None


# ── chunking ─────────────────────────────────────────────────────────────────
def test_short_text_is_one_chunk():
    assert list(tgmarkup.chunks("hello")) == ["hello"]


def test_chunks_respect_the_limit():
    text = "\n".join(f"line {i}" for i in range(500))
    parts = list(tgmarkup.chunks(text, limit=200))
    assert len(parts) > 1
    assert all(len(p) <= 220 for p in parts)          # +tag repair headroom


def test_chunks_prefer_line_boundaries():
    text = "\n".join("x" * 50 for _ in range(20))
    assert all(not p.startswith("\n") for p in tgmarkup.chunks(text, limit=120))


def test_split_inside_pre_repairs_the_tag():
    text = "<pre>" + "\n".join(f"log line {i}" for i in range(100)) + "</pre>"
    parts = list(tgmarkup.chunks(text, limit=200))
    assert len(parts) > 1
    for p in parts:                                    # every part is balanced
        assert p.count("<pre>") == p.count("</pre>")


def test_no_empty_chunks():
    assert all(p for p in tgmarkup.chunks("a\n\n\n\nb", limit=3))
