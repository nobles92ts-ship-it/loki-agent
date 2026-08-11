"""Kimi (Moonshot) — Claude Code's harness, pointed at someone else's model.

Moonshot serves an Anthropic-compatible endpoint, and Claude Code decides where
to send a request from three environment variables. So Kimi needs no adapter of
its own: it is the ``claude`` binary already on this machine, spawned against
``https://api.moonshot.ai/anthropic`` with a Moonshot token. Everything Loki
depends on — ``--resume``, the JSON envelope, permission modes — keeps working
because it is the same program.

The honest note on cost: ``KIMI_API_KEY`` is a platform key and platform keys
bill per token. Moonshot also sells a flat coding plan whose key is used the
same way; Loki cannot tell the two apart from here, so it does not claim to —
``!provider`` labels this one "depends on which key you hold" rather than
guessing.

The endpoint is configurable because Moonshot runs a separate one inside China
(``api.moonshot.cn``); pointing at the wrong one fails as an auth error, which
is a confusing way to learn about geography.
"""
from __future__ import annotations

import os

from .. import config
from . import base, claude

NAME = "kimi"
LABEL = "Kimi (Moonshot)"
SANDBOX = True          # it *is* Claude Code, so deny rules are honoured
PLAN = "Moonshot key — flat on a Kimi coding plan, metered on a platform key"

DEFAULT_BASE_URL = "https://api.moonshot.ai/anthropic"
DEFAULT_MODEL = "kimi-k2.5"

# The whole point is to replace these, so nothing inherited may survive — a
# leftover ANTHROPIC_AUTH_TOKEN from the parent would send Kimi's traffic to
# Anthropic and bill it there.
_STRIP_PREFIXES = ("ANTHROPIC_",)
_STRIP_EXACT = {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"}


def command() -> str:
    return config.CLAUDE_CMD        # same binary; only the destination differs


def api_key() -> str:
    return (os.environ.get("KIMI_API_KEY")
            or os.environ.get("MOONSHOT_API_KEY") or "").strip()


def base_url() -> str:
    return (os.environ.get("KIMI_BASE_URL") or "").strip() or DEFAULT_BASE_URL


def model() -> str:
    return (os.environ.get("KIMI_MODEL") or "").strip() or DEFAULT_MODEL


def env_extra() -> dict[str, str]:
    return {"ANTHROPIC_BASE_URL": base_url(),
            "ANTHROPIC_AUTH_TOKEN": api_key(),
            "ANTHROPIC_MODEL": model()}


def version() -> str:
    return claude.version()             # same binary


def available() -> tuple[bool, str]:
    if version() == "?":
        return False, f"`{command()}` did not answer --version"
    if not api_key():
        return False, "set KIMI_API_KEY in .env (a Moonshot platform or plan key)"
    return True, ""


def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    if not api_key():
        return base.fail(config.t("provider_unconfigured", provider=LABEL,
                                  detail="KIMI_API_KEY"), resume_id, NAME)

    # No --model here: ANTHROPIC_MODEL already names the Kimi model, and
    # config.MODEL holds a *Claude* model name that this endpoint would reject.
    cmd = [command(), "-p",
           "--permission-mode", permission_mode,
           "--output-format", "json",
           "--add-dir", config.WORK_DIR]
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
    result = claude._parse(out, err, rc, resume_id)
    result["provider"] = NAME
    return result
