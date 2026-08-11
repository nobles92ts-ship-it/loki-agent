"""Antigravity CLI (``agy``) — the flat-rate Google path, after the old one closed.

Google stopped serving the Gemini CLI to individual accounts in June 2026, free
tier and Google AI Pro/Ultra alike, and named Antigravity as the successor. So
this is where a Google plan actually answers now; :mod:`loki.core.providers.
gemini` survives for the two paths that still work there (an API key, an
enterprise Code Assist licence) and nothing else.

Everything below was taken from the running binary rather than from the docs:

* the prompt goes on **stdin with no ``-p``**. ⚠️ ``-p -`` looks like the usual
  "read from stdin" idiom and is not — ``agy`` takes the ``-`` as the literal
  prompt and answers it, discarding stdin. The question is silently replaced by
  a greeting.
* ``--output-format json`` returns one object:
  ``{conversation_id, status, response, usage, duration_seconds, error?}``,
  with ``status`` ``SUCCESS`` or ``ERROR``. Failures print it on **stdout** too,
  unlike Gemini.
* ``--conversation <id>`` resumes. A dead id is **not** an error: the CLI warns
  on stderr and starts a fresh conversation, returning the new id. Loki stores
  whatever came back, so continuity self-heals and there is no retry to write.

Sign-in lives in the OS keyring, so there is no file to check and
:func:`available` only reports whether the binary runs. An unauthenticated run
would open a browser; ``--print-timeout`` is pinned to Loki's own timeout so
that bounds rather than hangs.

Plan mode leans on the CLI's own default, which soft-denies any tool that would
need confirmation. That is close to read-only but it is not a deny-rule file,
which is why this provider does not claim ``SANDBOX`` — and why the boot
self-test re-runs when you switch to it.
"""
from __future__ import annotations

import json
import os
import shutil

from .. import config
from ..config import log
from . import base

NAME = "antigravity"
LABEL = "Antigravity CLI"
SANDBOX = False         # no per-request deny rules → owner DM / console only
PLAN = "Google AI Pro/Ultra or the free tier — flat"

# Where the installer puts it. Worth a fallback: it adds the directory to the
# user PATH *registry*, which a process that was already running — the Loki
# worker, for one — will not see until it restarts.
_DEFAULT_DIRS = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"),
    os.path.join(os.path.expanduser("~"), ".local", "bin", "agy"),
)


def command() -> str:
    explicit = os.environ.get("ANTIGRAVITY_CMD", "").strip()
    if explicit:
        return explicit
    found = shutil.which("agy")
    if found:
        return found
    for path in _DEFAULT_DIRS:
        if path and os.path.exists(path):
            return path
    return "agy"        # let the spawn raise; the caller shows a friendly line


def version() -> str:
    from . import _probe_version
    return _probe_version(command(), ["--version"])


def available() -> tuple[bool, str]:
    if version() == "?":
        return False, ("Antigravity CLI not installed — "
                       "`irm https://antigravity.google/cli/install.ps1 | iex`")
    # Sign-in is in the OS keyring; there is nothing on disk to read, so this
    # deliberately does not guess. A signed-out run fails at the timeout.
    return True, ""


def _build(resume_id: str | None, permission_mode: str,
           cwd: str | None = None) -> list[str]:
    cmd = [command(), "--output-format", "json",
           "--print-timeout", f"{config.TIMEOUT_SEC}s"]
    if permission_mode != "plan":
        cmd.append("--dangerously-skip-permissions")
    if resume_id:
        cmd += ["--conversation", resume_id]
    # cwd is already the workspace root; name WORK_DIR only when they differ.
    here = os.path.abspath(cwd or config.WORK_DIR or ".")
    if config.WORK_DIR and os.path.abspath(config.WORK_DIR) != here:
        cmd += ["--add-dir", config.WORK_DIR]
    model = os.environ.get("ANTIGRAVITY_MODEL", "").strip()
    if model:
        cmd += ["--model", model]
    effort = os.environ.get("ANTIGRAVITY_EFFORT", "").strip()
    if effort:
        cmd += ["--effort", effort]
    return cmd


def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    if settings_file:
        return base.fail(config.t("provider_no_sandbox", provider=LABEL),
                         resume_id, NAME)

    cmd = _build(resume_id, permission_mode, cwd)
    try:
        out, err, rc, expired = base.spawn(cmd, prompt, cwd,
                                           base.clean_env(), job)
    except FileNotFoundError:
        return base.not_found(command(), NAME)
    if expired:
        return base.timed_out(resume_id, NAME)
    return _parse(out, err, rc, resume_id)


def _parse(out: str, err: str, rc: int, resume_id: str | None) -> dict:
    try:
        data = json.loads((out or "").strip())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # "" on an error envelope — keep the id we came in with rather than
    # overwriting a live conversation with nothing.
    sid = (data.get("conversation_id") or "").strip() or resume_id
    if resume_id and sid != resume_id:
        log.info("antigravity: conversation %s was replaced by %s",
                 resume_id, sid)

    failure = (data.get("error") or "").strip()
    status = (data.get("status") or "").strip().upper()
    if not data and rc != 0:
        failure = base.trim(f"{err or ''}\n{out or ''}") or \
            config.t("exit_code", rc=rc)
    if failure or (status and status != "SUCCESS"):
        detail = base.trim(failure or status)
        return base.fail(detail, sid, NAME,
                         "quota" if base.quota_hit(detail) else "error")
    return base.ok((data.get("response") or "").strip(), sid, NAME)
