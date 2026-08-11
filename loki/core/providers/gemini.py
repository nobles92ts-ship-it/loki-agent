"""Gemini CLI — the flat-rate alternative, on a Google account rather than a key.

Two auth paths reach the same binary and they are not the same deal. Signing in
with a Google account (``GOOGLE_GENAI_USE_GCA``) spends a plan — the free tier's
daily allowance, or a Google AI subscription — while ``GEMINI_API_KEY`` bills
per token. Loki defaults to the account path and strips inherited key variables
so a stray ``GEMINI_API_KEY`` in the parent shell cannot quietly move a Slack
workspace onto metered billing. ``GEMINI_AUTH=apikey`` opts in deliberately.

Three flags here are not decoration:

* the prompt goes on **stdin with no ``-p``**. The CLI treats a piped non-TTY
  as headless already, and ``-p`` is *appended* to stdin rather than replacing
  it, so using both would send the text twice.
* ``--skip-trust`` because an untrusted folder silently downgrades
  ``--approval-mode`` back to ``default`` — which means "ask a human", which in
  a headless spawn means hang until the timeout.
* ``--session-id`` on a first turn, so Loki names the conversation instead of
  discovering its name afterwards.

Sessions are stored per project directory, so continuity holds only while a
conversation keeps landing in the same ``cwd``. Loki always spawns in
``WORK_DIR``, which is what makes that true here.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid

from .. import config
from ..config import ANSI, log
from . import base

NAME = "gemini"
LABEL = "Gemini CLI"
SANDBOX = False         # no per-request deny rules → owner DM / console only
PLAN = "Google account (free tier or Google AI subscription)"

_STRIP_PREFIXES = ("GEMINI_", "GOOGLE_GENAI_")
_STRIP_EXACT = {"GOOGLE_API_KEY"}

# What the CLI says when a --resume target is gone. Loki retries these without
# the resume rather than surfacing them: a dropped session should cost the user
# a fresh answer, not an error.
_DEAD_SESSION = ("invalid session identifier", "no previous sessions",
                 "no_sessions_found", "invalid_session_identifier")

# Google stopped serving the Gemini CLI for individual accounts in June 2026 —
# free tier *and* paid Google AI Pro/Ultra alike. Only enterprise Code Assist
# licences and API keys still answer. The CLI reports it as a crash with a
# stack trace, which reads like a bug in Loki; it is a closed door, and the way
# through it is a setting.
_INELIGIBLE = ("ineligibletiererror", "no longer supported for gemini code "
               "assist", "unsupported_client", "free_tier_user_not_eligible")

_APPROVAL = {"plan": "plan"}        # everything else is full write/execute


def command() -> str:
    explicit = os.environ.get("GEMINI_CMD", "").strip()
    return explicit or shutil.which("gemini") or "gemini"


def env_extra() -> dict[str, str]:
    """Pick the auth path deliberately, after the inherited one was stripped."""
    mode = (os.environ.get("GEMINI_AUTH") or "oauth").strip().lower()
    if mode == "apikey":
        return {"GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", "").strip()}
    if mode == "vertex":
        return {"GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY", "").strip()}
    return {"GOOGLE_GENAI_USE_GCA": "true"}


def version() -> str:
    from . import _probe_version
    return _probe_version(command(), ["--version"])


def available() -> tuple[bool, str]:
    if version() == "?":
        return False, ("gemini CLI not installed — "
                       "`npm install -g @google/gemini-cli`")
    mode = (os.environ.get("GEMINI_AUTH") or "oauth").strip().lower()
    if mode == "oauth" and not _logged_in():
        return False, "gemini is installed but not signed in — run `gemini` once"
    return True, ""


def _logged_in() -> bool:
    """Is there a cached Google sign-in for the CLI to use without asking?

    Two files count. ``oauth_creds.json`` is the sign-in itself; a
    ``settings.json`` carrying ``selectedAuthType`` means the user has chosen a
    method through the CLI's own flow, which covers setups this cannot see —
    a workspace account, a Cloud project. Guessing "not signed in" for someone
    who is would refuse every request, so the doubt goes the other way.
    """
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if not home:
        return False
    d = os.path.join(home, ".gemini")
    if os.path.exists(os.path.join(d, "oauth_creds.json")):
        return True
    try:
        with open(os.path.join(d, "settings.json"), encoding="utf-8") as fh:
            return "selectedAuthType" in fh.read()
    except OSError:
        return False


def _build(resume_id: str | None, permission_mode: str, new_id: str,
           cwd: str | None = None) -> list[str]:
    cmd = [command(), "--output-format", "json", "--skip-trust",
           "--approval-mode", _APPROVAL.get(permission_mode, "yolo")]
    # The spawn's own directory is already the workspace root, and it is
    # WORK_DIR in every ordinary case. Only name WORK_DIR as an *additional*
    # directory when the run is happening somewhere else.
    here = os.path.abspath(cwd or config.WORK_DIR or ".")
    if config.WORK_DIR and os.path.abspath(config.WORK_DIR) != here:
        cmd += ["--include-directories", config.WORK_DIR]
    model = os.environ.get("GEMINI_MODEL", "").strip()
    if model:
        cmd += ["--model", model]
    cmd += ["--resume", resume_id] if resume_id else ["--session-id", new_id]
    return cmd


def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    # settings_file is Claude Code's deny-rule format and has no equivalent
    # here. The registry keeps sandboxed requests away from this provider; this
    # is the belt to that suspenders, so a direct caller cannot bypass it.
    if settings_file:
        return base.fail(config.t("provider_no_sandbox", provider=LABEL),
                         resume_id, NAME)

    # Refuse rather than spawn when there is no cached sign-in. Unauthenticated,
    # the CLI starts its *interactive* OAuth flow — which reads stdin, and stdin
    # is where the prompt is. Observed: the question is swallowed by the login
    # prompt, the login is then cancelled by the same EOF, and the CLI answers
    # "No input provided via stdin" after burning a spawn. On a worker with no
    # console it can sit there until the timeout instead.
    ready, why = available()
    if not ready:
        return base.fail(config.t("provider_not_ready_run", provider=LABEL,
                                  detail=why), resume_id, NAME)

    for attempt in (resume_id, None) if resume_id else (None,):
        new_id = str(uuid.uuid4())
        cmd = _build(attempt, permission_mode, new_id, cwd)
        try:
            out, err, rc, expired = base.spawn(cmd, prompt, cwd, env(), job)
        except FileNotFoundError:
            return base.not_found(command(), NAME)
        if expired:
            return base.timed_out(attempt, NAME)
        result = _parse(out, err, rc, attempt or new_id)
        if result["error"] and attempt and _stale(out, err) \
                and not (job or {}).get("cancelled"):
            # `!cancel` kills the child, and a killed child must not be read as
            # "that session is gone, try again" — that would spawn the work the
            # user just stopped.
            log.info("gemini: session %s is gone — retrying fresh", attempt)
            continue                       # the loop's second pass has no resume
        return result
    return base.fail("", resume_id, NAME)   # unreachable; keeps the type honest


def env() -> dict[str, str]:
    return base.clean_env(_STRIP_PREFIXES, _STRIP_EXACT, env_extra())


def _stale(out: str, err: str) -> bool:
    blob = f"{out} {err}".lower()
    return any(mark in blob for mark in _DEAD_SESSION)


def _envelope(out: str, err: str) -> dict:
    """The CLI's JSON, wherever it landed and whatever is printed around it.

    A successful run prints it on stdout and a failed one on stderr, so reading
    only stdout turns every real error into "(empty response)". Either stream
    can also carry plain prose beside it — "YOLO mode is enabled." and
    "Approval mode overridden…" both arrive on stderr above the envelope — so
    this decodes from each ``{`` in turn and takes the first that yields an
    object, ignoring whatever trails it.
    """
    decoder = json.JSONDecoder()
    for blob in (out, err):
        blob = (blob or "").strip()
        for i, ch in enumerate(blob):
            if ch != "{":
                continue
            try:
                data, _end = decoder.raw_decode(blob, i)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {}


def _ineligible(blob: str) -> bool:
    low = (blob or "").lower()
    return any(mark in low for mark in _INELIGIBLE)


def _parse(out: str, err: str, rc: int, sid_hint: str | None) -> dict:
    data = _envelope(out, err)
    sid = data.get("session_id") or sid_hint
    err_obj = data.get("error")
    if err_obj:
        msg = (err_obj.get("message") if isinstance(err_obj, dict)
               else str(err_obj)) or ""
        code = (err_obj.get("code") if isinstance(err_obj, dict) else 0) or 0
        if _ineligible(msg):
            return base.fail(config.t("gemini_ineligible"), sid, NAME)
        reason = "quota" if base.quota_hit(msg, int(code or 0)) else "error"
        return base.fail(msg, sid, NAME, reason)
    text = (data.get("response") or "").strip()
    if not text and rc != 0:
        raw = f"{err or ''}\n{out or ''}"
        if _ineligible(raw):
            return base.fail(config.t("gemini_ineligible"), sid, NAME)
        # No envelope to read, so the raw stream is all there is — trimmed,
        # because a node stack trace is mostly frames.
        return base.fail(base.trim(raw) or config.t("exit_code", rc=rc), sid,
                         NAME, "quota" if base.quota_hit(raw) else "error")
    return base.ok(text, sid, NAME)
