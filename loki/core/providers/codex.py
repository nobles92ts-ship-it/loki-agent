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

**Sealed runs.** Codex cannot carry Loki's per-request deny rules: on Windows
its sandbox reads the whole disk or, with a narrowed read set, refuses to start
(see docs/codex-migration.md). What it *can* do is run with no tools at all, and
:func:`run_sealed` is that mode — no user config (so no MCP servers), every
tool-bearing feature off (shell, code-mode exec, ChatGPT connectors, plugins,
sub-agent tools fall with them), a read-only sandbox and an empty child
environment behind that, and a process environment cut down to what the CLI
needs to find its own login. A sealed answer can only use what is in the
prompt. Measured, not assumed — the canary probes are in the migration doc.
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
SEALED = True           # …but run_sealed can serve a restricted request tool-less
PLAN = "ChatGPT Plus/Pro plan (flat) when signed in, not an API key"

_STRIP_EXACT = {"OPENAI_API_KEY", "OPENAI_BASE_URL"}

# Every feature that hands the model a tool. `apps` is the one that matters
# most: without it gone, --ignore-user-config still leaves the ChatGPT account's
# connectors (Atlassian, Gmail, Drive, Slack…) callable. code_mode_host off makes
# the remaining `exec` tool fail closed; sub-agents inherit this same set.
_SEALED_OFF = ("shell_tool", "unified_exec", "shell_snapshot", "code_mode_host",
               "apps", "plugins", "remote_plugin", "memories", "hooks",
               "computer_use", "browser_use", "browser_use_external",
               "in_app_browser", "multi_agent", "image_generation", "view_image",
               "goals", "tool_suggest", "skill_search", "workspace_dependencies")

# What the CLI itself needs to start and find its login under the user profile.
# Anything else in Loki's environment — Slack, Jira and Claude tokens — stays out.
_SEALED_ENV_KEEP = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA",
    "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "USERNAME", "COMPUTERNAME", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "OS", "CODEX_HOME", "HTTP_PROXY", "HTTPS_PROXY",
    "NO_PROXY", "SSL_CERT_FILE", "LANG",
}

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


def sealed_flags() -> list[str]:
    """No user config, no rules, no tool-bearing feature; read-only beneath."""
    flags = ["--ignore-user-config", "--ignore-rules",
             "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"',
             "-c", 'web_search="disabled"', "-c", "include_apply_patch_tool=false",
             "-c", 'shell_environment_policy.inherit="none"',
             "-c", "project_doc_max_bytes=0"]
    for feature in _SEALED_OFF:
        flags += ["--disable", feature]
    return flags


def sealed_env() -> dict[str, str]:
    """An allowlisted environment, not a stripped one: a new secret added to
    Loki's .env must not reach a sealed run just because nobody listed it."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _SEALED_ENV_KEEP}
    env["PYTHONUTF8"] = "1"
    return env


def sealed_cwd() -> str:
    """An empty folder outside any repo, so no AGENTS.md rides into the run."""
    path = os.path.join(tempfile.gettempdir(), "loki_codex_sealed")
    os.makedirs(path, exist_ok=True)
    return path


def _build(resume_id: str | None, permission_mode: str, last_file: str,
           flags: list[str] | None = None,
           images: tuple[str, ...] = ()) -> list[str]:
    cmd = [command(), "exec"]
    if resume_id:
        cmd += ["resume", resume_id]
    # `exec resume` does not accept --color; JSONL output needs no color flag.
    cmd += ["--json", "--skip-git-repo-check",
            "--output-last-message", last_file]
    cmd += _mode_flags(permission_mode) if flags is None else flags
    model = os.environ.get("CODEX_MODEL", "").strip()
    if model:
        cmd += ["--model", model]
    for image in images:
        cmd += ["--image", image]
    cmd.append("-")                     # prompt arrives on stdin
    return cmd


def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    if settings_file:
        return base.fail(config.t("provider_no_sandbox", provider=LABEL),
                         resume_id, NAME)
    return _run(prompt, resume_id, cwd, base.clean_env((), _STRIP_EXACT), job,
                permission_mode)


def run_sealed(prompt: str, resume_id: str | None, cwd: str | None = None,
               job: dict | None = None,
               images: tuple[str, ...] = ()) -> dict:
    """A restricted request with no tools at all. ``cwd`` is ignored on
    purpose — the caller's folder (a guest's loki dir) means nothing to a run
    that cannot read, and its AGENTS.md would only leak into the context.
    ``images`` are attached to the prompt itself, the one way a tool-less run
    can see a screenshot."""
    return _run(config.t("sealed_note") + prompt, resume_id, sealed_cwd(),
                sealed_env(), job, "", sealed_flags(), tuple(images))


def _run(prompt: str, resume_id: str | None, cwd: str | None,
         env: dict[str, str], job: dict | None, permission_mode: str,
         flags: list[str] | None = None, images: tuple[str, ...] = ()) -> dict:
    fd, last_file = tempfile.mkstemp(prefix="loki_codex_", suffix=".txt")
    os.close(fd)
    try:
        cmd = _build(resume_id, permission_mode, last_file, flags, images)
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
