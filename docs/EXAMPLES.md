# Extending Loki — run your own Claude Code skills

Loki's brain is the **full `claude` CLI**, launched in your `WORK_DIR`. So Loki can reach **anything your Claude Code can**: the skills, subagents, slash commands, and MCP servers installed under `~/.claude`. It is not limited to Q&A — it's a chat front-end to your entire Claude Code setup.

There are two ways to put that to work.

## 1. Just ask — skills you already have

If a skill or agent is installed in `~/.claude`, describe the task in plain language and Loki uses it, exactly as if you typed the request in a terminal.

```
(DM · owner · write mode)
you → run my release-notes skill for the last 10 commits
you → use the pdf skill to pull the tables out of report.pdf
```

**Caveat:** anything that writes files or runs commands needs **owner + write mode**. Guest channel mentions are always read-only (`plan`) and scoped to the paths shared in `loki.md` — they can look things up and summarize, but not run write-heavy pipelines or touch anything outside the shared scope.

## 2. Wire a fixed command — repeatable / long-running pipelines

For a heavy, multi-agent pipeline you run often, a one-tap `!command` beats retyping a paragraph — and lets you stream progress back while it runs for minutes or hours. You add these in **your own** deployment; they are **not** part of loki-agent core.

### Worked example: a QA test-case pipeline

One real deployment wired a command that turns **a spec document + a spreadsheet** into a full test-case suite, produced by a multi-agent pipeline (analyze → design → write → review → fix):

```
(channel or DM · trusted user)
you  → !qa  <spreadsheet-url>  <spec-url>
Loki → 🚀 started — this can take a while, I'll stream progress…
Loki → ▶ analyzing the spec…
Loki → ▶ [agent] writing test cases for feature X…
Loki → ✅ done — check the sheet.
```

The pipeline itself is an open-source Claude Code skill you can install and point Loki at:

> **https://github.com/nobles92ts-ship-it/AI_GAME_QA_TestCase**
> A multi-agent QA test-case generation pipeline for Claude Code. Install it into `~/.claude`, then trigger it either in plain language (mode 1) or via a fixed command (mode 2).

The command handler is ~40 lines around `loki.core`: match a fixed pattern, restrict to trusted users, launch `claude -p` in a background thread with streaming output, and post throttled progress lines. Illustrative sketch (not shipped in core):

```python
# in your fork's Slack adapter — illustrative only
import re, threading
from loki.core import brain

TRUSTED = {"U0000OWNER", "U0000MATE"}      # who may run it
CMD = re.compile(r"^!qa\s+(\S+)\s+(\S+)")

def maybe_pipeline(job) -> bool:
    m = CMD.match(job["text"])
    if not m or job["user"] not in TRUSTED:
        return False
    sheet, spec = m.groups()
    threading.Thread(target=_run, args=(job, sheet, spec), daemon=True).start()
    return True

def _run(job, sheet, spec):
    prompt = f"Run the QA test-case pipeline.\nSheet: {sheet}\nSpec: {spec}"
    # brain.run_claude(..., permission_mode="bypassPermissions", track=False)
    # or spawn claude -p --output-format stream-json --verbose and post ▶ lines
    ...
```

Design notes for long-running jobs: run them off the serial queue (a background thread), use `track=False` so they don't clobber the main `!stop` slot, give them their own long timeout, and gate them to named trusted users only — never pattern-match permissions from free text.

## Keep it yours

Custom commands and the skills they call live in **your** deployment and your `~/.claude` — not in this repo. If you fork Loki to add commands, keep private URLs, spreadsheet IDs, internal tools, and credentials out of the public fork (see [SECURITY.md](SECURITY.md)). loki-agent core stays a generic bridge on purpose.
