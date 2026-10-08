"""What every provider shares: a clean environment, a spawn, a verdict.

A provider's job is narrow — turn (prompt, resume id, permission mode) into
``{text, session_id, error, reason}``. The parts that are the *same* for all of
them live here, because they are the parts that are easy to get subtly wrong:

* **the environment.** Loki is often started from inside a Claude Code session,
  and that parent hands down auth and session variables. Inherit them and the
  spawned CLI silently authenticates as something other than what the operator
  configured — a metered API key instead of a subscription, most expensively.
  :func:`clean_env` strips every such variable; a provider re-adds only the ones
  it means to use, so config always beats inheritance.
* **the spawn.** New process group (so a Ctrl-C in a terminal doesn't race the
  child, and ``tree_kill`` can take the whole tree), no console window on
  Windows, prompt via stdin rather than argv — the ``.cmd`` shims and Unicode
  make argv unreliable — and the live handle published to the job dict so
  ``!cancel`` reaches it.
* **the verdict.** "Did we run out of quota?" reads the same way whoever
  answered, and it is the one failure that must never look like a crash.
"""
from __future__ import annotations

import os
import signal
import subprocess

from .. import config
from ..config import ANSI, log, t

# Session state a parent agent session hands down to every child, whichever
# provider that child turns out to be. Loki is usually started from inside a
# Claude Code session, and these say "you are already inside a run".
_BASE_STRIP_PREFIXES = ("CLAUDE_CODE",)
_BASE_STRIP_EXACT = {"CLAUDECODE"}

# Phrases every vendor uses for "you have run out". Rate limits are a normal
# operating state for a flat-rate plan, not a fault, so they get their own
# reason and their own friendly message.
_QUOTA_MARKS = (
    "rate limit", "usage limit", "quota", "limit reached", "session limit",
    "hit your", "resource_exhausted", "429", "too many requests",
    "insufficient_quota", "over capacity",
    "out of credits",       # codex on a ChatGPT workspace whose credits ran out
)
_QUOTA_STATUS = (429, 529)


def clean_env(strip_prefixes: tuple[str, ...] = (),
              strip_exact: set[str] | frozenset[str] = frozenset(),
              extra: dict[str, str] | None = None) -> dict[str, str]:
    """The parent environment with a provider's auth variables removed.

    Each provider names what *it* must not inherit, rather than everything
    sharing one blanket list. The blanket version is tempting and wrong: an
    ``OPENAI_``/``GOOGLE_`` sweep also takes ``GOOGLE_APPLICATION_CREDENTIALS``
    out from under the MCP servers a spawned agent loads, breaking tools that
    have nothing to do with which model is answering.

    ``extra`` is applied *after* the strip, which is the point of the ordering:
    a provider that genuinely wants ``ANTHROPIC_BASE_URL`` (Kimi speaks the
    Anthropic wire protocol) sets it here and gets exactly its own value, never
    the one that happened to be in the parent shell.
    """
    prefixes = _BASE_STRIP_PREFIXES + tuple(strip_prefixes)
    exact = _BASE_STRIP_EXACT | set(strip_exact)
    env = {k: v for k, v in os.environ.items()
           if k not in exact and not k.startswith(prefixes)}
    env["PYTHONUTF8"] = "1"
    for k, v in (extra or {}).items():
        if v:
            env[k] = v
    return env


def tree_kill(pid: int) -> None:
    """Kill a spawned agent and everything it started.

    Agent CLIs are process *trees* — a node launcher, the agent, and whatever
    shell commands it ran. Killing only the pid we hold leaves the real work
    running, which is how a cancelled job keeps writing files.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, creationflags=config.NO_WINDOW)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                os.kill(pid, signal.SIGKILL)
    except Exception:
        log.exception("tree_kill failed")


def spawn(cmd: list[str], prompt: str, cwd: str | None,
          env: dict[str, str], job: dict | None,
          timeout: int | None = None) -> tuple[str, str, int, bool]:
    """Run one agent CLI to completion. Returns (stdout, stderr, rc, timed_out).

    The prompt goes in on stdin. Passing it as an argument looks simpler and
    breaks on the two things Loki does constantly: long text (Windows caps a
    command line at 8191 characters) and non-ASCII (the ``.cmd`` shims re-encode
    argv through the console code page).
    """
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | config.NO_WINDOW
    else:
        kw["start_new_session"] = True      # own group → killpg works

    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd or config.WORK_DIR or None,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            env=env, **kw,
        )
    except FileNotFoundError:
        raise
    if job is not None:
        job["proc"] = proc
    try:
        out, err = proc.communicate(input=prompt,
                                    timeout=timeout or config.TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        tree_kill(proc.pid)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return out or "", err or "", proc.returncode or -1, True
    finally:
        if job is not None:
            job.pop("proc", None)
    return out or "", err or "", proc.returncode, False


def trim(text: str, limit: int = 400) -> str:
    """A CLI's raw failure, cut down to the part a person can act on.

    When a provider fails outside its own JSON envelope there is nothing to
    parse and the raw stream is all we have — which for a node CLI means the
    message followed by a screenful of ``at process.processTicksAndRejections``.
    Relaying that whole thing into a chat window buries the one sentence that
    said what went wrong. Stack frames go, the message stays.
    """
    lines = []
    for raw in ANSI.sub("", text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("at ") or stripped.startswith("^"):
            continue                    # a stack frame, or a caret pointing at one
        lines.append(line)
    out = "\n".join(lines).strip()
    return out if len(out) <= limit else out[:limit].rstrip() + " …"


def quota_hit(text: str, api_status: int = 0) -> bool:
    """Does this failure mean "the plan is used up" rather than "it broke"?"""
    if api_status in _QUOTA_STATUS:
        return True
    return any(mark in (text or "").lower() for mark in _QUOTA_MARKS)


def ok(text: str, session_id: str | None, provider: str) -> dict:
    return {"text": text or t("empty"), "session_id": session_id,
            "error": False, "reason": "ok", "provider": provider}


def fail(text: str, session_id: str | None, provider: str,
         reason: str = "error") -> dict:
    return {"text": ANSI.sub("", text or "").strip() or t("empty"),
            "session_id": session_id, "error": True, "reason": reason,
            "provider": provider}


def timed_out(session_id: str | None, provider: str) -> dict:
    return {"text": t("timeout"), "session_id": session_id,
            "error": True, "reason": "timeout", "provider": provider}


def not_found(path: str, provider: str) -> dict:
    return {"text": t("provider_not_found", provider=provider, path=path),
            "session_id": None, "error": True, "reason": "error",
            "provider": provider}
