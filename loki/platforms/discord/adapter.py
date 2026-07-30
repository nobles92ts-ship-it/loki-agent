"""Discord adapter — gateway connection (discord.py).

Same shape as the Slack adapter: DMs are the owner's private line, guild
channels answer @mentions from anyone (guests forced read-only), threads carry
their own conversation. All gathered context is wrapped in the injection guard.

The one structural difference is threading. discord.py owns an asyncio loop
while ``loki.core.jobs`` runs Claude on worker threads, so every call back into
Discord from a worker goes through :func:`_call` — schedule onto the loop, wait
for the result. Reads happen on the loop before a job is queued; writes come
back through the bridge.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import discord

from ...core import (alias, autolisten, blocked, brain, budget, commands,
                     config, dedup, files, guard, health, jobs, orgs,
                     ratelimit, scheduler, scope, selftest, sessions, usage)
from ...core.config import log, require, t
from ...core.prompt import build_prompt

# ─────────────────────────── settings ───────────────────────────
BOT_TOKEN = require("DISCORD_BOT_TOKEN")
OWNER_ID = require("DISCORD_OWNER_ID")     # fail-closed: no allowlist, no boot

MAX_DISCORD = 1900          # chars per message (hard limit 2000, leave headroom)
CHANNEL_CTX_DAYS = int(os.environ.get("LOKI_CHANNEL_CTX_DAYS", "7"))
CHANNEL_CTX_MSGS = int(os.environ.get("LOKI_CHANNEL_CTX_MSGS", "120"))

_MENTION_RE = re.compile(r"<@[!&]?\d+>")            # users and roles
_USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")
_SNOWFLAKE_RE = re.compile(r"\d{15,20}")
_SEND_RE = re.compile(r"^!(?:send|전송|파일)(?:\s+(.+))?$", re.IGNORECASE)

intents = discord.Intents.default()
intents.message_content = True      # PRIVILEGED — enable it in the dev portal
bot = discord.Client(intents=intents)

_loop: asyncio.AbstractEventLoop | None = None      # captured in on_ready


# ─────────────────────────── async bridge ───────────────────────────
def _call(coro, timeout: float = 30.0):
    """Run a Discord coroutine from a worker thread and wait for the result.

    Returns None if the loop isn't up yet or the call failed — callers treat
    Discord I/O as best-effort, exactly like the Slack adapter does.
    """
    if _loop is None or _loop.is_closed():
        coro.close()
        return None
    try:
        return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)
    except Exception:
        log.exception("discord call failed")
        return None


# ─────────────────────────── helpers ───────────────────────────
def _strip_mention(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _user_name(uid: str | None) -> str | None:
    """Display name from the gateway cache — no API round trip."""
    if not uid:
        return None
    try:
        u = bot.get_user(int(uid))
    except (TypeError, ValueError):
        return uid
    return (u.display_name if u else None) or uid


def _resolve_channel(channel_id: str):
    """Channel object from an id — cache first, then the API (DM channels and
    threads the bot hasn't touched this run aren't always cached)."""
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return None
    return bot.get_channel(cid) or _call(bot.fetch_channel(cid))


def _is_dm(channel) -> bool:
    return isinstance(channel, discord.DMChannel)


def _is_thread(channel) -> bool:
    return isinstance(channel, discord.Thread)


def _session_key(channel) -> str | None:
    """Conversation key for --resume continuity (see core.sessions.key_for).
    A Discord thread is its own channel, so it is both the channel and the
    thread here — the key stays opaque and unique either way."""
    cid = str(channel.id)
    return sessions.key_for(cid, cid if _is_thread(channel) else None,
                            is_dm=_is_dm(channel))


# ─────────────────────────── context gathering ───────────────────────────
async def _thread_context(channel) -> str:
    """This thread's messages as reference context (data, not commands)."""
    lines, seen = [], 0
    try:
        async for m in channel.history(limit=50, oldest_first=True):
            seen += 1
            line = _strip_mention((m.content or "").strip())
            if line:
                lines.append(f"[{m.author.display_name}] {line}")
    except Exception:
        log.exception("thread history fetch failed")
        return ""
    if seen <= 1:                  # nothing but the message we're answering
        return ""
    return "\n".join(lines)[:8000]


