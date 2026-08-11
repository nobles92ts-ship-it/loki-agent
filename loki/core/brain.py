"""The brain — one turn against whichever agent is currently answering.

This used to *be* the Claude spawn. It is now the seam in front of
:mod:`loki.core.providers`, which holds one module per agent CLI (Claude Code,
Gemini, Codex, Kimi, Groq) and the rule that keeps sandboxed requests on Claude.

The name and signature stayed. Every adapter, the scheduler, the self-test and
a fork's private commands all call ``run_claude``, and renaming it would have
bought nothing but a migration — so this is the same function it always was,
now asking the registry who should answer rather than assuming.
"""
from __future__ import annotations

from . import providers
from .providers import base

# Kill a spawned agent and its children. Re-exported because callers hold it as
# a value (``jobs.start(..., kill=brain.tree_kill)``).
tree_kill = base.tree_kill


def run_claude(prompt: str, resume_id: str | None,
               permission_mode: str | None = None,
               settings_file: str | None = None,
               cwd: str | None = None,
               job: dict | None = None,
               provider: str | None = None) -> dict:
    """Run one turn headless. Returns {text, session_id, error, reason, provider}.

    settings_file: per-request settings JSON (e.g. the guest allowlist's deny
    rules — too long for the command line, cmd.exe caps it at 8191 chars).
    Passing one also *selects* the provider: only an agent that can enforce
    those rules may serve the request, so it runs on Claude whatever the
    current switch says.
    cwd: working directory override (guests are pinned to the loki folder).
    job: the queue's job dict — the live proc handle is attached to it so
    !cancel <id> / !stop can tree-kill exactly the right process.
    provider: force one for this call, ignoring the switch (the self-test uses
    it to check a specific agent).
    """
    return providers.run(prompt, resume_id, permission_mode,
                         settings_file=settings_file, cwd=cwd, job=job,
                         provider=provider)


def claude_version() -> str:
    """The version of the agent currently answering (diagnostics, self-test)."""
    return providers.current().version()
