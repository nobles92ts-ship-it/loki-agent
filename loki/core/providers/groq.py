"""Groq — fast answers, and the one provider here that is not an agent.

The others are CLIs that can read your disk, run commands and hold a session on
their own. Groq is a hosted inference endpoint: no tools, no filesystem, no
server-side conversation. Asking it to "check the log in WORK_DIR" gets a
confident guess, because it cannot look. That is worth stating plainly rather
than discovering — :func:`caveat` is shown wherever this provider is selected.

It earns its place on cost and latency. Groq's free tier is rate-limited rather
than metered, which makes it flat in the sense that matters here: a bad day
costs nothing but a refusal. Keep the token ceiling in mind — a free key is
capped per minute, and Loki's own prompts are small, so the history window
below is deliberately short.

Continuity is local. With no session on the far end, a conversation is a
transcript this module keeps in ``state/groq/``; ``!new`` drops it like any
other session because the id flows through :mod:`loki.core.sessions` unchanged.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

from .. import config
from ..config import log
from . import base

NAME = "groq"
LABEL = "Groq"
SANDBOX = False
PLAN = "free tier — rate-limited rather than metered"

DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

_STORE = config.STATE / "groq"

# A free key is capped per minute, and the cap counts the history we resend.
# Trimming to the recent turns keeps a long conversation from walking into a
# 413 it can never recover from without `!new`.
MAX_TURNS = 12
MAX_CHARS = 24_000


def caveat() -> str:
    return config.t("provider_no_tools", provider=LABEL)


def api_key() -> str:
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def model() -> str:
    return (os.environ.get("GROQ_MODEL") or "").strip() or DEFAULT_MODEL


def version() -> str:
    return model() if api_key() else "?"


def available() -> tuple[bool, str]:
    if not api_key():
        return False, "set GROQ_API_KEY in .env (console.groq.com — free tier)"
    return True, ""


# ─────────────────────────── local transcript ───────────────────────────
def _path(session_id: str):
    return _STORE / f"{session_id}.json"


def _history(session_id: str | None) -> list[dict]:
    if not session_id:
        return []
    try:
        data = json.loads(_path(session_id).read_text(encoding="utf-8"))
    except Exception:
        return []
    return [m for m in data if isinstance(m, dict) and m.get("content")]


def _remember(session_id: str, messages: list[dict]) -> None:
    """Persist the trimmed transcript. Trimming happens here, not at read time,
    so the file on disk is always the thing that will actually be sent."""
    trimmed = messages[-MAX_TURNS:]
    while trimmed and sum(len(m.get("content", "")) for m in trimmed) > MAX_CHARS:
        trimmed.pop(0)
    try:
        _STORE.mkdir(parents=True, exist_ok=True)
        _path(session_id).write_text(json.dumps(trimmed, ensure_ascii=False),
                                     encoding="utf-8")
    except Exception:
        log.exception("groq transcript write failed")


# ─────────────────────────── the call ───────────────────────────
def run(prompt: str, resume_id: str | None, permission_mode: str,
        settings_file: str | None = None, cwd: str | None = None,
        job: dict | None = None) -> dict:
    if settings_file:
        return base.fail(config.t("provider_no_sandbox", provider=LABEL),
                         resume_id, NAME)
    key = api_key()
    if not key:
        return base.fail(config.t("provider_unconfigured", provider=LABEL,
                                  detail="GROQ_API_KEY"), resume_id, NAME)

    sid = resume_id or str(uuid.uuid4())
    messages = _history(resume_id) + [{"role": "user", "content": prompt}]
    payload = json.dumps({"model": model(), "messages": messages},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        os.environ.get("GROQ_URL", "").strip() or DEFAULT_URL,
        data=payload, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = _detail(e)
        status = e.code
        return base.fail(detail or f"HTTP {status}", sid, NAME,
                         "quota" if base.quota_hit(detail, status) else "error")
    except Exception as e:
        return base.fail(str(e), sid, NAME)

    text = ""
    try:
        text = (body["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return base.fail(json.dumps(body)[:400], sid, NAME)

    _remember(sid, messages + [{"role": "assistant", "content": text}])
    return base.ok(text, sid, NAME)


def _detail(e: urllib.error.HTTPError) -> str:
    """Groq's own error message — it names the valid models on a bad one."""
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        return json.dumps(body)[:400]
    except Exception:
        return f"HTTP {e.code} {e.reason}"


def forget(session_id: str | None) -> None:
    """Drop a local transcript — called when `!new` resets the conversation."""
    if not session_id:
        return
    try:
        _path(session_id).unlink()
    except OSError:
        pass
