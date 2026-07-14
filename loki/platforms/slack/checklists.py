"""Slack glue for checklists — posting, click routing, conversational toggles.

Pure model/logic lives in loki.core.checklist; this module is the only part
that touches the Slack SDK. adapter.py calls `register(app)` once (wires the
checkbox-click handler) and `try_handle(ctx)` inside _dispatch (owner `!check`
create + everyone's `완료 N` toggles inside a checklist thread).

Clickable checkboxes require *Interactivity* enabled on the Slack app
(manifest: settings.interactivity.is_enabled: true). With Socket Mode on, no
Request URL is needed — payloads arrive over the socket.
"""
from __future__ import annotations

import re

from ...core import checklist as C
from ...core import config
from ...core.config import log, t

_DIR = config.STATE / "checklists"
_CMD_RE = re.compile(r"^!(?:check|todo|체크)\b", re.IGNORECASE)


# ── i18n helpers (core stays English-only; Korean labels applied here) ────────
def _labels() -> dict:
    if config.LANG == "ko":
        return {"title": "체크리스트", "empty": "_(항목 없음)_",
                "footer": "✅ {done}/{total} 완료 · 버튼 클릭 또는 `완료 N`"}
    return {}


def _title_default() -> str:
    return "체크리스트" if config.LANG == "ko" else "Checklist"


def _blocks(cl: dict) -> list[dict]:
    return C.render_blocks(cl, _labels())


def _fallback(cl: dict) -> str:
    return C.fallback_text(cl, _title_default())


# ── click handler (registered on the app) ────────────────────────────────────
def register(app) -> None:
    app.action(C.ACTION_ID)(_on_toggle)


def _on_toggle(ack, body, client, logger=None) -> None:
    ack()                                        # must ack within 3s
    try:
        channel = (body.get("channel") or {}).get("id")
        ts = (body.get("message") or {}).get("ts")
        acts = body.get("actions") or []
        if not (channel and ts and acts):
            return
        item_id = acts[0].get("value")           # button value = the item id
        cl = C.load_by_ts(_DIR, channel, ts)
        if not cl or not item_id:                # unknown/expired checklist
            return
        cl = C.toggle_item(cl, item_id)
        C.save(_DIR, cl)
        client.chat_update(channel=channel, ts=ts,
                           blocks=_blocks(cl), text=_fallback(cl))
    except Exception:
        log.exception("checklist toggle failed")


# ── message handling (called from _dispatch) ─────────────────────────────────
def try_handle(ctx: dict) -> bool:
    """Return True if this message was a checklist command/toggle we consumed."""
    text = (ctx.get("text") or "").strip()
    if _CMD_RE.match(text):
        return _handle_command(ctx, _CMD_RE.sub("", text, count=1).strip())
    # bare conversational toggle — only when clearly inside a checklist context,
    # so ordinary messages fall through to the brain untouched.
    action, refs, is_all = C.parse_toggle(text)
    if action and (refs or is_all):
        cl = _target_for_message(ctx)
        if cl and cl.get("message_ts"):
            _apply(ctx, cl, action, refs, is_all)
            return True
    return False


def _target_for_message(ctx: dict) -> dict | None:
    channel = ctx["channel"]
    cl = C.find_target(_DIR, channel, ctx.get("thread_root"))
    if cl:
        return cl
    if str(channel).startswith("D"):             # DM: one conversation → latest
        return C.find_latest(_DIR, channel)
    return None


def _handle_command(ctx: dict, arg: str) -> bool:
    action, refs, is_all = C.parse_toggle(arg)
    if action and (refs or is_all):              # e.g. `!check 완료 2`
        cl = _target_for_message(ctx) or C.find_latest(_DIR, ctx["channel"])
        if not cl or not cl.get("message_ts"):
            ctx["post"](ctx["channel"], ctx["thread"], t("check_none"))
            return True
        _apply(ctx, cl, action, refs, is_all)
        return True
    if not ctx.get("is_owner"):                  # creation is owner-only
        ctx["post"](ctx["channel"], ctx["thread"], t("check_owner_only"))
        return True
    title, items = C.parse(arg)
    if not items:
        ctx["post"](ctx["channel"], ctx["thread"], t("check_usage"))
        return True
    _create(ctx, title, items)
    return True


def _create(ctx: dict, title: str | None, items: list[str]) -> None:
    app_, channel = ctx["app"], ctx["channel"]
    thread_root = ctx.get("thread_root")         # post inside the thread if any
    cl = C.new(channel, title, items, ctx.get("user") or "", thread_root)
    try:
        resp = app_.client.chat_postMessage(
            channel=channel, thread_ts=thread_root,
            blocks=_blocks(cl), text=_fallback(cl))
    except Exception:
        log.exception("checklist post failed")
        ctx["post"](channel, ctx["thread"], t("check_post_fail"))
        return
    cl["message_ts"] = resp.get("ts")
    C.save(_DIR, cl)


def _apply(ctx: dict, cl: dict, action: str, refs: list[int], is_all: bool) -> None:
    checked = action == "check"
    cl2 = (C.set_all(cl, checked) if is_all
           else C.set_checked(cl, C.refs_to_ids(cl, refs), checked))
    C.save(_DIR, cl2)
    try:
        ctx["app"].client.chat_update(
            channel=cl2["channel"], ts=cl2["message_ts"],
            blocks=_blocks(cl2), text=_fallback(cl2))
    except Exception:
        log.exception("checklist update failed")
    ev = ctx.get("event") or {}
    if ev.get("ts"):                             # quiet ack on the command message
        try:
            ctx["app"].client.reactions_add(
                channel=ctx["channel"], name="white_check_mark", timestamp=ev["ts"])
        except Exception:
            pass
