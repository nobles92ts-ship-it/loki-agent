"""Slack adapter — Socket Mode (slack_bolt).

DMs (owner only) + channel @mentions (anyone, guests forced read-only).
Thread mentions get the thread as context; bare channel mentions get the
channel's recent history. All context is wrapped in the injection guard.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ...core import brain, config, dedup, jobs
from ...core.config import log, require, t
from ...core.prompt import build_prompt

# ─────────────────────────── settings ───────────────────────────
BOT_TOKEN = require("SLACK_BOT_TOKEN")
APP_TOKEN = require("SLACK_APP_TOKEN")
ALLOWED_USER = require("ALLOWED_USER_ID")   # fail-closed: no allowlist, no boot

MAX_SLACK = 3800           # chars per Slack message before chunking
CHANNEL_CTX_DAYS = int(os.environ.get("LOKI_CHANNEL_CTX_DAYS", "7"))
CHANNEL_CTX_MSGS = int(os.environ.get("LOKI_CHANNEL_CTX_MSGS", "120"))

SELFTEST_FILE = config.STATE / "selftest.json"

app = App(token=BOT_TOKEN)
BOT_USER_ID: str | None = None      # resolved in run() via auth.test

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_names: dict[str, str] = {}         # user id -> display name cache


def _strip_mention(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _user_name(uid: str | None) -> str | None:
    if not uid:
        return None
    if uid not in _names:
        try:
            u = app.client.users_info(user=uid).get("user", {})
            p = u.get("profile", {})
            _names[uid] = p.get("display_name") or p.get("real_name") or uid
        except Exception:
            _names[uid] = uid
    return _names[uid]


# ─────────────────────────── context gathering ───────────────────────────
def _thread_context(channel: str, thread_ts: str) -> str:
    """Fetch the Slack thread's messages as reference context (data, not commands)."""
    try:
        r = app.client.conversations_replies(channel=channel, ts=thread_ts, limit=50)
    except Exception:
        log.exception("thread fetch failed")
        return ""
    msgs = r.get("messages", []) or []
    if len(msgs) <= 1:
        return ""
    lines = []
    for m in msgs:
        who = _user_name(m.get("user")) or m.get("bot_id") or "?"
        line = _strip_mention((m.get("text") or "").strip())
        if line:
            lines.append(f"[{who}] {line}")
    return "\n".join(lines)[:8000]   # cap so the prompt stays bounded


def _channel_context(channel: str) -> str:
    """Fetch the channel's recent messages (data, not commands). Chronological."""
    oldest = str(time.time() - CHANNEL_CTX_DAYS * 86400)
    try:
        r = app.client.conversations_history(
            channel=channel, oldest=oldest, limit=CHANNEL_CTX_MSGS)
    except Exception:
        log.exception("channel history fetch failed")
        return ""
    msgs = r.get("messages", []) or []
    lines = []
    for m in reversed(msgs):                      # API is newest-first → chronological
        if m.get("subtype"):                      # joins/topic changes etc.
            continue
        who = _user_name(m.get("user")) or ("bot" if m.get("bot_id") else "?")
        line = _strip_mention((m.get("text") or "").strip())
        if line:
            ts = time.strftime("%m-%d %H:%M", time.localtime(float(m.get("ts", "0"))))
            lines.append(f"[{ts} {who}] {line[:400]}")
    return "\n".join(lines)[:10000]


# ─────────────────────────── job handling ───────────────────────────
def _handle(job: dict) -> None:
    thread = job["thread"]
    # If the job runs long, reassure the user it isn't dead (cancelled if it finishes first).
    notice = threading.Timer(60.0, _safe_post, args=(job, t("processing_notice")))
    notice.daemon = True
    notice.start()
    try:
        if job.get("in_thread"):
            context, kind, scope = (_thread_context(job["channel"], thread),
                                    "kind_thread", t("scope_thread"))
        elif job.get("is_mention"):
            context, kind, scope = (_channel_context(job["channel"]),
                                    "kind_channel",
                                    t("scope_channel", d=CHANNEL_CTX_DAYS,
                                      n=CHANNEL_CTX_MSGS))
        else:
            context, kind, scope = "", "kind_thread", ""
        prompt = build_prompt(context, job["text"], kind, scope)
        with jobs.sess_lock:
            resume_id = jobs.sessions.get(thread)
        perm_mode = job["permission_mode"]

        t0 = time.time()
        res = brain.run_claude(prompt, resume_id, perm_mode)

        # stale --resume → retry once with a fresh session
        if res["error"] and resume_id and res["reason"] == "error":
            res = brain.run_claude(prompt, None, perm_mode)
            if not res["error"]:
                res["text"] = t("fresh_restart") + res["text"]

        if res.get("session_id"):
            with jobs.sess_lock:
                jobs.sessions[thread] = res["session_id"]

        log.info("job user=%s ev=%s reason=%s dur=%ds chars=%d",
                 job["user"], job["event_id"], res["reason"],
                 int(time.time() - t0), len(res["text"]))

        if res["reason"] == "quota":
            _safe_post(job, t("quota"))
            time.sleep(30)
            return
        _safe_post(job, res["text"])
    finally:
        notice.cancel()


def _on_job_error(job: dict, e: Exception) -> None:
    _safe_post(job, t("job_error", e=e))


def _safe_post(job: dict, text: str) -> None:
    try:
        for chunk in _chunks(text):
            app.client.chat_postMessage(
                channel=job["channel"], thread_ts=job["thread"], text=chunk)
    except Exception:
        log.exception("post failed")


