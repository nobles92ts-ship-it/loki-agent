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

from ...core import (autolisten, brain, config, dedup, jobs, learn, mrkdwn,
                     orgs, ratelimit, scheduler, scope, usage)
from ...core.config import log, require, t
from ...core.prompt import build_prompt

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
BLOCKED_FILE = config.STATE / "blocked_channels.json"
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
BOT_USER_ID: str | None = None      # resolved in run() via auth.test

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_BLOCK_RE = re.compile(r"^!(?:block|차단)\s+(\S+)$", re.IGNORECASE)
_UNBLOCK_RE = re.compile(r"^!(?:unblock|차단해제)\s+(\S+)$", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"^!(?:summary|채널요약)\s+(\S+)$", re.IGNORECASE)
_USAGE_RE = re.compile(r"^!(?:usage|사용량)(?:\s+(\d{1,3}))?$", re.IGNORECASE)
_JOBS_RE = re.compile(r"^!(?:jobs|작업목록)$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^!(?:cancel|취소)\s+(j\d+)$", re.IGNORECASE)
_SCHED_RE = re.compile(r"^!(?:schedule|예약)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_LEARN_RE = re.compile(r"^!(?:learn|학습)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_LISTEN_RE = re.compile(r"^!(?:listen|청취)$", re.IGNORECASE)
_UNLISTEN_RE = re.compile(r"^!(?:unlisten|청취해제)$", re.IGNORECASE)
_LISTENING_RE = re.compile(r"^!(?:listening|청취목록)$", re.IGNORECASE)
_ORG_RE = re.compile(r"^!(?:org|조직)\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
_ORG_SUB_RE = re.compile(
    r"^(create|list|info|add|remove|bind|unbind|allow|deny)\b\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
_MENTION_ID_RE = re.compile(r"<@([UW][A-Z0-9]+)>")
_names: dict[str, str] = {}         # user id -> display name cache


# ── channel block list (owner opt-out) ──────────────────────────────────────
# Every channel Loki joins is usable by default; the owner can shut one off
# from DM with "!block <channel_id>" and reopen it with "!unblock <id>".
_blocked_lock = threading.Lock()


def _load_blocked() -> set[str]:
    try:
        return set(json.loads(BLOCKED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


BLOCKED_CHANNELS = _load_blocked()


def _set_blocked(channel_id: str, blocked: bool) -> None:
    with _blocked_lock:
        (BLOCKED_CHANNELS.add if blocked else BLOCKED_CHANNELS.discard)(channel_id)
        try:
            BLOCKED_FILE.write_text(json.dumps(sorted(BLOCKED_CHANNELS)),
                                    encoding="utf-8")
        except Exception:
            log.exception("blocked_channels.json write failed")


def _is_blocked(channel_id: str) -> bool:
    with _blocked_lock:
        return channel_id in BLOCKED_CHANNELS


# Auto-listen zones live in core.autolisten (owner opt-in via !listen); the
# adapter only formats the listing and wires the commands.
def _fmt_listening() -> str:
    chans, threads = autolisten.snapshot()
    if not chans and not threads:
        return t("listening_none")
    lines = [t("listening_header", c=len(chans), t=len(threads))]
    lines += [f"• #{c}" for c in chans]
    lines += [f"• 🧵 {th}" for th in threads]
    return "\n".join(lines)


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
        with jobs.sess_lock:
            resume_id = jobs.sessions.get(thread) if thread else None
        perm_mode = job["permission_mode"]

        # Guests: the loki.md allowlist — everything else is tool-level denied
        # via a per-request settings file, cwd pinned to the loki folder, and
        # the shared scope explained in-prompt. Owners are unaffected.
        if job["user"] == ALLOWED_USER:
            guest_settings, run_cwd = None, None
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

        # stale --resume → retry once with a fresh session
        if res["error"] and resume_id and res["reason"] == "error":
            res = brain.run_claude(prompt, None, perm_mode,
                                   settings_file=guest_settings, cwd=run_cwd,
                                   job=job)
            if job.get("cancelled"):
                return
            if not res["error"]:
                res["text"] = t("fresh_restart") + res["text"]

        if thread and res.get("session_id"):
            with jobs.sess_lock:
                jobs.sessions[thread] = res["session_id"]

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


# ─────────────────────────── owner command helpers ───────────────────────────
def _fmt_dur(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _fmt_usage(days: int) -> str:
    data = usage.summarize(max(1, min(days, 90)))
    if not data["total"]:
        return t("usage_empty")
    lines = [t("usage_header", d=data["days"], n=data["total"], ok=data["ok"],
               fail=data["fail"], dur=_fmt_dur(data["dur_total"])),
             t("usage_today", n=data["today"]["total"],
               dur=_fmt_dur(data["today"]["dur"]))]
    if data["by_user"]:
        s = " · ".join(f"{_user_name(u) or u} {n}"
                       for u, n in data["by_user"][:8])
        lines.append(t("usage_by_user", s=s))
    if data["by_kind"]:
        lines.append(t("usage_by_kind",
                       s=" · ".join(f"{k} {n}" for k, n in data["by_kind"])))
    if data.get("by_org"):
        lines.append(t("usage_by_org",
                       s=" · ".join(f"{o} {n}" for o, n in data["by_org"])))
    return "\n".join(lines)


def _fmt_jobs() -> str:
    items = jobs.snapshot()
    if not items:
        return t("jobs_none")
    running = [j for j in items if j["status"] == "running"]
    queued = [j for j in items if j["status"] == "queued"]
    lines = [t("jobs_header", r=len(running), q=len(queued))]
    now = time.time()
    for j in running + queued:
        who = _user_name(j.get("user")) or j.get("user") or "?"
        age = _fmt_dur(now - j["started"]) if j.get("started") else "—"
        snip = (j.get("text") or "").replace("\n", " ")[:48]
        lines.append(f"• {j['id']} [{j['status']}] {j.get('kind', '?')}/{who}"
                     f" · {age} · “{snip}”")
    return "\n".join(lines)


def _schedule_cmd(channel: str, arg: str) -> str:
    a = arg.strip()
    if a.lower() in ("list", "목록"):
        items = scheduler.list_all()
        if not items:
            return t("sched_empty")
        lines = [t("sched_list_header")]
        for s in items:
            nxt = time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(s.get("next_fire", 0)))
            snip = (s.get("prompt") or "").replace("\n", " ")[:60]
            lines.append(f"• {s['id']} — {scheduler.spec_str(s)} → {nxt} · “{snip}”")
        return "\n".join(lines)
    m = re.match(r"^(?:remove|delete|삭제|제거)\s+(s\d+)$", a, re.IGNORECASE)
    if m:
        sid = m.group(1).lower()
        return (t("sched_removed", id=sid) if scheduler.remove(sid)
                else t("sched_not_found", id=sid))
    spec = scheduler.parse(a)
    if not spec:
        return t("sched_help")
    item = scheduler.add(spec, channel)
    nxt = time.strftime("%Y-%m-%d %H:%M", time.localtime(item["next_fire"]))
    return t("sched_added", id=item["id"], spec=scheduler.spec_str(item), next=nxt)


def _org_cmd(arg: str, raw_text: str, channel: str) -> str:
    """Owner `!org …` — thin command layer; the org's .md file stays the SSoT.
    Mentions are read from the RAW event text (channel mentions get stripped
    from the command text before dispatch)."""
    m = _ORG_SUB_RE.match((arg or "").strip())
    if not m:
        return t("org_help")
    sub, rest = m.group(1).lower(), (m.group(2) or "").strip()
    if sub == "list":
        ns = orgs.names()
        if not ns:
            return t("org_list_empty")
        lines = [t("org_list_header", n=len(ns))]
        for n in ns:
            o = orgs.get(n) or {}
            lines.append(t("org_list_line", name=n,
                           m=len(o.get("members", [])),
                           c=len(o.get("channels", [])),
                           k=len(o.get("commands", [])),
                           r=o.get("rate") if o.get("rate") is not None
                             else config.GUEST_RATE_PER_HOUR))
        return "\n".join(lines)
    toks = rest.split()
    name = toks[0] if toks else ""
    if sub == "create":
        r = orgs.create(name)
        return (t("org_created", name=name, path=orgs.org_file(name))
                if r == "created" else t("org_" + r, name=name))
    if not name or orgs.get(name) is None:
        return t("org_not_found", name=name or "?")
    if sub == "info":
        o = orgs.get(name)
        mem = ", ".join(o["members"][:8]) + (" …" if len(o["members"]) > 8 else "")
        return t("org_info", name=name, path=orgs.org_file(name),
                 n=len(o["members"]), members=mem or "-",
                 channels=", ".join(o["channels"]) or "-",
                 commands=", ".join(o["commands"]) or "-",
                 rate=o["rate"] if o["rate"] is not None
                      else config.GUEST_RATE_PER_HOUR)
    if sub in ("add", "remove"):
        ids = [i for i in _MENTION_ID_RE.findall(raw_text or "")
               if i != BOT_USER_ID]
        ids += [tk for tk in toks[1:] if re.fullmatch(r"[UW][A-Z0-9]{4,}", tk)]
        ids = list(dict.fromkeys(ids))
        if not ids:
            return t("org_add_none", name=name)
        if sub == "add":
            n = sum(1 for i in ids
                    if orgs.add_member(name, i, _user_name(i) or ""))
            return t("org_added", n=n, org=name) if n else t("org_nochange")
        n = sum(1 for i in ids if orgs.remove_member(name, i))
        return t("org_member_removed", org=name) if n else t("org_nochange")
    if sub in ("bind", "unbind"):
        cid = next((tk for tk in toks[1:]
                    if re.fullmatch(r"[CG][A-Z0-9]{4,}", tk)), None)
        if not cid:
            if channel and channel[0] in "CG":   # typed inside the target channel
                cid = channel
            else:
                return t("org_bind_need_id", name=name)
        if sub == "bind":
            return (t("org_bound", cid=cid, org=name) if orgs.bind(name, cid)
                    else t("org_nochange"))
        return (t("org_unbound", org=name) if orgs.unbind(name, cid)
                else t("org_nochange"))
    if sub in ("allow", "deny"):
        cmd = toks[1].lstrip("!").lower() if len(toks) > 1 else ""
        if not cmd:
            return t("org_help")
        if sub == "allow":
            return (t("org_cmd_allowed", org=name, cmd=cmd)
                    if orgs.allow_command(name, cmd) else t("org_nochange"))
        return (t("org_cmd_denied", org=name, cmd=cmd)
                if orgs.deny_command(name, cmd) else t("org_nochange"))
    return t("org_help")


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
    if _is_blocked(channel):
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
        if _is_blocked(channel):
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

    # owner commands: !block / !unblock a channel, !summary <channel_id>
    if is_owner:
        m = _BLOCK_RE.match(text)
        if m:
            _set_blocked(m.group(1), True)
            _post(channel, thread, t("blocked", cid=m.group(1)))
            return
        m = _UNBLOCK_RE.match(text)
        if m:
            _set_blocked(m.group(1), False)
            _post(channel, thread, t("unblocked", cid=m.group(1)))
            return
        m = _SUMMARY_RE.match(text)
        if m:
            jobs.submit({"channel": channel, "thread": thread,
                         "text": t("summary_request"), "user": user,
                         "event_id": event_id, "in_thread": False,
                         "is_mention": False, "permission_mode": "plan",
                         "target_channel": m.group(1), "kind": "summary"})
            return
        m = _USAGE_RE.match(text)
        if m:
            _post(channel, thread, _fmt_usage(int(m.group(1) or 7)))
            return
        if _JOBS_RE.match(text):
            _post(channel, thread, _fmt_jobs())
            return
        m = _CANCEL_RE.match(text)
        if m:
            jid = m.group(1).lower()
            key = {"killed": "cancel_killed", "dequeued": "cancel_dequeued",
                   "starting": "cancel_retry"}.get(jobs.cancel(jid),
                                                   "cancel_not_found")
            _post(channel, thread, t(key, id=jid))
            return
        m = _SCHED_RE.match(text)
        if m:
            _post(channel, thread, _schedule_cmd(channel, m.group(1)))
            return
        m = _LEARN_RE.match(text)
        if m:
            _post(channel, thread,
                  t("learn_saved", n=learn.capture(m.group(1))))
            return
        if _LISTEN_RE.match(text):
            _post(channel, thread, t(autolisten.add(channel, event.get("thread_ts"))))
            return
        if _UNLISTEN_RE.match(text):
            _post(channel, thread, t(autolisten.remove(channel, event.get("thread_ts"))))
            return
        if _LISTENING_RE.match(text):
            _post(channel, thread, _fmt_listening())
            return
        m = _ORG_RE.match(text)
        if m:
            _post(channel, thread,
                  _org_cmd(m.group(1), event.get("text") or "", channel))
            return

    if is_owner and text.lower() in ("!stop", "!cancel", "중지", "!중지"):
        n = jobs.cancel_all()
        _post(channel, thread,
              t("stopped_n", n=n) if n else t("nothing_running"))
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
