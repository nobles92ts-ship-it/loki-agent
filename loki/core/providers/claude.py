"""Claude Code — the default brain, and the only one that can hold a sandbox.

Auth comes from the machine's own login (``~/.claude``, or the config dir this
install pins), which is what makes Loki flat-rate: the same subscription that
answers in your terminal answers in Slack. ``ANTHROPIC_*`` is stripped on the
way in so an inherited API key can never quietly move that onto metered
billing.

This provider is also the only one that accepts ``settings_file``. Loki's guest
scope and its permission-file guard are both expressed as Claude Code deny
rules, so it is the one place where "this run may not read that" is enforced by
the tool layer rather than by asking nicely. :mod:`loki.core.providers` routes
any request carrying one back here for exactly that reason.
"""
from __future__ import annotations

import json

from .. import account, config
from ..config import ANSI, t
from . import base

NAME = "claude"
LABEL = "Claude Code"
SANDBOX = True          # honours settings_file → may serve guests
PLAN = "Claude Pro/Max subscription (flat)"

# Auth that must not be inherited: an API key or a pointed-elsewhere config dir
# changes who pays and who you are, silently.
_STRIP_PREFIXES = ("ANTHROPIC_",)
_STRIP_EXACT = {"CLAUDE_CONFIG_DIR"}


def command() -> str:
    return config.CLAUDE_CMD


def env_extra() -> dict[str, str]:
    """Config's own auth, re-applied after the strip so it always wins."""
    return {"CLAUDE_CONFIG_DIR": config.CLAUDE_CONFIG_DIR,
            "CLAUDE_CODE_OAUTH_TOKEN": account.token()}


def version() -> str:
    from . import _probe_version
    return _probe_version(command(), ["--version"])


def available() -> tuple[bool, str]:
    if version() == "?":
        return False, f"`{command()}` did not answer --version"
    return True, ""       # the note is the reason it is *not* ready, not a label


def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    cmd = [command(), "-p",
           "--permission-mode", permission_mode,
           "--output-format", "json",
           "--add-dir", config.WORK_DIR]
    if config.MODEL:
        cmd += ["--model", config.MODEL]
    if resume_id:
        cmd += ["--resume", resume_id]
    if settings_file:
        cmd += ["--settings", settings_file]

    env = base.clean_env(_STRIP_PREFIXES, _STRIP_EXACT, env_extra())
    try:
        out, err, rc, expired = base.spawn(cmd, prompt, cwd, env, job)
    except FileNotFoundError:
        return base.not_found(command(), NAME)
    if expired:
        return base.timed_out(resume_id, NAME)
    return _parse(out, err, rc, resume_id)


def _parse(out: str, err: str, rc: int, resume_id: str | None) -> dict:
    out = (out or "").strip()
    text, sid, is_err, api_status = "", resume_id, False, 0
    try:
        data = json.loads(out)
        text = (data.get("result") or "").strip()
        sid = data.get("session_id") or resume_id
        is_err = bool(data.get("is_error"))
        api_status = int(data.get("api_error_status") or 0)
    except Exception:
        text = ANSI.sub("", out)
    if rc != 0 and not text:
        text = ANSI.sub("", (err or "")).strip() or t("exit_code", rc=rc)
        is_err = True
    if base.quota_hit(text + " " + (err or ""), api_status):
        return base.fail(text, sid, NAME, "quota")
    return base.fail(text, sid, NAME) if is_err else base.ok(text, sid, NAME)