async def _channel_context(channel) -> str:
    """The channel's recent messages (data, not commands). Chronological."""
    after = datetime.now(timezone.utc) - timedelta(days=CHANNEL_CTX_DAYS)
    lines = []
    try:
        async for m in channel.history(limit=CHANNEL_CTX_MSGS, after=after,
                                       oldest_first=True):
            line = _strip_mention((m.content or "").strip())
            if line:
                ts = m.created_at.astimezone().strftime("%m-%d %H:%M")
                lines.append(f"[{ts} {m.author.display_name}] {line[:400]}")
    except Exception:
        log.exception("channel history fetch failed")
        return ""
    return "\n".join(lines)[:10000]


async def _download_attachments(message) -> tuple[list[str], list[str]]:
    """Save the owner's attachments for Claude to read locally.

    Same deny-by-default allowlist as every other platform (core.files):
    documents, data and source text in, executables and archives refused.
    Returns (image paths, document paths) plus posts nothing — the caller
    reports what was skipped.
    """
    imgs: list[str] = []
    docs: list[str] = []
    rejected: list[str] = []
    img_dir, doc_dir = files.inbox_dir("img"), files.inbox_dir("files")
    files.prune_old(img_dir)
    files.prune_old(doc_dir)
    for i, att in enumerate(message.attachments[:files.MAX_INBOUND_FILES]):
        name = att.filename or "file"
        kind = files.classify_inbound(name, att.content_type or "")
        if not kind:
            rejected.append(name)
            continue
        if att.size > files.MAX_FILE_BYTES:
            log.warning("attachment over the size cap, skipped: %s", name)
            continue
        dest = ((img_dir if kind == "image" else doc_dir)
                / files.safe_filename(name, i))
        try:
            dest.write_bytes(await att.read())
            (imgs if kind == "image" else docs).append(str(dest))
        except Exception:
            log.exception("attachment download failed")
    if rejected:
        try:
            await message.channel.send(
                t("file_rejected", names=", ".join(rejected[:4])))
        except Exception:
            log.exception("rejection notice failed")
    return imgs, docs


# ─────────────────────────── job handling ───────────────────────────
def _handle(job: dict) -> None:
    """Runs on a worker thread — Discord I/O goes through _call."""
    notice = threading.Timer(60.0, _safe_post, args=(job, t("processing_notice")))
    notice.daemon = True
    notice.start()
    # Permission files may only change in the owner's own DM — a transport fact
    # (see core.guard). Everything else is snapshot and reverted if it tries.
    owner_dm = job["user"] == OWNER_ID and job.get("is_dm")
    snap = None if owner_dm else guard.snapshot()
    try:
        channel = _resolve_channel(job["channel"])
        if job.get("in_thread") and channel is not None:
            context = _call(_thread_context(channel)) or ""
            kind, scope_label = "kind_thread", t("scope_thread")
        elif job.get("is_mention") and channel is not None:
            context = _call(_channel_context(channel)) or ""
            kind, scope_label = "kind_channel", t("scope_channel",
                                                  d=CHANNEL_CTX_DAYS,
                                                  n=CHANNEL_CTX_MSGS)
        else:
            context, kind, scope_label = "", "kind_thread", ""
        prompt = build_prompt(context, job["text"], kind, scope_label)
        if job.get("doc_paths"):
            prompt = t("file_note", n=len(job["doc_paths"]),
                       paths="\n".join(f"- {p}" for p in job["doc_paths"])) + prompt
        if job.get("image_paths"):
            prompt = t("image_note", n=len(job["image_paths"]),
                       paths="\n".join(f"- {p}" for p in job["image_paths"])) + prompt

        skey = job.get("session_key")
        resume_id = sessions.get(skey)
        perm_mode = job["permission_mode"]

        if job["user"] == OWNER_ID:
            guest_settings, run_cwd = None, None
            if not owner_dm:                 # channel context = injection risk
                guest_settings = guard.settings_file()
        else:
            guest_settings, manifest = scope.write_scope_settings(job.get("org"))
            run_cwd = str(scope.loki_dir())
            prompt = t("guest_scope_note", manifest=manifest[:2500]) + prompt

        t0 = time.time()
        res = brain.run_claude(prompt, resume_id, perm_mode,
                               settings_file=guest_settings, cwd=run_cwd,
                               job=job)
        if job.get("cancelled"):
            return

        # stale --resume → drop the dead id and retry once with a fresh session
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
        # Discord renders standard Markdown, so Claude's output goes as-is.
        _safe_post(job, job.get("reply_prefix", "") + res["text"])
        if job.get("user") == OWNER_ID:
            _upload_reply_files(job, res["text"])
    finally:
        notice.cancel()
        if snap is not None:
            reverted = guard.restore(snap)
            if reverted:
                alert_owner(guard.alert_text(reverted))
                _safe_post(job, t("tamper_blocked"))


