"""Interactive terminal console — ``python -m loki chat``.

Loki normally carries a chat platform's messages to the ``claude`` on this
machine. This is that same path with the transport removed: stdin in, Claude's
answer out, the ``!`` vocabulary in between. It exists because trying a plugin,
an alias or a budget change used to mean posting into Slack and waiting for the
round trip.

The caller is always the owner. Guests are a permission model for a *remote*
audience — whoever is typing here already has a shell on the machine Loki would
act on, so there is nothing left to withhold from them.

The console keeps its own session key, so a terminal conversation never resumes
— and so never derails — the one running in your DM.

Turns run inline rather than through the job queue: a terminal is synchronous
already, and Ctrl-C is the cancel button people reach for. ``!jobs`` and
``!cancel`` still see the worker's jobs; they just never see this one.
"""
from __future__ import annotations

import sys
import time

from . import brain, commands, config, goals, providers, sessions, usage
from .config import log

SESSION_KEY = "cli:local"
QUIT = ("exit", "quit", ":q")

# `!schedule` records the channel to deliver into, and "cli" is not one the
# worker can post to — a schedule made here would fire into nothing.
SCHEDULE_PREFIXES = ("!schedule", "!예약")

_TXT = {
    "en": {
        "banner": "Loki console · {brain} · {mode} mode · {cwd}",
        "hint": "! commands work here. 'exit' or Ctrl-D to leave.",
        "bye": "bye.",
        "stopped": "⛔ stopped — the claude process was killed.",
        "no_schedule": "!schedule needs a chat channel to deliver into. "
                       "Create it from your DM instead.",
    },
    "ko": {
        "banner": "Loki 콘솔 · {brain} · {mode} 모드 · {cwd}",
        "hint": "! 명령을 그대로 쓸 수 있습니다. 나가기는 'exit' 또는 Ctrl-D.",
        "bye": "종료합니다.",
        "stopped": "⛔ 중단됨 — claude 프로세스를 종료했습니다.",
        "no_schedule": "!schedule 은 결과를 보낼 대화 채널이 필요합니다. "
                       "DM에서 만들어 주세요.",
    },
}


def _t(key: str, **kw) -> str:
    table = _TXT.get(config.LANG) or _TXT["en"]
    return table[key].format(**kw)


class _Handle(dict):
    """A job dict that keeps the process handle after the call clears it.

    :func:`brain.run_claude` attaches the live ``proc`` and drops it again in a
    ``finally``, so by the time a Ctrl-C propagates out there is no pid left to
    kill. This remembers it separately.
    """

    proc = None

    def __setitem__(self, key, value):
        if key == "proc":
            self.proc = value
        super().__setitem__(key, value)


def _ctx() -> dict:
    """The context :func:`commands.handle` documents, filled in for a terminal.

    A console has no mention markup and no ids, so the id predicates answer no
    and names pass through unchanged.
    """
    return {
        "channel": "cli",
        "thread": None,
        "session_key": SESSION_KEY,
        "is_dm": True,          # a terminal is at least as private as a DM
        "is_owner": True,
        "name_of": lambda uid: uid,
        "user_ids": [],
        "is_user_id": lambda tok: False,
        "is_channel_id": lambda tok: False,
        "chan_ref": lambda c: c,
    }


def _ask(line: str) -> None:
    """One turn against the current provider, printed as it comes back.

    The conversation's standing goals ride in front of the text, the same way
    they do on every chat platform — a goal opened here is meant to shape the
    next twenty turns, not just the one that created it.
    """
    job = _Handle()
    started = time.time()
    prompt = goals.context(SESSION_KEY) + line
    try:
        result = brain.run_claude(prompt, sessions.get(SESSION_KEY), job=job)
    except KeyboardInterrupt:
        if job.proc is not None:
            brain.tree_kill(job.proc.pid)
        print(_t("stopped"))
        return

    took = time.time() - started
    sessions.remember(SESSION_KEY, result.get("session_id"))
    usage.record("cli", "cli", not result.get("error"), took,
                 result.get("reason", "ok"))
    print(f"\n{result['text']}\n  ({took:.1f}s · {result.get('provider', '?')})")


def run() -> int:
    config.validate_core()

    # A Windows console defaults to the ANSI code page, which cannot print
    # Claude's output (or Korean) without mangling it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print(_t("banner", brain=providers.current().LABEL,
             mode=config.PERMISSION_MODE, cwd=config.WORK_DIR))
    print(_t("hint"))

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{_t('bye')}")
            return 0

        if not line:
            continue
        if line.lower() in QUIT:
            print(_t("bye"))
            return 0

        if line.startswith("!"):
            if line.lower().startswith(SCHEDULE_PREFIXES):
                print(_t("no_schedule"))
                continue
            try:
                reply = commands.handle(line, _ctx())
            except Exception as e:            # a bad plugin must not end the session
                log.exception("console command failed")
                print(f"⚠️ {e}")
                continue
            # None means no command matched — fall through and ask Claude,
            # exactly as a chat adapter would.
            if reply is not None:
                print(reply)
                continue

        _ask(line)
