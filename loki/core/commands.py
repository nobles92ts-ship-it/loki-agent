"""The owner's `!` vocabulary, shared by every platform.

Parsing, formatting and state changes live here so Slack, Discord and whatever
comes next expose exactly the same commands instead of drifting apart. Each
handler returns the text to post — the adapter only delivers it.

Adapters supply a small context dict for the things only they can know
(``handle`` documents it): how to turn a user id into a name, and which ids the
caller referenced, since mention markup and id shapes differ per platform.

Two commands stay adapter-side because they aren't plain text: ``!summary``
enqueues a job against another channel, and ``!check`` posts interactive
buttons. Everything else belongs here.
"""
from __future__ import annotations

import re
import time
from typing import Callable

from . import (alias, autolisten, blocked, budget, config, jobs, learn, orgs,
               plugins, scheduler, sessions, usage)
from .config import t

# Command surface. Korean aliases sit alongside the English ones so a Korean
# workspace never has to code-switch mid-sentence.
BLOCK_RE = re.compile(r"^!(?:block|차단)\s+(\S+)$", re.IGNORECASE)
UNBLOCK_RE = re.compile(r"^!(?:unblock|차단해제)\s+(\S+)$", re.IGNORECASE)
USAGE_RE = re.compile(r"^!(?:usage|사용량)(?:\s+(\d{1,3}))?$", re.IGNORECASE)
JOBS_RE = re.compile(r"^!(?:jobs|작업목록)$", re.IGNORECASE)
CANCEL_RE = re.compile(r"^!(?:cancel|취소)\s+(j\d+)$", re.IGNORECASE)
SCHED_RE = re.compile(r"^!(?:schedule|예약)\s+(.+)$", re.IGNORECASE | re.DOTALL)
LEARN_RE = re.compile(r"^!(?:learn|학습)\s+(.+)$", re.IGNORECASE | re.DOTALL)
NEW_RE = re.compile(r"^!(?:new|새대화|리셋)$", re.IGNORECASE)
LISTEN_RE = re.compile(r"^!(?:listen|청취)$", re.IGNORECASE)
UNLISTEN_RE = re.compile(r"^!(?:unlisten|청취해제)$", re.IGNORECASE)
LISTENING_RE = re.compile(r"^!(?:listening|청취목록)$", re.IGNORECASE)
PLUGINS_RE = re.compile(r"^!(?:plugins|플러그인)$", re.IGNORECASE)
ALIAS_RE = re.compile(r"^!(?:alias|별칭)\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
_ALIAS_SUB_RE = re.compile(r"^(list|add|remove|delete|목록|추가|삭제|제거)\b\s*(.*)$",
                           re.IGNORECASE | re.DOTALL)
BUDGET_RE = re.compile(r"^!(?:budget|예산)\b\s*(.*)$", re.IGNORECASE)
ORG_RE = re.compile(r"^!(?:org|조직)\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
_ORG_SUB_RE = re.compile(
    r"^(create|list|info|add|remove|bind|unbind|allow|deny)\b\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
STOP_WORDS = ("!stop", "!cancel", "중지", "!중지")


def handle(text: str, ctx: dict) -> str | None:
    """Run a command. Returns the reply text, or None if `text` isn't one.

    Two tiers, in this order:

    1. the built-in vocabulary below — **owner only**, gated here so an adapter
       cannot forget the check
    2. this install's own plugins (:mod:`loki.core.plugins`), which carry their
       own permissions

    Built-ins win, so no plugin can shadow ``!stop`` or ``!org``.
    """
    text = (text or "").strip()
    if ctx.get("is_owner"):
        reply = _builtin(text, ctx)
        if reply is not None:
            return reply
    return plugins.handle(text, ctx)


def _fmt_plugins() -> str:
    items = plugins.listing()
    if not items:
        return t("plugins_none", path=plugins.plugins_dir())
    lines = [t("plugins_header", n=len(items))]
    for name, help_text, owner_only in items:
        who = "🔒" if owner_only else "👥"
        lines.append(f"• {who} `{name}`" + (f" — {help_text}" if help_text else ""))
    return "\n".join(lines)


def _builtin(text: str, ctx: dict) -> str | None:
    """The shipped commands. Owner-gated by the caller.

    ``ctx`` keys:
      ``channel``       current channel id
      ``thread``        current thread id, or None at top level
      ``session_key``   this conversation's session key (see core.sessions)
      ``is_dm``         True when the message came from a direct message
      ``is_owner``      True only for the configured owner
      ``name_of``       user id → display name
      ``user_ids``      user ids the caller referenced (the bot's own excluded)
      ``is_user_id``    does this bare token look like a user id?
      ``is_channel_id`` does this bare token look like a channel id?
    """
    m = BLOCK_RE.match(text)
    if m:
        blocked.set_blocked(m.group(1), True)
        return t("blocked", cid=m.group(1))
    m = UNBLOCK_RE.match(text)
    if m:
        blocked.set_blocked(m.group(1), False)
        return t("unblocked", cid=m.group(1))
    m = USAGE_RE.match(text)
    if m:
        return fmt_usage(int(m.group(1) or 7), ctx["name_of"])
    if JOBS_RE.match(text):
        return fmt_jobs(ctx["name_of"])
    m = CANCEL_RE.match(text)
    if m:
        jid = m.group(1).lower()
        key = {"killed": "cancel_killed", "dequeued": "cancel_dequeued",
               "starting": "cancel_retry"}.get(jobs.cancel(jid),
                                               "cancel_not_found")
        return t(key, id=jid)
    m = SCHED_RE.match(text)
    if m:
        return schedule_cmd(ctx["channel"], m.group(1),
                            ctx.get("chan_ref") or (lambda c: c))
    m = LEARN_RE.match(text)
    if m:
        return t("learn_saved", n=learn.capture(m.group(1)))
    if NEW_RE.match(text):
        return (t("session_reset") if sessions.reset(ctx.get("session_key"))
                else t("session_reset_none"))
    if LISTEN_RE.match(text):
        return t(autolisten.add(ctx["channel"], ctx.get("thread")))
    if UNLISTEN_RE.match(text):
        return t(autolisten.remove(ctx["channel"], ctx.get("thread")))
    if LISTENING_RE.match(text):
        return fmt_listening()
    if PLUGINS_RE.match(text):
        return _fmt_plugins()
    m = ALIAS_RE.match(text)
    if m:
        return alias_cmd(m.group(1))
    m = BUDGET_RE.match(text)
    if m:
        return budget_cmd(m.group(1))
    m = ORG_RE.match(text)
    if m:
        return org_cmd(m.group(1), ctx)
    if text.lower() in STOP_WORDS:
        n = jobs.cancel_all()
        return t("stopped_n", n=n) if n else t("nothing_running")
    return None


# ─────────────────────────── formatting ───────────────────────────
def fmt_dur(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def fmt_usage(days: int, name_of: Callable) -> str:
    data = usage.summarize(max(1, min(days, 90)))
    if not data["total"]:
        return t("usage_empty")
    lines = [t("usage_header", d=data["days"], n=data["total"], ok=data["ok"],
               fail=data["fail"], dur=fmt_dur(data["dur_total"])),
             t("usage_today", n=data["today"]["total"],
               dur=fmt_dur(data["today"]["dur"]))]
    if data["by_user"]:
        s = " · ".join(f"{name_of(u) or u} {n}" for u, n in data["by_user"][:8])
        lines.append(t("usage_by_user", s=s))
    if data["by_kind"]:
        lines.append(t("usage_by_kind",
                       s=" · ".join(f"{k} {n}" for k, n in data["by_kind"])))
    if data.get("by_org"):
        lines.append(t("usage_by_org",
                       s=" · ".join(f"{o} {n}" for o, n in data["by_org"])))
    return "\n".join(lines)


def fmt_jobs(name_of: Callable) -> str:
    items = jobs.snapshot()
    if not items:
        return t("jobs_none")
    running = [j for j in items if j["status"] == "running"]
    queued = [j for j in items if j["status"] == "queued"]
    lines = [t("jobs_header", r=len(running), q=len(queued))]
    now = time.time()
    for j in running + queued:
        who = name_of(j.get("user")) or j.get("user") or "?"
        age = fmt_dur(now - j["started"]) if j.get("started") else "—"
        snip = (j.get("text") or "").replace("\n", " ")[:48]
        lines.append(f"• {j['id']} [{j['status']}] {j.get('kind', '?')}/{who}"
                     f" · {age} · “{snip}”")
    return "\n".join(lines)


def fmt_listening() -> str:
    chans, threads = autolisten.snapshot()
    if not chans and not threads:
        return t("listening_none")
    lines = [t("listening_header", c=len(chans), t=len(threads))]
    lines += [f"• #{c}" for c in chans]
    lines += [f"• 🧵 {th}" for th in threads]
    return "\n".join(lines)


# ─────────────────────────── schedules ───────────────────────────
def schedule_cmd(channel: str, arg: str, chan_ref=lambda c: c) -> str:
    a = (arg or "").strip()
    if a.lower() in ("list", "목록"):
        items = scheduler.list_all()
        if not items:
            return t("sched_empty")
        lines = [t("sched_list_header")]
        for s in items:
            nxt = time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(s.get("next_fire", 0)))
            snip = (s.get("prompt") or "").replace("\n", " ")[:60]
            where = f" → {chan_ref(s['post_to'])}" if s.get("post_to") else ""
            lines.append(f"• {s['id']} — {scheduler.spec_str(s)}{where}"
                         f" → {nxt} · “{snip}”")
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
    msg = t("sched_added", id=item["id"], spec=scheduler.spec_str(item), next=nxt)
    if item.get("post_to"):
        # A scheduled fire runs at the owner's scope; publishing that to a
        # room is a deliberate choice, so say so as it is made.
        msg += "\n" + t("sched_added_channel", cid=item["post_to"])
    return msg


# ─────────────────────────── aliases + budget ───────────────────────────
def alias_cmd(arg: str) -> str:
    m = _ALIAS_SUB_RE.match((arg or "").strip())
    if not m:
        return t("alias_help")
    sub, rest = m.group(1).lower(), (m.group(2) or "").strip()
    if sub in ("list", "목록"):
        items = alias.all_items()
        if not items:
            return t("alias_list_empty")
        lines = [t("alias_list_header", n=len(items))]
        for name, template in sorted(items.items()):
            lines.append(t("alias_list_line", name=name,
                           prompt=template.replace("\n", " ")[:70]))
        return "\n".join(lines)
    if sub in ("remove", "delete", "삭제", "제거"):
        name = rest.split()[0].lstrip("!").lower() if rest else ""
        return (t("alias_removed", name=name) if name and alias.remove(name)
                else t("alias_not_found", name=name or "?"))
    parts = rest.split(None, 1)
    if len(parts) < 2:
        return t("alias_help")
    name = parts[0].lstrip("!")
    outcome = alias.add(name, parts[1])
    return t(f"alias_{outcome}", name=name.lower(), path=alias.aliases_file())


def budget_cmd(arg: str) -> str:
    a = (arg or "").strip()
    if not a:
        return fmt_budget()
    parts = a.split()
    head = parts[0].lower()
    if head in ("mode", "모드"):
        mode = parts[1].lower() if len(parts) > 1 else ""
        if not budget.set_mode(mode):
            return t("budget_help")
        return t("budget_mode_set", mode=mode, note=t(f"budget_mode_{mode}"))
    if head in ("daily", "weekly", "일일", "주간"):
        period = {"일일": "daily", "주간": "weekly"}.get(head, head)
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
        if not budget.set_limit(period, n):
            return t("budget_help")
        return (t("budget_limit_set", period=period, n=n) if n
                else t("budget_limit_off", period=period))
    if head in ("org", "조직"):
        if len(parts) < 3 or not parts[2].isdigit():
            return t("budget_help")
        org, n = parts[1], int(parts[2])
        if not budget.set_org_limit(org, n):
            return t("budget_help")
        return (t("budget_org_set", org=org, n=n) if n
                else t("budget_org_off", org=org))
    if head in ("off", "해제"):
        budget.clear()
        return t("budget_cleared")
    if head in budget.ACTIONS:
        return t(budget.apply(head))
    return t("budget_help")


def fmt_budget() -> str:
    s = budget.settings()
    mode = s.get("mode", "manual")
    lines = [t("budget_status_header", mode=mode,
               mode_note=t(f"budget_mode_{mode}"))]
    scopes = budget.scopes(s)
    if not scopes:
        lines.append(t("budget_status_none"))
    for sc in scopes:
        lines.append(t("budget_status_line", label=sc["label"], used=sc["used"],
                       limit=sc["limit"], pct=sc["pct"]))
    if s.get("model_override"):
        lines.append(t("budget_status_model", model=s["model_override"]))
    paused = budget.guests_paused_for()
    if paused:
        lines.append(t("budget_status_paused", n=paused))
    return "\n".join(lines)


# ─────────────────────────── orgs ───────────────────────────
def org_cmd(arg: str, ctx: dict) -> str:
    """Owner `!org …` — a thin command layer; the org's .md file stays the SSoT.

    User and channel ids come from ``ctx``: the adapter has already pulled them
    out of the raw message, because mention markup differs per platform.
    """
    m = _ORG_SUB_RE.match((arg or "").strip())
    if not m:
        return t("org_help")
    sub, rest = m.group(1).lower(), (m.group(2) or "").strip()
    name_of = ctx["name_of"]
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
        ids = list(ctx.get("user_ids") or [])
        ids += [tk for tk in toks[1:] if ctx["is_user_id"](tk)]
        ids = list(dict.fromkeys(ids))
        if not ids:
            return t("org_add_none", name=name)
        if sub == "add":
            n = sum(1 for i in ids
                    if orgs.add_member(name, i, name_of(i) or ""))
            return t("org_added", n=n, org=name) if n else t("org_nochange")
        n = sum(1 for i in ids if orgs.remove_member(name, i))
        return t("org_member_removed", org=name) if n else t("org_nochange")
    if sub in ("bind", "unbind"):
        cid = next((tk for tk in toks[1:] if ctx["is_channel_id"](tk)), None)
        if not cid:
            if ctx.get("channel") and not ctx.get("is_dm"):   # typed in the target
                cid = ctx["channel"]
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