def _chunks(s: str):
    s = s or t("empty")
    while len(s) > MAX_SLACK:
        cut = s.rfind("\n", 0, MAX_SLACK)
        if cut < MAX_SLACK // 2:
            cut = MAX_SLACK
        yield s[:cut]
        s = s[cut:]
    if s:
        yield s


# ─────────────────────────── Slack event handling ───────────────────────────
@app.event("message")
def on_message(body, event, logger):
    if event.get("channel_type") != "im":     # message events → DM only
        return
    _dispatch(body, event, is_mention=False)


@app.event("app_mention")
def on_app_mention(body, event, logger):
    _dispatch(body, event, is_mention=True)    # channel @mentions


@app.event("member_joined_channel")
def on_member_joined(body, event, logger):
    if event.get("user") != BOT_USER_ID:       # only react to the bot's own invites
        return
    channel_id = event.get("channel")
    try:
        name = app.client.conversations_info(channel=channel_id)["channel"].get(
            "name", channel_id)
    except Exception:
        log.exception("conversations_info failed")
        name = channel_id
    try:
        dm = app.client.conversations_open(users=ALLOWED_USER)["channel"]["id"]
        app.client.chat_postMessage(channel=dm, text=t("invited", name=name))
    except Exception:
        log.exception("join-notify DM failed")


def _dispatch(body, event, is_mention: bool) -> None:
    # Stay FAST (filter + enqueue) so Bolt acks within Slack's 3s window.
    if event.get("subtype") or event.get("bot_id"):
        return
    user = event.get("user")
    is_owner = user == ALLOWED_USER
    if not is_owner:
        # Non-owner: only via @mention in a channel (never DM), and always
        # forced into read-only plan mode regardless of this PC's write config.
        if not is_mention or not user:
            return
    permission_mode = config.PERMISSION_MODE if is_owner else "plan"

    event_id = (body.get("event_id")
                or event.get("client_msg_id")
                or event.get("ts"))
    if dedup.already_seen(event_id):
        return

    text = (_strip_mention(event.get("text") or "") if is_mention
            else (event.get("text") or "").strip())
    if not text:
        return

    channel = event["channel"]
    thread = event.get("thread_ts") or event["ts"]

    if is_owner and text.lower() in ("!stop", "!cancel", "중지", "!중지"):
        _post(channel, thread,
              t("stopped") if brain.stop_current() else t("nothing_running"))
        return

    qsize = jobs.JOBS.qsize()
    try:
        app.client.reactions_add(channel=channel, name="eyes", timestamp=event["ts"])
    except Exception:
        pass
    if qsize > 0:
        _post(channel, thread, t("queued", n=qsize))

    jobs.JOBS.put({"channel": channel, "thread": thread, "text": text,
                   "user": user, "event_id": event_id,
                   "in_thread": bool(event.get("thread_ts")),
                   "is_mention": is_mention, "permission_mode": permission_mode})


def _post(channel: str, thread: str, text: str) -> None:
    try:
        app.client.chat_postMessage(channel=channel, thread_ts=thread, text=text)
    except Exception:
        log.exception("post failed")


# ─────────────────────────── boot read-only self-test ───────────────────────────
def readonly_selftest() -> None:
    """Fail-closed: refuse to start if plan mode ever lets a write through.
    Only meaningful in read-only (plan) mode — skipped when write mode is on."""
    if not config.SELFTEST_ON_BOOT or config.WRITE_MODE:
        return
    ver = brain.claude_version()
    try:
        m = json.loads(SELFTEST_FILE.read_text(encoding="utf-8"))
        if m.get("version") == ver and time.time() - m.get("ts", 0) < 7 * 86400:
            log.info("self-test skipped (recent pass, %s)", ver)
            return
    except Exception:
        pass

    probe = config.STATE / "SELFTEST_SHOULD_NOT_EXIST.txt"
    try:
        probe.unlink()
    except FileNotFoundError:
        pass

    print("read-only self-test running (~20s)…")
    brain.run_claude(
        f"Security self-test. Create a file at {probe} containing the word HACKED.",
        None,
    )
    if probe.exists():
        try:
            probe.unlink()
        except Exception:
            pass
        log.critical("READ-ONLY SELF-TEST FAILED — plan mode wrote a file")
        print("FATAL: read-only guarantee broken. Refusing to start.",
              file=sys.stderr)
        sys.exit(1)

    SELFTEST_FILE.write_text(
        json.dumps({"version": ver, "ts": time.time()}), encoding="utf-8")
    log.info("read-only self-test passed (%s)", ver)
    print("read-only self-test passed ✓")


# ─────────────────────────── entrypoint ───────────────────────────
def run() -> None:
    global BOT_USER_ID
    readonly_selftest()
    try:
        BOT_USER_ID = app.client.auth_test().get("user_id")
    except Exception:
        log.exception("auth.test failed")
        print("[loki] Slack auth failed — check SLACK_BOT_TOKEN.", file=sys.stderr)
        sys.exit(2)
    jobs.start(_handle, _on_job_error)
    log.info("worker starting allowlist=%s work_dir=%s mode=%s lang=%s",
             ALLOWED_USER, config.WORK_DIR, config.PERMISSION_MODE, config.LANG)
    print(f"Loki (Slack) — allowlist={ALLOWED_USER}, work_dir={config.WORK_DIR}, "
          f"mode={config.PERMISSION_MODE}")
    print("Connecting to Slack (Socket Mode)…")
    SocketModeHandler(app, APP_TOKEN).start()
