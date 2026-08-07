"""The account pin, with a switch — which Claude account a spawn runs as.

``CLAUDE_CODE_OAUTH_TOKEN`` in ``.env`` fixes the account for every spawn
(v1.8). That is the right default for a worker with its own subscription, but
it made the choice a restart-only one: the moment you have **two** accounts —
a personal one and the company's — deciding which is spending on this run means
editing ``.env`` and bouncing the worker.

So the token stays the configuration and this holds the *state*: the owner
flips it from chat with ``!account off`` / ``!account on``, and the next request
spawns under the other account. The token is never written here — only the
choice of whether to use it — so turning the pin off cannot lose it.

Off means the spawn falls back to whatever login the config dir holds, which is
the pre-v1.8 behaviour. Default is on, so an install that upgrades into this
version keeps running exactly as it did.

Sessions do not survive the switch. A resumed conversation replays under
whoever is authenticated now, and carrying one account's thread into the
other's context (and quota) is not something to do silently — so a flip clears
them and the next message starts fresh.
"""
from __future__ import annotations

import json
import threading

from . import config
from .config import log

STATE_FILE = config.STATE / "account.json"

_lock = threading.Lock()


def configured() -> bool:
    """Is there a token to switch at all?"""
    return bool(config.CLAUDE_OAUTH_TOKEN)


def _read() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_on() -> bool:
    """Should the next spawn use the pinned account?

    Default on: no state file means an install that never touched the switch,
    which must behave the way its ``.env`` says.
    """
    with _lock:
        return bool(_read().get("pinned", True))


def set_on(on: bool) -> bool:
    """Flip the pin. Returns False when it was already in that state."""
    on = bool(on)
    with _lock:
        if bool(_read().get("pinned", True)) == on:
            return False
        try:
            STATE_FILE.write_text(json.dumps({"pinned": on}), encoding="utf-8")
        except Exception:
            log.exception("account.json write failed")
            return False
    log.info("account pin %s", "on" if on else "off")
    return True


def token() -> str:
    """The token to spawn with — empty when unset or switched off.

    The single place that decides. Everything that spawns `claude` reads this
    instead of ``config.CLAUDE_OAUTH_TOKEN``, so the switch cannot be honoured
    on one path and missed on another.
    """
    return config.CLAUDE_OAUTH_TOKEN if is_on() else ""


def fingerprint() -> str:
    """Enough of the token to tell two of them apart, and no more.

    Never the token: this goes to a chat reply, and a Slack workspace is not a
    secret store.
    """
    tok = config.CLAUDE_OAUTH_TOKEN
    return f"…{tok[-6:]}" if len(tok) >= 6 else "(set)" if tok else "-"


def status() -> dict:
    return {"configured": configured(), "on": is_on(),
            "fingerprint": fingerprint(),
            "config_dir": config.CLAUDE_CONFIG_DIR or "~/.claude"}
