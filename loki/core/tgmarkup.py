"""Markdown → Telegram HTML.

Same job as ``mrkdwn.py`` does for Slack: Claude answers in CommonMark and the
platform speaks something else, so raw `**bold**` and `[links](url)` would show
their punctuation instead of rendering.

HTML rather than MarkdownV2 because MarkdownV2 requires escaping a dozen
characters *outside* markup too — one stray `.` or `-` in a model answer and
Telegram rejects the whole message. HTML needs only `& < >` escaped, and the
adapter still retries as plain text if a send is ever refused.

Telegram HTML supports: <b> <i> <u> <s> <code> <pre> <a href>. No headings, no
lists, no tables — those become bold lines, bullets, and preformatted blocks.
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```(?:[\w+-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_TOKEN = "\x00{}\x00"


def to_html(text: str) -> str:
    if not text:
        return text

    text = _wrap_tables(text)

    # Stash code first: its contents must be escaped but never marked up.
    stash: list[str] = []

    def _stash(html: str) -> str:
        stash.append(html)
        return _TOKEN.format(len(stash) - 1)

    def _fence(m: "re.Match") -> str:
        return _stash(f"<pre>{_escape(m.group(1).rstrip())}</pre>")

    def _inline(m: "re.Match") -> str:
        return _stash(f"<code>{_escape(m.group(1))}</code>")

    text = _FENCE_RE.sub(_fence, text)
    text = _INLINE_CODE_RE.sub(_inline, text)

    text = _escape(text)

    # headings → bold line (Telegram has none)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", r"<b>\1</b>", text)

    # links before emphasis, so an underscore inside a URL survives
    text = re.sub(r"!?\[([^\]]+)\]\((\S+?)\)", r'<a href="\2">\1</a>', text)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!_)__(.+?)__(?!_)", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_(?!_)([^_\n]+?)_(?![\w_])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # bullets (numbered lists already read fine)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", text)

    for i, code in enumerate(stash):
        text = text.replace(_TOKEN.format(i), code)
    return text


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_separator(line: str) -> bool:
    s = line.strip()
    return "-" in s and bool(re.fullmatch(r"\|?[\s:|-]+\|?", s))


def _wrap_tables(text: str) -> str:
    """Wrap pipe tables in a fence so columns line up in monospace."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if "|" in lines[i]:
            j = i
            while j < len(lines) and "|" in lines[j]:
                j += 1
            block = lines[i:j]
            if len(block) >= 2 and any(_is_separator(b) for b in block):
                out.append("```")
                out.extend(block)
                out.append("```")
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def chunks(text: str, limit: int = 4000):
    """Split for Telegram's 4096-char cap, preferring line boundaries.

    Splitting inside a <pre> block would leave an unclosed tag and Telegram
    would reject the part, so a fence is closed and reopened across the seam.
    """
    text = text or ""
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        head, text = text[:cut], text[cut:]
        if head.count("<pre>") > head.count("</pre>"):
            head += "</pre>"
            text = "<pre>" + text
        yield head
        text = text.lstrip("\n")
    if text:
        yield text
