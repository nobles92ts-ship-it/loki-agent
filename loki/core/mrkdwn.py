"""Markdown → Slack mrkdwn.

Claude answers in CommonMark; Slack speaks its own dialect, so raw `**bold**`,
`# headers`, and `[links](url)` show their punctuation instead of rendering.
This converts a Claude reply to Slack's format. Code spans and fences are
protected — nothing inside them is transformed. Applied only to model output,
never to the bot's own (already-mrkdwn) command strings.

Slack mrkdwn: *bold* · _italic_ · ~strike~ · `code` · ```block``` · <url|text>
             • bullets · >quote  (no headers, no tables).
"""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_BOLD = "\x01"                    # placeholder while italic is resolved
_TOKEN = "\x00{}\x00"            # placeholder for stashed code


def to_mrkdwn(text: str) -> str:
    if not text:
        return text

    text = _wrap_tables(text)            # MD tables → fenced (monospace) block

    # protect code (fences first, then inline) so transforms never touch it
    stash: list[str] = []

    def _stash(m: "re.Match") -> str:
        stash.append(m.group(0))
        return _TOKEN.format(len(stash) - 1)

    text = _FENCE_RE.sub(_stash, text)
    text = _INLINE_CODE_RE.sub(_stash, text)

    # headers (#..######) → bold line (Slack has no headings). Use the bold
    # marker so the italic pass below doesn't re-read the emitted asterisks.
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$",
                  _BOLD + r"\1" + _BOLD, text)

    # bold **x** / __x__ → marker; italic *x* / stays _x_; marker → *
    text = re.sub(r"\*\*(.+?)\*\*", _BOLD + r"\1" + _BOLD, text)
    text = re.sub(r"(?<!_)__(.+?)__(?!_)", _BOLD + r"\1" + _BOLD, text)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)", r"_\1_", text)
    text = text.replace(_BOLD, "*")

    # strikethrough ~~x~~ → ~x~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)

    # links [text](url) and images ![text](url) → <url|text>
    text = re.sub(r"!?\[([^\]]+)\]\((\S+?)\)", r"<\2|\1>", text)

    # bullets -, *, + → •  (numbered lists render fine as-is)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", text)

    for i, code in enumerate(stash):     # restore protected code verbatim
        text = text.replace(_TOKEN.format(i), code)
    return text


def _is_separator(line: str) -> bool:
    s = line.strip()
    return "-" in s and bool(re.fullmatch(r"\|?[\s:|-]+\|?", s))


def _wrap_tables(text: str) -> str:
    """Wrap contiguous pipe-table blocks in a code fence so columns line up
    (Slack has no table rendering)."""
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
