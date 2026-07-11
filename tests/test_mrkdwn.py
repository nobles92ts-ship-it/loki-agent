"""Markdown → Slack mrkdwn conversion."""
from loki.core.mrkdwn import to_mrkdwn


def test_bold():
    assert to_mrkdwn("a **bold** b") == "a *bold* b"
    assert to_mrkdwn("a __bold__ b") == "a *bold* b"


def test_italic():
    assert to_mrkdwn("a *it* b") == "a _it_ b"
    assert to_mrkdwn("a _it_ b") == "a _it_ b"        # already Slack italic


def test_bold_and_italic_together():
    assert to_mrkdwn("**b** and *i*") == "*b* and _i_"


def test_headers_become_bold():
    assert to_mrkdwn("# Title") == "*Title*"
    assert to_mrkdwn("### Sub heading") == "*Sub heading*"


def test_links():
    assert to_mrkdwn("see [docs](https://x.io/a)") == "see <https://x.io/a|docs>"
    assert to_mrkdwn("![alt](http://img/p.png)") == "<http://img/p.png|alt>"


def test_bullets():
    assert to_mrkdwn("- one\n- two") == "• one\n• two"
    assert to_mrkdwn("  * nested") == "  • nested"


def test_strikethrough():
    assert to_mrkdwn("~~gone~~") == "~gone~"


def test_inline_code_protected():
    assert to_mrkdwn("use `**not bold**` here") == "use `**not bold**` here"


def test_fenced_code_protected():
    src = "text\n```\n**x** and [a](b)\n```\nafter **y**"
    out = to_mrkdwn(src)
    assert "**x** and [a](b)" in out          # inside fence: untouched
    assert "after *y*" in out                 # outside fence: converted


def test_table_wrapped_in_fence():
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = to_mrkdwn(src)
    assert out.startswith("```")
    assert out.rstrip().endswith("```")
    assert "| a | b |" in out                 # cells preserved verbatim


def test_table_cells_not_transformed():
    src = "| **x** | y |\n|---|---|\n| 1 | 2 |"
    out = to_mrkdwn(src)
    assert "**x**" in out                      # protected inside the fence


def test_empty_and_plain():
    assert to_mrkdwn("") == ""
    assert to_mrkdwn("just text") == "just text"