def _upload_reply_files(job: dict, raw_text: str) -> None:
    """Owner-only: upload local output files the reply references by absolute
    path (known output extensions, under WORK_DIR, size-capped, deduped)."""
    channel = _resolve_channel(job["channel"])
    if channel is None:
        return
    for rp in files.find_reply_files(raw_text):
        try:
            _call(channel.send(content=t("file_uploaded", name=rp.name),
                               file=discord.File(str(rp))), timeout=120)
        except Exception:
            log.exception("file upload failed")


def _send_files(channel, spec: str) -> str:
    """Owner `!send <path>` — upload WORK_DIR files into this channel.
    Returns a message to post, or '' when every file went out cleanly."""
    paths, err, detail = files.resolve_outbound(spec)
    if err:
        return t(err, p=detail, name=detail, work=config.WORK_DIR,
                 max=files.MAX_FILE_MB)
    failed = 0
    for rp in paths:
        try:
            _call(channel.send(content=t("file_uploaded", name=rp.name),
                               file=discord.File(str(rp))), timeout=120)
        except Exception:
            log.exception("send upload failed")
            failed += 1
    return t("send_fail", n=failed) if failed else ""


async def _alert_owner(text: str) -> None:
    """DM the owner out of band — security alerts must not depend on them
    watching the channel where the run fired."""
    try:
        user = await bot.fetch_user(int(OWNER_ID))
        await user.send(text)
    except Exception:
        log.exception("owner alert DM failed")


def alert_owner(text: str) -> None:
    """Thread-side wrapper. Never call this from the event loop — scheduling
    onto the loop and then waiting on it from the loop itself deadlocks."""
    _call(_alert_owner(text))


def _on_job_error(job: dict, e: Exception) -> None:
    _safe_post(job, t("job_error", e=e))


def _safe_post(job: dict, text: str) -> None:
    _post(job["channel"], text)


def _post(channel_id: str, text: str) -> None:
    channel = _resolve_channel(channel_id)
    if channel is None:
        log.warning("no channel to post to: %s", channel_id)
        return
    for chunk in _chunks(text):
        _call(channel.send(chunk))


def _chunks(s: str):
    s = s or t("empty")
    while len(s) > MAX_DISCORD:
        cut = s.rfind("\n", 0, MAX_DISCORD)
        if cut < MAX_DISCORD // 2:
            cut = MAX_DISCORD
        yield s[:cut]
        s = s[cut:]
    if s:
        yield s


def _fire_schedule(s: dict) -> None:
    """Scheduler callback — owner permission, posts back to the origin channel.

    ``is_dm`` stays False on purpose: a Discord channel id doesn't reveal
    whether it's a DM, and we will not guess our way out of the tamper guard.
    A scheduled run is therefore always guarded — it can still do the work,
    it just can't rewrite the permission files.
    """
    jobs.submit({
        "channel": s["channel"], "thread": None, "text": s["prompt"],
        "user": OWNER_ID, "event_id": f"sched-{s['id']}-{int(time.time())}",
        "in_thread": False, "is_mention": False, "is_dm": False,
        "permission_mode": config.PERMISSION_MODE, "kind": "scheduled",
        "reply_prefix": t("sched_fired", id=s["id"], spec=scheduler.spec_str(s)),
    })


