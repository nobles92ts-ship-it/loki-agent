"""Slack glue for polls — asking for one, voting, closing.

Pure model lives in loki.core.poll; this module is the only part that touches
the Slack SDK. adapter.py calls `register(app)` once (wires the vote button)
and `try_handle(ctx)` inside _dispatch.

Anyone who reaches Loki can ask for a poll in plain words — "이 후보들 투표로
만들어줘" — and Loki builds it from the thread (or, outside a thread, the
channel's recent talk). The model only *reads* that talk and answers with JSON;
core.poll validates it and this module posts it. Whoever made a poll (or the
owner) can close or remove it; anyone can ask for the tally.

Vote buttons need *Interactivity* enabled on the Slack app (manifest:
settings.interactivity.is_enabled: true) — the checklist needs the same.
"""
from __future__ import annotations

import html
import threading
import time

from ...core import brain, budget, guard, orgs, ratelimit, scope, usage
from ...core import config
from ...core import poll as P
from ...core.config import log, t

_DIR = config.STATE / "polls"
_vote_lock = threading.Lock()                   # a click is read → changed → written → re-rendered
_slots = threading.BoundedSemaphore(2)          # model readings in flight at once


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


# ── i18n helpers (core stays English-only; Korean labels applied here) ────────
def _labels() -> dict:
    if config.LANG == "ko":
        return {"title": "투표", "vote": "투표하기",
                "vote_one": "{n}표", "vote_many": "{n}표",
                "multi": "복수 선택", "single": "하나만 선택",
                "closed": "🔒 마감됨 — 더 받지 않아요",
                "footer": "<@{by}> 님이 만듦 · {n}명 참여 · 눌러서 투표, 다시 누르면 취소",
                "footer_closed": "<@{by}> 님이 만듦 · {n}명 참여",
                "more": "외 {n}명"}
    return {}


def _blocks(poll: dict) -> list[dict]:
    return P.render_blocks(poll, _labels())


def _fallback(poll: dict) -> str:
    return P.fallback_text(poll, _labels().get("title", "Poll"))


# ── vote button (registered on the app) ──────────────────────────────────────
def register(app) -> None:
    app.action(P.ACTION_ID)(_on_vote)


def _on_vote(ack, body, client, logger=None) -> None:
    ack()                                        # must ack within 3s
    try:
        channel = (body.get("channel") or {}).get("id")
        ts = (body.get("message") or {}).get("ts")
        user = (body.get("user") or {}).get("id")
        acts = body.get("actions") or []
        if not (channel and ts and user and acts):
            return
        with _vote_lock:
            poll = P.load_by_ts(_DIR, channel, ts)
            if not poll:                         # unknown/expired poll
                return
            changed = P.vote(poll, acts[0].get("value"), user)
            if changed is poll:                  # closed, or nothing to change
                return
            P.save(_DIR, changed)
            client.chat_update(channel=channel, ts=ts,
                               blocks=_blocks(changed), text=_fallback(changed))
    except Exception:
        log.exception("poll vote failed")


# ── message handling (called from _dispatch) ─────────────────────────────────
def try_handle(ctx: dict) -> bool:
    """True if this message was a poll request we consumed."""
    if ctx.get("from_bot"):                      # a bot's message is text, not a command
        return False
    text = html.unescape((ctx.get("text") or "").strip())   # Slack escapes & < >
    kind = P.classify(text)
    if kind == "create":
        return _start_create(ctx, text)
    if kind:
        return _manage(ctx, kind)
    return False


def _guest_gate(ctx: dict) -> str | None:
    """Why this caller may not spend a model run right now (None = go ahead).
    Same budget and hourly cap as any other guest request; the owner has none."""
    if ctx["is_owner"]:
        return None
    ok, key, params = budget.check_guest(ctx.get("org"))
    if not ok:
        return t(key, **params)
    limit = orgs.rate(ctx.get("org"))
    allowed, retry = ratelimit.check(ctx["user"], limit=limit)
    if not allowed:
        return t("rate_limited",
                 n=limit if limit is not None else config.GUEST_RATE_PER_HOUR, m=retry)
    return None


def _start_create(ctx: dict, text: str) -> bool:
    post, channel, thread = ctx["post"], ctx["channel"], ctx["thread"]
    why = _guest_gate(ctx)
    if why:
        post(channel, thread, why)
        return True
    if not _slots.acquire(blocking=False):
        post(channel, thread, t("poll_busy"))
        return True
    ev = ctx.get("event") or {}
    if ev.get("ts"):                             # quiet "on it" mark on the request
        try:
            ctx["app"].client.reactions_add(channel=channel, name="eyes", timestamp=ev["ts"])
        except Exception:
            pass

    def run() -> None:
        try:
            _create(ctx, text)
        except Exception:
            log.exception("poll create failed")
            post(channel, thread, t("poll_fail"))
        finally:
            _slots.release()
    _spawn(run)
    return True


