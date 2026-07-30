"""Telegram adapter — Bot API long polling.

Same four hooks as Slack and Discord, over ``loki.core``: commands, sessions,
scoping, budgets and the queue all come from there unchanged, so this file is
only what is Telegram-shaped.

No new dependency. The Bot API is plain HTTPS + JSON, so `urllib` covers it;
pulling in an SDK to call five endpoints would cost every Slack and Discord
user an install for something they don't run.

Trust model, mapped onto Telegram's surfaces:

* **Private chat** is the owner's DM. ``TELEGRAM_OWNER_ID`` is the one numeric
  id that reaches it; anyone else messaging privately is ignored, exactly as a
  non-owner Slack DM is.
* **Groups** are the guest surface. A guest must address the bot — @mention or
  a reply to it — or be inside an auto-listen zone, and is forced read-only,
  path-scoped, rate-limited and budget-capped like any other guest.
* Telegram has no workspace boundary, so there is no "anyone in the org" tier:
  unaddressed group chatter is never processed.

Keep BotFather's group privacy setting **on** — it means the bot only receives
messages that mention it, which is the same boundary this adapter enforces.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ...core import (alias, autolisten, blocked, brain, budget, commands,
                     config, dedup, files, guard, health, jobs, orgs,
                     ratelimit, scheduler, scope, selftest, sessions, tgmarkup,
                     usage)
from ...core.config import log, require, t
from ...core.prompt import build_prompt

BOT_TOKEN = require("TELEGRAM_BOT_TOKEN")
OWNER_ID = require("TELEGRAM_OWNER_ID")     # fail-closed: no allowlist, no boot

API_ROOT = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_ROOT = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
POLL_TIMEOUT = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "50"))
HTTP_TIMEOUT = POLL_TIMEOUT + 15
CTX_MESSAGES = 40            # recent group messages kept as conversation context

BOT_USERNAME: str | None = None
BOT_USER_ID: str | None = None

_SEND_RE = re.compile(r"^!(?:send|전송|파일)(?:\s+(.+))?$", re.IGNORECASE)
_NUMERIC_ID_RE = re.compile(r"-?\d{5,}")

_names: dict[str, str] = {}
# Recent group messages per chat, kept in memory as conversation context —
# a bot can't read history it wasn't present for, so this is the only source.
_history: dict[str, list] = {}
_hist_lock = threading.Lock()


# ─────────────────────────── Bot API ───────────────────────────
def _api(method: str, params: dict | None = None, timeout: int | None = None) -> dict:
    """Call the Bot API. Returns the `result` payload, or {} on failure."""
    data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_ROOT}/{method}", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
        return payload.get("result") or {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        log.warning("telegram %s failed: %s %s", method, e.code, body)
        raise
    except Exception:
        log.exception("telegram %s failed", method)
        return {}


def _send(chat_id: str, text: str, thread_id: str | None = None,
          html: bool = True) -> bool:
    """Send a (chunked) message, falling back to plain text if HTML is refused.

    Model output can produce markup Telegram dislikes; losing the formatting is
    an acceptable outcome, losing the answer is not. Returns False when even
    the plain-text retry was refused — a scheduled fire aimed at a chat Loki
    isn't in, most often.
    """
    ok = True
    for chunk in tgmarkup.chunks(text or t("empty")):
        params = {"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True}
        if thread_id:
            params["message_thread_id"] = int(thread_id)
        if html:
            params["parse_mode"] = "HTML"
        try:
            _api("sendMessage", params)
        except Exception:
            if not html:
                continue
            params.pop("parse_mode", None)
            params["text"] = re.sub(r"<[^>]+>", "", chunk)
            try:
                _api("sendMessage", params)
            except Exception:
                log.exception("telegram send failed after plain-text retry")
                ok = False
    return ok


def _send_document(chat_id: str, path, thread_id: str | None = None) -> bool:
    """Upload one file with a multipart body (no requests dependency)."""
    boundary = f"----loki{int(time.time() * 1000)}"
    fields = {"chat_id": chat_id}
    if thread_id:
        fields["message_thread_id"] = str(thread_id)
    body = bytearray()
    for key, value in fields.items():
        body += (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                 f"{value}\r\n").encode("utf-8")
    try:
        payload = path.read_bytes()
    except Exception:
        log.exception("telegram document read failed")
        return False
    name = urllib.parse.quote(path.name)
    body += (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="document"; filename="{name}"\r\n'
             f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{API_ROOT}/sendDocument", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
            return True
    except Exception:
        log.exception("telegram document upload failed")
        return False


def _download(file_id: str, dest) -> bool:
    """Resolve a Telegram file id and save it, honouring the size cap."""
    try:
        info = _api("getFile", {"file_id": file_id})
        path = info.get("file_path")
        if not path:
            return False
        with urllib.request.urlopen(f"{FILE_ROOT}/{path}", timeout=60) as r:
            data = r.read(files.MAX_FILE_BYTES + 1)
        if len(data) > files.MAX_FILE_BYTES:
            log.warning("telegram attachment over the size cap, skipped")
            return False
        dest.write_bytes(data)
        return True
    except Exception:
        log.exception("telegram download failed")
        return False


def _user_name(uid: str | None) -> str | None:
    return _names.get(str(uid)) if uid else None


# ─────────────────────────── context ───────────────────────────
def _remember(chat_id: str, who: str, text: str) -> None:
    if not text:
        return
    with _hist_lock:
        buf = _history.setdefault(chat_id, [])
        buf.append((time.time(), who, text[:400]))
        del buf[:-CTX_MESSAGES]


def _context(chat_id: str) -> str:
    """Recent chatter in this group, as reference data for the injection guard."""
    with _hist_lock:
        buf = list(_history.get(chat_id) or [])
    if len(buf) <= 1:
        return ""
    return "\n".join(
        f"[{time.strftime('%m-%d %H:%M', time.localtime(ts))} {who}] {txt}"
        for ts, who, txt in buf)[-10000:]


# ─────────────────────────── job handling ───────────────────────────
def _handle(job: dict) -> None:
    chat, thread_id = job["channel"], job.get("thread_id")
    notice = threading.Timer(60.0, _send,
                             args=(chat, t("processing_notice"), thread_id))
    notice.daemon = True
    notice.start()
    # Permission files may only change in the owner's own private chat — a
    # transport fact (see core.guard). Everything else is snapshot and reverted.
    owner_dm = job["user"] == OWNER_ID and job.get("is_dm")
    snap = None if owner_dm else guard.snapshot()
    try:
        context = _context(chat) if job.get("with_context") else ""
        kind = "kind_channel" if context else "kind_thread"
        prompt = build_prompt(context, job["text"], kind,
                              t("scope_channel", d=1, n=CTX_MESSAGES)
                              if context else "")
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
            if not owner_dm:                 # group context = injection risk
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
        _budget_alerts(budget.note_usage())
        log.info("job id=%s kind=%s user=%s reason=%s dur=%ds chars=%d",
                 job.get("id"), job.get("kind"), job["user"], res["reason"],
                 int(dur), len(res["text"]))

        if res["reason"] == "quota":
            _send(chat, t("quota"), thread_id)
            time.sleep(30)
            return
        body = job.get("reply_prefix", "") + tgmarkup.to_html(res["text"])
        if not _send(chat, body, thread_id) and job.get("fallback_channel"):
            _send(job["fallback_channel"],
                  t("sched_post_failed", cid=chat) + body)
        _remember(chat, "Loki", res["text"][:400])
        if job.get("user") == OWNER_ID:
            for rp in files.find_reply_files(res["text"]):
                _send_document(chat, rp, thread_id)
    finally:
        notice.cancel()
        if snap is not None:
            reverted = guard.restore(snap)
            if reverted:
                _send(OWNER_ID, guard.alert_text(reverted))
                _send(chat, t("tamper_blocked"), thread_id)


def _download_attachments(msg: dict) -> tuple[list[str], list[str]]:
    """Owner attachments → (image paths, document paths). Same allowlist as
    every other platform; rejected names are reported by the caller."""
    imgs: list[str] = []
    docs: list[str] = []
    items, _ = _classify_attachments(msg)
    if not items:
        return imgs, docs
    img_dir, doc_dir = files.inbox_dir("img"), files.inbox_dir("files")
    files.prune_old(img_dir)
    files.prune_old(doc_dir)
    for i, f in enumerate(items):
        is_image = f["kind"] == "image"
        dest = ((img_dir if is_image else doc_dir)
                / files.safe_filename(f["name"], i))
        if _download(f["file_id"], dest):
            (imgs if is_image else docs).append(str(dest))
    return imgs, docs


def _classify_attachments(msg: dict) -> tuple[list[dict], list[str]]:
    """(accepted, rejected names) — deny-by-default, same as Slack/Discord."""
    accepted: list[dict] = []
    rejected: list[str] = []
    photos = msg.get("photo") or []
    if photos:                                   # sizes ascending → largest
        accepted.append({"file_id": photos[-1]["file_id"],
                         "name": "photo.jpg", "kind": "image"})
    doc = msg.get("document")
    if doc:
        name = doc.get("file_name") or "file"
        kind = files.classify_inbound(name, doc.get("mime_type") or "")
        if kind:
            accepted.append({"file_id": doc["file_id"], "name": name,
                             "kind": kind})
        else:
            rejected.append(name)
    return accepted[:files.MAX_INBOUND_FILES], rejected


def _on_job_error(job: dict, e: Exception) -> None:
    _send(job["channel"], t("job_error", e=e), job.get("thread_id"))


def _budget_alerts(alerts: list) -> None:
    """Budget thresholds go to the owner's private chat. No buttons — inline
    keyboards need their own callback routing, and the text commands cover it."""
    for a in alerts:
        text = t("budget_alert_full" if a["threshold"] >= 100
                 else "budget_alert_warn",
                 label=a["label"], used=a["used"], limit=a["limit"],
                 pct=a["threshold"])
        text += "\n" + (t(a["applied"]) if a["applied"] else t("budget_help"))
        _send(OWNER_ID, text)


def _fire_schedule(s: dict) -> None:
    """Scheduler callback — owner permission, posting where the schedule says.

    ``is_dm`` stays False on purpose: a chat id doesn't prove it's a private
    chat, and we won't guess our way out of the tamper guard. A scheduled run
    is therefore always guarded — it can work, it just can't rewrite the
    permission files.
    """
    target = s.get("post_to") or s["channel"]
    jobs.submit({
        "channel": target, "thread": None, "thread_id": None,
        "fallback_channel": s["channel"] if target != s["channel"] else None,
        "text": s["prompt"], "user": OWNER_ID,
        "event_id": f"sched-{s['id']}-{int(time.time())}",
        "permission_mode": config.PERMISSION_MODE, "kind": "scheduled",
        "with_context": False, "is_dm": False, "session_key": None,
        "reply_prefix": t("sched_fired", id=s["id"], spec=scheduler.spec_str(s)),
    })


# ─────────────────────────── update handling ───────────────────────────
def _addressed(msg: dict, text: str) -> bool:
    """True when a group message is aimed at Loki (mention or a reply to it)."""
    if BOT_USERNAME and f"@{BOT_USERNAME}".lower() in text.lower():
        return True
    reply = msg.get("reply_to_message") or {}
    return str((reply.get("from") or {}).get("id") or "") == str(BOT_USER_ID)


def _strip_mention(text: str) -> str:
    if not BOT_USERNAME:
        return (text or "").strip()
    return re.sub(rf"@{re.escape(BOT_USERNAME)}\b", "", text or "",
                  flags=re.IGNORECASE).strip()


def _send_files(chat_id: str, thread_id, spec: str) -> str:
    if not (spec or "").strip():
        return t("send_usage")
    paths, err, detail = files.resolve_outbound(spec)
    if err:
        return t(err, p=detail, name=detail, work=config.WORK_DIR,
                 max=files.MAX_FILE_MB)
    failed = sum(0 if _send_document(chat_id, rp, thread_id) else 1
                 for rp in paths)
    return t("send_fail", n=failed) if failed else ""


def _dispatch(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    sender = msg.get("from") or {}
    if sender.get("is_bot"):                     # no bot-to-bot loops
        return
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    user = str(sender.get("id") or "")
    if not chat_id or not user:
        return
    if dedup.already_seen(f"tg-{update.get('update_id')}"):
        return

    _names[user] = sender.get("username") or sender.get("first_name") or user
    is_owner = user == OWNER_ID
    is_private = chat.get("type") == "private"
    if is_private and not is_owner:
        return                                   # the private chat is the owner's

    thread_id = msg.get("message_thread_id")
    raw = (msg.get("text") or msg.get("caption") or "").strip()

    accepted, rejected = ([], [])
    if is_owner:
        accepted, rejected = _classify_attachments(msg)
    if rejected:
        _send(chat_id, t("file_rejected", names=", ".join(rejected[:4])), thread_id)

    if not is_private:
        blocked_here = blocked.is_blocked(chat_id)
        if blocked_here and not is_owner:
            return                               # blocked groups are silent
        _remember(chat_id, _user_name(user) or user, raw)
        # Blocking switches off the zone too, but the owner addressing Loki
        # directly still works — or `!unblock` is unreachable from the one
        # place you'd think to type it.
        in_zone = (not blocked_here) and autolisten.is_zone(
            chat_id, str(thread_id) if thread_id else None)
        if not (_addressed(msg, raw) or in_zone):
            return

    text = _strip_mention(raw)
    if not text and not accepted:
        return
    if not text:                                 # attachment with no caption
        text = (t("image_default")
                if any(a["kind"] == "image" for a in accepted)
                else t("file_default"))

    session_key = sessions.key_for(chat_id,
                                   str(thread_id) if thread_id else None,
                                   is_dm=is_private)

    if is_owner:
        m = _SEND_RE.match(text)
        if m:
            reply = _send_files(chat_id, thread_id, m.group(1) or "")
            if reply:
                _send(chat_id, reply, thread_id)
            return

    reply = commands.handle(text, {
        "channel": chat_id,
        "thread": str(thread_id) if thread_id else None,
        "session_key": session_key,
        "is_dm": is_private,
        "is_owner": is_owner,
        "name_of": _user_name,
        "user_ids": [],          # Telegram mentions carry @usernames, not ids
        "is_user_id": lambda tk: bool(_NUMERIC_ID_RE.fullmatch(tk)),
        "is_channel_id": lambda tk: bool(_NUMERIC_ID_RE.fullmatch(tk)),
        "chan_ref": lambda cid: str(cid),
    })
    if reply is not None:
        _send(chat_id, reply, thread_id)
        return

    org = None if is_owner else orgs.resolve(user, chat_id)

    kind = "owner" if is_owner else "guest"
    reply_prefix = ""
    expanded = alias.resolve(text, is_owner, org, orgs.allows_command)
    if expanded is not None:
        text, reply_prefix = expanded
        if not text:
            return               # known alias, this caller wasn't granted it
        kind = "alias"

    if not is_owner:
        ok, key, params = budget.check_guest(org)
        if not ok:
            _send(chat_id, t(key, **params), thread_id)
            return
        limit = orgs.rate(org)
        allowed, retry = ratelimit.check(user, limit=limit)
        if not allowed:
            _send(chat_id, t("rate_limited",
                             n=limit if limit is not None
                             else config.GUEST_RATE_PER_HOUR, m=retry), thread_id)
            return

    img_paths, doc_paths = _download_attachments(msg) if is_owner else ([], [])

    jobs.submit({"channel": chat_id, "thread": session_key,
                 "thread_id": thread_id, "text": text, "user": user,
                 "event_id": f"tg-{update.get('update_id')}",
                 "permission_mode": config.PERMISSION_MODE if is_owner else "plan",
                 "kind": kind, "reply_prefix": reply_prefix, "org": org,
                 "is_dm": is_private, "session_key": session_key,
                 "with_context": not is_private,
                 "image_paths": img_paths, "doc_paths": doc_paths})


# ─────────────────────────── entrypoint ───────────────────────────
def _poll_loop() -> None:
    """Long-poll getUpdates forever, backing off when Telegram is unreachable."""
    offset = 0
    backoff = 1
    while True:
        try:
            updates = _api("getUpdates", {
                "offset": offset, "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message", "edited_message"]})
        except Exception:
            updates = None
        if not isinstance(updates, list):
            time.sleep(min(backoff, 60))
            backoff = min(backoff * 2, 60)
            continue
        backoff = 1
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            try:
                _dispatch(update)
            except Exception:
                log.exception("telegram dispatch failed")


def run() -> None:
    global BOT_USERNAME, BOT_USER_ID
    scope.ensure_manifest()
    alias.ensure_file()
    selftest.run()
    try:
        me = _api("getMe", timeout=30)
    except Exception as e:          # noqa: BLE001 — a bad token 401s here
        log.critical("telegram getMe failed: %s", e)
        me = {}
    if not me.get("id"):
        print("[loki] Telegram rejected TELEGRAM_BOT_TOKEN — check it in .env "
              "(get one from @BotFather).", file=sys.stderr)
        sys.exit(2)
    BOT_USERNAME, BOT_USER_ID = me.get("username"), str(me.get("id"))

    health.start("telegram")
    jobs.start(_handle, _on_job_error, kill=brain.tree_kill)
    scheduler.start(_fire_schedule)
    log.info("worker starting platform=telegram allowlist=%s work_dir=%s mode=%s",
             OWNER_ID, config.WORK_DIR, config.PERMISSION_MODE)
    print(f"Loki (Telegram) — @{BOT_USERNAME}, allowlist={OWNER_ID}, "
          f"work_dir={config.WORK_DIR}, mode={config.PERMISSION_MODE}")
    print("Polling Telegram…")
    _poll_loop()