# ─────────────────────────── Discord events ───────────────────────────
@bot.event
async def on_ready():
    global _loop
    _loop = asyncio.get_running_loop()
    log.info("discord connected as %s (%s)", bot.user, bot.user.id)
    print(f"Loki (Discord) — connected as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:                  # ignore self and every other bot
        return
    channel = message.channel
    is_mention = bot.user in message.mentions
    if _is_dm(channel):
        await _dispatch(message, is_mention=False)
        return
    if blocked.is_blocked(str(channel.id)):
        return
    if is_mention:
        await _dispatch(message, is_mention=True)
        return
    # No mention: engage only inside a registered auto-listen zone.
    thread_id = str(channel.id) if _is_thread(channel) else None
    if autolisten.is_zone(str(channel.id), thread_id):
        await _dispatch(message, is_mention=False, auto_listen=True)


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Tell the owner where Loki just landed, and how to shut it off.
    Awaited directly — we're already on the loop here."""
    await _alert_owner(t("invited_guild", name=guild.name))


async def _dispatch(message: discord.Message, is_mention: bool,
                    auto_listen: bool = False) -> None:
    channel = message.channel
    user = str(message.author.id)
    is_owner = user == OWNER_ID
    channel_id = str(channel.id)
    in_thread = _is_thread(channel)

    if not is_owner:
        # Non-owner: only via @mention or inside an auto-listen zone (never a
        # bare DM), always forced read-only.
        if not (is_mention or auto_listen):
            return
    permission_mode = config.PERMISSION_MODE if is_owner else "plan"

    if dedup.already_seen(str(message.id)):
        return

    text = _strip_mention(message.content or "")
    img_paths, doc_paths = ((await _download_attachments(message)) if is_owner
                            else ([], []))
    if not text and not img_paths and not doc_paths:
        return
    if not text:                           # attachment dropped with no caption
        text = t("image_default") if img_paths else t("file_default")

    org = None if is_owner else orgs.resolve(user, channel_id)
    session_key = _session_key(channel)

    if is_owner:
        m = _SEND_RE.match(text)
        if m:
            msg = (_send_files(channel, m.group(1)) if m.group(1)
                   else t("send_usage"))
            if msg:
                await channel.send(msg)
            return

    reply = commands.handle(text, {
        "channel": channel_id,
        "thread": channel_id if in_thread else None,
        "session_key": session_key,
        "is_dm": _is_dm(channel),
        "is_owner": is_owner,
        "name_of": _user_name,
        "user_ids": [i for i in _USER_MENTION_RE.findall(message.content or "")
                     if i != str(bot.user.id)],
        "is_user_id": lambda tk: bool(_SNOWFLAKE_RE.fullmatch(tk)),
        "is_channel_id": lambda tk: bool(_SNOWFLAKE_RE.fullmatch(tk)),
    })
    if reply is not None:
        for chunk in _chunks(reply):
            await channel.send(chunk)
        return

    # Guest throttle — an org's rate overrides the global GUEST_RATE_PER_HOUR.
    if not is_owner:
        limit = orgs.rate(org)
        allowed, retry = ratelimit.check(user, limit=limit)
        if not allowed:
            await channel.send(
                t("rate_limited",
                  n=limit if limit is not None else config.GUEST_RATE_PER_HOUR,
                  m=retry))
            return

    qsize = jobs.JOBS.qsize()
    try:
        await message.add_reaction("👀")
    except Exception:
        pass
    if qsize > 0:
        await channel.send(t("queued", n=qsize))

    jobs.submit({"channel": channel_id, "thread": channel_id if in_thread else None,
                 "text": text, "user": user, "event_id": str(message.id),
                 "in_thread": in_thread, "is_mention": is_mention,
                 "is_dm": _is_dm(channel), "permission_mode": permission_mode,
                 "kind": "owner" if is_owner else "guest",
                 "session_key": session_key,
                 "org": org, "image_paths": img_paths, "doc_paths": doc_paths})


# ─────────────────────────── entrypoint ───────────────────────────
def run() -> None:
    scope.ensure_manifest()
    alias.ensure_file()
    selftest.run()
    health.start("discord")
    jobs.start(_handle, _on_job_error, kill=brain.tree_kill)
    scheduler.start(_fire_schedule)
    log.info("worker starting platform=discord allowlist=%s work_dir=%s mode=%s",
             OWNER_ID, config.WORK_DIR, config.PERMISSION_MODE)
    print(f"Loki (Discord) — allowlist={OWNER_ID}, work_dir={config.WORK_DIR}, "
          f"mode={config.PERMISSION_MODE}")
    print("Connecting to Discord…")
    try:
        bot.run(BOT_TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        print("[loki] Discord rejected the connection: the MESSAGE CONTENT "
              "intent is off.\n"
              "       Enable it at https://discord.com/developers/applications "
              "→ your app → Bot → Privileged Gateway Intents.", file=sys.stderr)
        log.critical("MESSAGE CONTENT intent not enabled")
        sys.exit(2)
    except discord.LoginFailure:
        print("[loki] Discord login failed — check DISCORD_BOT_TOKEN.",
              file=sys.stderr)
        log.critical("discord login failed")
        sys.exit(2)
