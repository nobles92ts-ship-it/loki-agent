"""The brain — runs `claude -p` (Claude Code headless) and parses its result.

Auth comes from the machine's `~/.claude` login (your subscription), never from
env inherited from a parent Claude Code session (stripped below).
"""
from __future__ import annotations

import json
import os
import subprocess

from . import config
from .config import ANSI, log, t

_current: dict = {"proc": None}   # running subprocess handle, for !stop / tree-kill


def tree_kill(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, creationflags=config.NO_WINDOW,
        )
    except Exception:
        log.exception("taskkill failed")


def stop_current() -> bool:
    """Kill the currently running claude job. Returns True if one was running."""
    proc = _current.get("proc")
    if not proc:
        return False
    tree_kill(proc.pid)
    return True


def run_claude(prompt: str, resume_id: str | None,
               permission_mode: str | None = None,
               settings_file: str | None = None,
               cwd: str | None = None) -> dict:
    """Run claude headless. Returns {text, session_id, error(bool), reason}.

    settings_file: per-request settings JSON (e.g. the guest allowlist's deny
    rules — too long for the command line, cmd.exe caps it at 8191 chars).
    cwd: working directory override (guests are pinned to the loki folder)."""
    mode = permission_mode or config.PERMISSION_MODE
    cmd = [
        config.CLAUDE_CMD, "-p",
        "--permission-mode", mode,
        "--output-format", "json",
        "--add-dir", config.WORK_DIR,
    ]
    if config.MODEL:
        cmd += ["--model", config.MODEL]
    if resume_id:
        cmd += ["--resume", resume_id]
    if settings_file:
        cmd += ["--settings", settings_file]
    # prompt goes via stdin (robust for the .cmd wrapper + spaces/unicode), not as an arg

    # Auth via the on-disk login (~/.claude), NOT auth inherited from a parent
    # Claude Code session — strip its session/auth env so claude -p logs in fresh.
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE_CODE") or k == "CLAUDECODE"
                   or k.startswith("ANTHROPIC_"))}
    env["PYTHONUTF8"] = "1"
    flags = ((subprocess.CREATE_NEW_PROCESS_GROUP | config.NO_WINDOW)
             if os.name == "nt" else 0)
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd or config.WORK_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", env=env,
            creationflags=flags,
        )
    except FileNotFoundError:
        return {"text": t("claude_not_found", path=config.CLAUDE_CMD),
                "session_id": resume_id, "error": True, "reason": "error"}

    _current["proc"] = proc
    try:
        out, err = proc.communicate(input=prompt, timeout=config.TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        tree_kill(proc.pid)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return {"text": t("timeout"),
                "session_id": resume_id, "error": True, "reason": "timeout"}
    finally:
        _current["proc"] = None

    return _parse(out, err, proc.returncode, resume_id)


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
    blob = (text + " " + (err or "")).lower()
    if api_status in (429, 529) or any(s in blob for s in
           ("rate limit", "usage limit", "quota", "limit reached",
            "session limit", "hit your", "resource_exhausted", "429")):
        return {"text": text, "session_id": sid, "error": True, "reason": "quota"}
    return {"text": text or t("empty"), "session_id": sid,
            "error": is_err, "reason": "error" if is_err else "ok"}


def claude_version() -> str:
    try:
        r = subprocess.run([config.CLAUDE_CMD, "--version"],
                           capture_output=True, text=True, timeout=30,
                           creationflags=config.NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return "?"
