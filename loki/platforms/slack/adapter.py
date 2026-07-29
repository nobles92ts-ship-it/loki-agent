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
import urllib.request
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ...core import (autolisten, blocked, brain, commands, config, dedup,
                     guard, jobs, mrkdwn, orgs, ratelimit, scheduler, scope,
                     sessions, usage)
from ...core.config import log, require, t
from ...core.prompt import build_prompt
from . import checklists

# Optional private command extension — gitignored, workspace-specific heavy
# commands (see private_commands.example.py). Absent in a clean checkout.
try:
    from . import private_commands as _private
except Exception:
    _private = None

# ─────────────────────────── settings ───────────────────────────
BOT_TOKEN = require("SLACK_BOT_TOKEN")
APP_TOKEN = require("SLACK_APP_TOKEN")
ALLOWED_USER = require("ALLOWED_USER_ID")   # fail-closed: no allowlist, no boot

MAX_SLACK = 3800           # chars per Slack message before chunking
CHANNEL_CTX_DAYS = int(os.environ.get("LOKI_CHANNEL_CTX_DAYS", "7"))
CHANNEL_CTX_MSGS = int(os.environ.get("LOKI_CHANNEL_CTX_MSGS", "120"))

SELFTEST_FILE = config.STATE / "selftest.json"
IMG_DIR = config.STATE / "img"             # downloaded inbound image attachments

MAX_FILE_BYTES = 20 * 1024 * 1024          # cap per download / upload (20 MB)
MAX_UPLOADS = 4                            # max files auto-uploaded per reply
# absolute paths (Windows or POSIX) with an output-ish extension → candidates
# for outbound upload when the owner's reply references them.
_PATH_RE = re.compile(
    r'(?:[A-Za-z]:\\|/)[^\s"\'`<>|]+?'
    r'\.(?:html?|png|jpe?g|gif|svg|pdf|csv|md|txt|json|xlsx?|docx?)',
    re.IGNORECASE)

app = App(token=BOT_TOKEN)
checklists.register(app)            # clickable-checkbox handler (needs interactivity)
BOT_USER_ID: str | None = None      # resolved in run() via auth.test

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_SUMMARY_RE = re.compile(r"^!(?:summary|채널요약)\s+(\S+)$", re.IGNORECASE)
_MENTION_ID_RE = re.compile(r"<@([UW][A-Z0-9]+)>")
_USER_ID_RE = re.compile(r"[UW][A-Z0-9]{4,}")       # bare id typed by hand
_CHANNEL_ID_RE = re.compile(r"[CG][A-Z0-9]{4,}")
_names: dict[str, str] = {}         # user id -> display name cache