def _context_for(ctx: dict) -> str:
    """What the people said before the request: this thread, or — for a request
    that opens a conversation — the channel's recent talk."""
    if ctx.get("thread_root"):
        return ctx["thread_context"](ctx["channel"], ctx["thread_root"])
    return ctx["channel_context"](ctx["channel"])


def _ask(ctx: dict, prompt: str) -> dict:
    """One tool-less reading of the talk. It runs on the restricted route every
    guest request takes — the thread is other people's words, and the model
    needs no file to turn them into a list."""
    if ctx["is_owner"]:
        settings, cwd = guard.settings_file(), None
        roots = scope.read_roots(scope.guest_scope()[1])
    else:
        settings, manifest = scope.write_scope_settings(ctx.get("org"))
        cwd, roots = str(scope.loki_dir()), scope.read_roots(manifest)
    t0 = time.time()
    res = brain.run_claude(prompt, None, "plan", settings_file=settings, cwd=cwd,
                           read_roots=roots)
    usage.record("poll", ctx["user"], res["reason"] == "ok", time.time() - t0,
                 res["reason"], org=ctx.get("org"))
    return res


def _create(ctx: dict, text: str) -> None:
    app_, post, channel, thread = ctx["app"], ctx["post"], ctx["channel"], ctx["thread"]
    thread_root = ctx.get("thread_root")         # post inside the thread if any
    res = _ask(ctx, P.build_prompt(text, _context_for(ctx), time.strftime("%Y-%m-%d (%a)")))
    if res["reason"] == "quota":
        post(channel, thread, t("quota"))
        return
    if res.get("error"):
        post(channel, thread, t("poll_fail"))
        return
    reply = P.parse_reply(res.get("text", "")) or {}
    questions = P.clean_questions(reply.get("questions"))
    if not questions:
        ask_back = reply.get("question")
        if isinstance(ask_back, str) and ask_back.strip():
            post(channel, thread, "🤔 " + ask_back.strip()[:500])
        else:
            post(channel, thread, t("poll_no_options"))
        return
    poll = P.new(channel, reply.get("title") if isinstance(reply.get("title"), str) else None,
                 questions, ctx.get("user") or "", thread_root)
    try:
        resp = app_.client.chat_postMessage(
            channel=channel, thread_ts=thread_root,
            blocks=_blocks(poll), text=_fallback(poll))
    except Exception:
        log.exception("poll post failed")
        post(channel, thread, t("poll_post_fail"))
        return
    poll["message_ts"] = resp.get("ts")
    P.save(_DIR, poll)


def _manage(ctx: dict, kind: str) -> bool:
    """Tally, close or remove the poll this message is about. Anything that
    isn't clearly about one of our polls falls through to the ordinary path."""
    post, channel, thread = ctx["post"], ctx["channel"], ctx["thread"]
    thread_root = ctx.get("thread_root")
    poll = P.find_target(_DIR, channel, thread_root)
    if poll is None and (kind == "results" or not thread_root):
        poll = P.find_latest(_DIR, channel)      # outside a thread: the channel's newest
    if not poll or not poll.get("message_ts"):
        return False
    labels = _labels()
    if kind == "results":
        post(channel, thread, P.results_text(poll, labels, ctx.get("user_name")))
        return True
    if not (ctx["is_owner"] or ctx.get("user") == poll.get("created_by")):
        post(channel, thread, t("poll_creator_only"))
        return True
    client = ctx["app"].client
    if kind == "close":
        poll = {**poll, "closed": True}
        with _vote_lock:
            P.save(_DIR, poll)
            try:
                client.chat_update(channel=channel, ts=poll["message_ts"],
                                   blocks=_blocks(poll), text=_fallback(poll))
            except Exception:
                log.exception("poll close update failed")
        post(channel, thread, P.results_text(poll, labels, ctx.get("user_name")))
        return True
    with _vote_lock:                             # cancel — take the message down
        try:
            client.chat_delete(channel=channel, ts=poll["message_ts"])
        except Exception:
            log.exception("poll delete failed")
        P.delete(_DIR, poll)
    post(channel, thread, t("poll_removed"))
    return True
