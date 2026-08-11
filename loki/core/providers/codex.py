"""Codex CLI — OpenAI's agent, on a ChatGPT plan rather than an API key.

``~/.codex/auth.json`` records which of the two it is: ``auth_mode: chatgpt``
means the sign-in spends a ChatGPT Plus/Pro plan, while an ``OPENAI_API_KEY``
bills per token. Loki strips the key so the plan is what answers, and
:func:`available` reports the mode it found rather than assuming.

Two shapes to know about. Codex streams **JSONL events** rather than returning
one JSON object, so the conversation id arrives in ``thread.started`` at the
top and failures arrive as ``error`` / ``turn.failed`` at the bottom — this
reads the whole stream and takes the last word. And the final answer is fetched
via ``--output-last-message``, a file the CLI writes, instead of by guessing
which streamed event held the reply; the event names have moved between
versions and the file has not.

``codex exec resume`` accepts neither ``--sandbox`` nor ``--cd``, so the
permission mode travels as ``-c`` config overrides (accepted by both
subcommands) and the working directory travels as the spawn's own cwd.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from .. import config
from ..config import ANSI, log
from . import base

NAME = "codex"
LABEL = "Codex CLI"
SANDBOX = False         # no per-request deny rules → owner DM / console only
PLAN = "ChatGPT Plus/Pro plan (flat) when signed in, not an API key"

_STRIP_EXACT = {"OPENAI_API_KEY", "OPENAI_BASE_URL"}

# A refresh token that has already been spent, and other "log in again" states.
# Worth naming: it looks like a model failure in the stream but no amount of
# retrying fixes it.
_REAUTH = ("could not be refreshed", "please log out and sign in",
           "sign in again", "not logged in", "unauthorized")


def command() -> str:
    explicit = os.environ.get("CODEX_CMD", "").strip()
    return explicit or shutil.which("codex") or "codex"


def version() -> str:
    from . import _probe_version
    return _probe_version(command(), ["--version"])


def auth_mode() -> str:
    """chatgpt (a plan) · apikey (metered) · none — read from the CLI's own file."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    path = os.path.join(home, ".codex", "auth.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return "none"
    mode = (data.get("auth_mode") or "").strip().lower()
    if mode:
        return mode
    return "apikey" if data.get("OPENAI_API_KEY") else "none"


def available() -> tuple[bool, str]:
    if version() == "?":
        return False, "codex CLI not installed — `npm install -g @openai/codex`"
    mode = auth_mode()
    if mode == "none":
        return False, "codex is installed but not signed in — run `codex login`"
    if mode == "apikey":
        return True, "signed in with an API key — this bills per token"
    return True, ""


def _mode_flags(permission_mode: str) -> list[str]:
    """Loki's two permission modes, in the vocabulary both subcommands accept."""
    if permission_mode == "plan":
        return ["-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"']
    return ["--dangerously-bypass-approvals-and-sandbox"]


def _build(resume_id: str | None, permission_mode: str, last_file: str) -> list[str]:
    cmd = [command(), "exec"]
    if resume_id:
        cmd += ["resume", resume_id]
    cmd += ["--json", "--skip-git-repo-check", "--color", "never",
            "--output-last-message", last_file]
    cmd += _mode_flags(permission_mode)
    model = os.environ.get("CODEX_MODEL", "").strip()
    if model:
        cmd += ["--model", model]
    cmd.append("-")                     # prompt arrives on stdin
    return cmd


def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    if settings_file:
        return base.fail(config.t("provider_no_sandbox", provider=LABEL),
                         resume_id, NAME)

    fd, last_file = tempfile.mkstemp(prefix="loki_codex_", suffix=".txt")
    os.close(fd)
    try:
        cmd = _build(resume_id, permission_mode, last_file)
        env = base.clean_env((), _STRIP_EXACT)
        try:
            out, err, rc, expired = base.spawn(cmd, prompt, cwd, env, job)
        except FileNotFoundError:
            return base.not_found(command(), NAME)
        if expired:
            return base.timed_out(resume_id, NAME)
        return _parse(out, err, rc, resume_id, last_file)
    finally:
        try:
            os.unlink(last_file)
        except OSError:
            pass


def _events(out: str) -> list[dict]:
    events = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _error_of(event: dict) -> str:
    """The message out of the several shapes codex has used for failures."""
    for spot in (event.get("error"), event.get("msg"), event):
        if isinstance(spot, dict) and spot.get("message"):
            return str(spot["message"])
    return ""


def _parse(out: str, err: str, rc: int, resume_id: str | None,
           last_file: str) -> dict:
    sid, failure = resume_id, ""
    for ev in _events(out):
        kind = ev.get("type") or (ev.get("msg") or {}).get("type") or ""
        if kind in ("thread.started", "session.created", "session_configured"):
            sid = ev.get("thread_id") or ev.get("session_id") or sid
        elif kind in ("error", "turn.failed", "task.failed"):
            failure = _error_of(ev) or failure

    text = ""
    try:
        with open(last_file, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()
    except OSError:
        pass

    if not text and not failure and rc != 0:
        failure = ANSI.sub("", (err or "")).strip() or config.t("exit_code", rc=rc)
    if failure:
        low = failure.lower()
        if any(mark in low for mark in _REAUTH):
            log.warning("codex needs a fresh login: %s", failure[:160])
            return base.fail(config.t("provider_reauth", provider=LABEL,
                                      detail=failure[:200]), sid, NAME)
        return base.fail(failure, sid, NAME,
                         "quota" if base.quota_hit(failure) else "error")
    return base.ok(text, sid, NAME)