def _strip_mention(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _session_key(channel: str, thread_ts: str | None) -> str | None:
    """Conversation key for --resume continuity (see core.sessions.key_for).
    `D…` = DM, straight from Slack's channel ids."""
    return sessions.key_for(channel, thread_ts,
                            is_dm=str(channel or "").startswith("D"))


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


# ─────────────────────────── image attachments ───────────────────────────
def _download_images(image_files: list) -> list:
    """Download inbound image attachments (owner-only, pre-filtered) to
    state/img. Returns local absolute paths for Claude to read."""
    if not image_files:
        return []
    IMG_DIR.mkdir(exist_ok=True)
    paths = []
    for i, f in enumerate(image_files):
        url = f.get("url")
        if not url:
            continue
        name = re.sub(r"[^\w.\-]", "_", f.get("name") or f"image{i}")[-60:]
        dest = IMG_DIR / f"{int(time.time())}_{i}_{name}"
        try:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {BOT_TOKEN}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                log.warning("attachment too large, skipped: %s", name)
                continue
            dest.write_bytes(data)
            paths.append(str(dest))
        except Exception:
            log.exception("image download failed")
    return paths


def _upload_reply_files(job: dict, raw_text: str) -> None:
    """Owner-only: upload local output files the reply references by absolute
    path (whitelisted extensions, under WORK_DIR, size-capped, deduped)."""
    work = Path(config.WORK_DIR).resolve()
    seen: set = set()
    uploaded = 0
    for m in _PATH_RE.finditer(raw_text or ""):
        if uploaded >= MAX_UPLOADS:
            break
        try:
            rp = Path(m.group(0)).resolve()
        except Exception:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        try:
            rp.relative_to(work)             # must live under WORK_DIR
        except ValueError:
            continue
        try:
            if not rp.is_file() or rp.stat().st_size > MAX_FILE_BYTES:
                continue
            app.client.files_upload_v2(
                channel=job["channel"], thread_ts=job.get("thread"),
                file=str(rp), title=rp.name,
                initial_comment=t("file_uploaded", name=rp.name))
            uploaded += 1
        except Exception:
            log.exception("file upload failed")


# ─────────────────────────── job handling ───────────────────────────
def _handle(job: dict) -> None:
    thread = job.get("thread")     # None for scheduled fires → top-level DM post
    # If the job runs long, reassure the user it isn't dead (cancelled if it finishes first).
    notice = threading.Timer(60.0, _safe_post, args=(job, t("processing_notice")))
    notice.daemon = True
    notice.start()
    # Permission files may only change in the owner's own DM. `D…` = DM, straight
    # from Slack — a transport fact no message content can forge. Everything else
    # runs under the guard's deny rules and gets snapshot/reverted (core.guard).
    owner_dm = (job["user"] == ALLOWED_USER
                and str(job.get("channel") or "").startswith("D"))
    snap = None if owner_dm else guard.snapshot()
    try:
        if job.get("target_channel"):          # owner's !summary <channel_id>
            context, kind, scope_label = (_channel_context(job["target_channel"]),
                                          "kind_channel",
                                          t("scope_channel", d=CHANNEL_CTX_DAYS,
                                            n=CHANNEL_CTX_MSGS))
        elif job.get("in_thread"):
            context, kind, scope_label = (_thread_context(job["channel"], thread),
                                          "kind_thread", t("scope_thread"))
        elif job.get("is_mention"):
            context, kind, scope_label = (_channel_context(job["channel"]),
                                          "kind_channel",
                                          t("scope_channel", d=CHANNEL_CTX_DAYS,
                                            n=CHANNEL_CTX_MSGS))
        else:
            context, kind, scope_label = "", "kind_thread", ""
        prompt = build_prompt(context, job["text"], kind, scope_label)
        img_paths = _download_images(job.get("image_files") or [])
        if img_paths:
            prompt = t("image_note", n=len(img_paths),
                       paths="\n".join(f"- {p}" for p in img_paths)) + prompt
        skey = job.get("session_key")
        resume_id = sessions.get(skey)
        perm_mode = job["permission_mode"]

        # Guests: the loki.md allowlist — everything else is tool-level denied
        # via a per-request settings file, cwd pinned to the loki folder, and
        # the shared scope explained in-prompt. Owners are unaffected.
        #
        # An owner talking in a *channel* is protected too: _channel_context
        # feeds other people's messages into a bypassPermissions run, so that
        # path carries the same injection risk as a guest's.
        if job["user"] == ALLOWED_USER:
            guest_settings, run_cwd = None, None
            if not owner_dm:
                guest_settings = guard.settings_file()
        else:
            # org members get their org's manifest; unaffiliated → loki.md
            guest_settings, manifest = scope.write_scope_settings(job.get("org"))
            run_cwd = str(scope.loki_dir())
            prompt = t("guest_scope_note", manifest=manifest[:2500]) + prompt

        t0 = time.time()
        res = brain.run_claude(prompt, resume_id, perm_mode,
                               settings_file=guest_settings, cwd=run_cwd,
                               job=job)
        if job.get("cancelled"):           # killed via !cancel/!stop — stay quiet
            return

        # stale --resume → drop the dead id (so the next turn doesn't retry it)
        # and try once more with a fresh session
        if res["error"] and resume_id and res["reason"] == "error":
            sessions.reset(skey)
            res = brain.run_claude(prompt, None, perm_mode,
                                   settings_file=guest_settings, cwd=run_cwd,
                                   job=job)
            if job.get("cancelled"):
                return
            if not res["error"]:
                res["text"] = t("fresh_restart") + res["text"]

        sessions.remember(skey, res.get("session_id"))

        dur = time.time() - t0
        usage.record(job.get("kind", "?"), job.get("user", "?"),
                     res["reason"] == "ok", dur, res["reason"],
                     org=job.get("org"))
        log.info("job id=%s kind=%s user=%s ev=%s reason=%s dur=%ds chars=%d",
                 job.get("id"), job.get("kind"), job["user"], job["event_id"],
                 res["reason"], int(dur), len(res["text"]))

        if res["reason"] == "quota":
            _safe_post(job, t("quota"))
            time.sleep(30)
            return
        # Convert Claude's Markdown to Slack mrkdwn so it renders cleanly.
        _safe_post(job, job.get("reply_prefix", "") + mrkdwn.to_mrkdwn(res["text"]))
        # Owner only: attach any local output files the reply references.
        if job.get("user") == ALLOWED_USER:
            _upload_reply_files(job, res["text"])
    finally:
        notice.cancel()
        if snap is not None:
            reverted = guard.restore(snap)
            if reverted:
                alert_owner(guard.alert_text(reverted))
                _safe_post(job, t("tamper_blocked"))


def alert_owner(text: str) -> None:
    """DM the owner out-of-band — used for security alerts that must not depend
    on the owner happening to watch the channel where they fired."""
    try:
        dm = app.client.conversations_open(users=ALLOWED_USER)["channel"]["id"]
        app.client.chat_postMessage(channel=dm, text=text)
    except Exception:
        log.exception("owner alert DM failed")


def _on_job_error(job: dict, e: Exception) -> None:
    _safe_post(job, t("job_error", e=e))


def _safe_post(job: dict, text: str) -> None:
    kw = {"thread_ts": job["thread"]} if job.get("thread") else {}
    try:
        for chunk in _chunks(text):
            app.client.chat_postMessage(channel=job["channel"], text=chunk, **kw)
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


# ─────────────────────────── owner commands ───────────────────────────
# The `!` vocabulary itself lives in core.commands, shared with every other
# platform. Slack only supplies what Slack alone knows: how to resolve a name,
# and which ids the caller referenced (mention markup and id shapes differ).
def _cmd_ctx(text: str, event: dict, channel: str, is_owner: bool) -> dict:
    raw = event.get("text") or ""
    return {
        "channel": channel,
        "thread": event.get("thread_ts"),
        "session_key": _session_key(channel, event.get("thread_ts")),
        "is_dm": str(channel or "").startswith("D"),
        "is_owner": is_owner,
        "name_of": _user_name,
        # read from the RAW text: channel mentions are stripped before dispatch
        "user_ids": [i for i in _MENTION_ID_RE.findall(raw) if i != BOT_USER_ID],
        "is_user_id": lambda tk: bool(_USER_ID_RE.fullmatch(tk)),
        "is_channel_id": lambda tk: bool(_CHANNEL_ID_RE.fullmatch(tk)),
    }


def _fire_schedule(s: dict) -> None:
    """Scheduler callback — runs at owner permission, posts to the origin DM."""
    jobs.submit({
        "channel": s["channel"], "thread": None,
        "text": s["prompt"], "user": ALLOWED_USER,
        "event_id": f"sched-{s['id']}-{int(time.time())}",
        "in_thread": False, "is_mention": False,
        "permission_mode": config.PERMISSION_MODE, "kind": "scheduled",
        "reply_prefix": t("sched_fired", id=s["id"], spec=scheduler.spec_str(s)),
    })


# ─────────────────────────── Slack event handling ───────────────────────────
@app.event("message")
def on_message(body, event, logger):
    if event.get("channel_type") == "im":     # DMs → owner conversation
        _dispatch(body, event, is_mention=False)
        return
    # Channel/group message: engage only inside a registered auto-listen zone,
    # and never for @mentions (those arrive via app_mention → no double-handling).
    if event.get("bot_id"):
        return
    if BOT_USER_ID and f"<@{BOT_USER_ID}>" in (event.get("text") or ""):
        return
    channel = event.get("channel")
    if blocked.is_blocked(channel):
        return
    if autolisten.is_zone(channel, event.get("thread_ts")):
        _dispatch(body, event, is_mention=False, auto_listen=True)


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
        app.client.chat_postMessage(
            channel=dm, text=t("invited", name=name, cid=channel_id))
    except Exception:
        log.exception("join-notify DM failed")


def _dispatch(body, event, is_mention: bool, auto_listen: bool = False) -> None:
    # Stay FAST (filter + enqueue) so Bolt acks within Slack's 3s window.
    subtype = event.get("subtype")
    if (subtype and subtype != "file_share") or event.get("bot_id"):
        return
    user = event.get("user")
    is_owner = user == ALLOWED_USER
    channel = event.get("channel")
    if not is_owner:
        # Non-owner: only via @mention OR inside an auto-listen zone (never a
        # bare DM), always forced into read-only plan mode, and silently ignored
        # in blocked channels.
        if not (is_mention or auto_listen) or not user:
            return
        if blocked.is_blocked(channel):
            return
    permission_mode = config.PERMISSION_MODE if is_owner else "plan"

    event_id = (body.get("event_id")
                or event.get("client_msg_id")
                or event.get("ts"))
    if dedup.already_seen(event_id):
        return

    text = (_strip_mention(event.get("text") or "") if is_mention
            else (event.get("text") or "").strip())

    # Image attachments (owner only): downloaded in the worker, read in-prompt.
    image_files = []
    if is_owner:
        for f in (event.get("files") or []):
            if (f.get("mimetype") or "").startswith("image/"):
                url = f.get("url_private_download") or f.get("url_private")
                if url:
                    image_files.append({"url": url, "name": f.get("name") or "image"})
    if not text and not image_files:
        return
    if not text:
        text = t("image_default")          # image dropped with no caption

    thread = event.get("thread_ts") or event["ts"]

    # Organization tier — None means unaffiliated (global loki.md guest scope).
    org = None if is_owner else orgs.resolve(user, channel)

    # Private, workspace-specific commands (gitignored extension point). Runs for
    # owner + named trusted users; bypasses the guest queue/throttle by design.
    if _private is not None:
        try:
            if _private.try_handle({
                    "app": app, "event": event, "text": text, "user": user,
                    "channel": channel, "thread": thread, "is_owner": is_owner,
                    "org": org, "post": _post}):
                return
        except Exception:
            log.exception("private command handler failed")

    # Checklists — owner `!check` create + anyone's `완료 N` toggle inside a
    # checklist thread. Clickable checkbox toggles arrive via app.action instead.
    try:
        if checklists.try_handle({
                "app": app, "event": event, "text": text, "user": user,
                "channel": channel, "thread": thread,
                "thread_root": event.get("thread_ts"),
                "is_owner": is_owner, "post": _post}):
            return
    except Exception:
        log.exception("checklist handler failed")

    # !summary <channel_id> stays here — it enqueues a job against ANOTHER
    # channel, which is Slack plumbing rather than a plain-text reply.
    if is_owner:
        m = _SUMMARY_RE.match(text)
        if m:
            jobs.submit({"channel": channel, "thread": thread,
                         "text": t("summary_request"), "user": user,
                         "event_id": event_id, "in_thread": False,
                         "is_mention": False, "permission_mode": "plan",
                         "target_channel": m.group(1), "kind": "summary"})
            return

    reply = commands.handle(text, _cmd_ctx(text, event, channel, is_owner))
    if reply is not None:
        _post(channel, thread, reply)
        return

    # Guest throttle — protect the owner's subscription (owners never limited).
    # An org's Settings.rate overrides the global GUEST_RATE_PER_HOUR.
    if not is_owner:
        limit = orgs.rate(org)
        allowed, retry = ratelimit.check(user, limit=limit)
        if not allowed:
            _post(channel, thread,
                  t("rate_limited",
                    n=limit if limit is not None else config.GUEST_RATE_PER_HOUR,
                    m=retry))
            return

    qsize = jobs.JOBS.qsize()
    try:
        app.client.reactions_add(channel=channel, name="eyes", timestamp=event["ts"])
    except Exception:
        pass
    if qsize > 0:
        _post(channel, thread, t("queued", n=qsize))

    jobs.submit({"channel": channel, "thread": thread, "text": text,
                 "user": user, "event_id": event_id,
                 "in_thread": bool(event.get("thread_ts")),
                 "is_mention": is_mention, "permission_mode": permission_mode,
                 "kind": "owner" if is_owner else "guest",
                 "session_key": _session_key(channel, event.get("thread_ts")),
                 "org": org, "image_files": image_files})


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
    scope.ensure_manifest()        # guest allowlist template on first boot
    readonly_selftest()
    try:
        BOT_USER_ID = app.client.auth_test().get("user_id")
    except Exception:
        log.exception("auth.test failed")
        print("[loki] Slack auth failed — check SLACK_BOT_TOKEN.", file=sys.stderr)
        sys.exit(2)
    jobs.start(_handle, _on_job_error, kill=brain.tree_kill)
    scheduler.start(_fire_schedule)
    log.info("worker starting allowlist=%s work_dir=%s mode=%s lang=%s conc=%s",
             ALLOWED_USER, config.WORK_DIR, config.PERMISSION_MODE, config.LANG,
             config.JOB_CONCURRENCY)
    print(f"Loki (Slack) — allowlist={ALLOWED_USER}, work_dir={config.WORK_DIR}, "
          f"mode={config.PERMISSION_MODE}")
    print("Connecting to Slack (Socket Mode)…")
    SocketModeHandler(app, APP_TOKEN).start()
