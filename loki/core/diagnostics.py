"""Diagnostics behind `python -m loki doctor` and `status`.

Answers two different questions, deliberately kept apart:

- **doctor** — *is this install correct?* config, credentials, the claude CLI.
  Safe to run any time; it makes one identity call (plus one tiny `claude`
  call when an account-pin token is set), never a gateway connection, so a
  running worker is unaffected.
- **status** — *is the worker up right now?* purely the heartbeat
  (:mod:`loki.core.health`), no network at all.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from . import config, health

_ok = True


def _check(label: str, passed: bool, detail: str = "") -> None:
    global _ok
    print(("[OK]" if passed else "[X ]") + f" {label}" + (f" — {detail}" if detail else ""))
    if not passed:
        _ok = False


def _info(label: str) -> None:
    print(f"[i ] {label}")


def _fmt_dur(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


# ─────────────────────────── status ───────────────────────────
def status() -> int:
    """Liveness only. Exit 0 = up, 1 = not."""
    s = health.snapshot()
    if not s["known"]:
        print("[X ] worker has never run on this machine "
              "(no state/health.json)")
        return 1
    if s["alive"]:
        print(f"[OK] worker up — {s['platform']} · pid {s['pid']} · "
              f"uptime {_fmt_dur(s['uptime_sec'])} · "
              f"{s['jobs']} job(s) · last beat {_fmt_dur(s['idle_sec'])} ago")
        return 0
    why = {"process_gone": f"process {s['pid']} is gone",
           "stale_heartbeat": f"no heartbeat for {_fmt_dur(s['idle_sec'])}"}
    print(f"[X ] worker DOWN — {why.get(s['reason'], s['reason'])}")
    print("     start it:  python -m loki            (or `gateway ensure`)")
    return 1


def _probe_account_pin() -> tuple[bool, str]:
    """Does CLAUDE_CODE_OAUTH_TOKEN actually authenticate?

    Probed against an EMPTY config dir on purpose. With a stored login present,
    `claude` answers normally even when the token is invalid — it falls back to
    that login without erroring — so probing the real config dir would pass no
    matter what, and the pin would look healthy while Loki quietly ran as
    whoever the machine happens to be logged in as. An empty dir removes the
    fallback, so the token has to stand on its own. Tokens expire after a year,
    which is exactly when this silent fallback would otherwise kick in.
    """
    tok = config.CLAUDE_OAUTH_TOKEN
    if not tok.startswith("sk-ant-oat"):
        return False, "not a setup-token — paste the token, not the browser's code"
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE_CODE") or k == "CLAUDECODE"
                   or k.startswith("ANTHROPIC_") or k == "CLAUDE_CONFIG_DIR")}
    env["PYTHONUTF8"] = "1"
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    try:
        with tempfile.TemporaryDirectory(prefix="loki-authprobe-") as empty:
            env["CLAUDE_CONFIG_DIR"] = empty
            r = subprocess.run([config.CLAUDE_CMD, "-p", "Reply with exactly: PONG"],
                               env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120,
                               creationflags=config.NO_WINDOW)
    except Exception as e:                     # missing exe, timeout, temp dir
        return False, f"probe failed: {type(e).__name__}"
    if r.returncode == 0:
        return True, "authenticates on its own — no silent fallback"
    tail = [l for l in ((r.stdout or "") + " " + (r.stderr or "")).splitlines() if l.strip()]
    return False, (tail[-1].strip()[:90] if tail else f"rc={r.returncode}")


# ─────────────────────────── doctor ───────────────────────────
def doctor() -> int:
    """Full install check + liveness. Exit 0 = all good."""
    global _ok
    _ok = True

    _check("WORK_DIR set", bool(config.WORK_DIR), config.WORK_DIR or "missing")
    if config.WORK_DIR:
        _check("WORK_DIR exists", os.path.isdir(config.WORK_DIR), config.WORK_DIR)

    platform = (os.environ.get("LOKI_PLATFORM", "slack") or "slack").lower()
    _info(f"platform: {platform}")

    from . import brain
    ver = brain.claude_version()
    _check("claude CLI reachable", ver not in ("", "?"),
           f"{config.CLAUDE_CMD} → {ver}")

    if config.CLAUDE_OAUTH_TOKEN:
        from . import account
        if account.is_on():
            _check("account pin (CLAUDE_CODE_OAUTH_TOKEN)", *_probe_account_pin())
        else:
            # Configured but switched off from chat. Probing it would report a
            # healthy pin for an account nothing is running as.
            _info(f"account pin: configured ({account.fingerprint()}) but "
                  f"switched OFF — spawns use the login in "
                  f"{account.status()['config_dir']}. `!account on` to restore.")
    elif config.CLAUDE_CONFIG_DIR:
        _info(f"account: config dir {config.CLAUDE_CONFIG_DIR} "
              "(follows that dir's login — see docs/SETUP.md to pin it)")

    _check_discord() if platform == "discord" else _check_slack()

    mode = config.PERMISSION_MODE
    _info(f"permission mode: {mode}" +
          ("  (FULL write/execute — see docs/SECURITY.md)" if mode != "plan"
           else " (read-only)"))
    _info(f"language: {config.LANG} · model: {config.MODEL or '(account default)'}")
    _info(f"session memory: " +
          (f"{config.SESSION_IDLE_MIN} min idle" if config.SESSION_IDLE_MIN
           else "never expires"))

    print("")
    live = status()
    return 0 if (_ok and live == 0) else 1


def _check_slack() -> None:
    bot = os.environ.get("SLACK_BOT_TOKEN", "")
    app = os.environ.get("SLACK_APP_TOKEN", "")
    owner = os.environ.get("ALLOWED_USER_ID", "")
    _check("SLACK_BOT_TOKEN format", bot.startswith("xoxb-"),
           (bot[:9] + "…") if bot else "missing")
    _check("SLACK_APP_TOKEN format", app.startswith("xapp-"),
           (app[:9] + "…") if app else "missing")
    _check("ALLOWED_USER_ID set", bool(owner), owner or "missing")
    if not bot.startswith("xoxb-"):
        return
    try:
        from slack_sdk import WebClient
        r = WebClient(token=bot).auth_test()
        _check("Slack auth.test", bool(r.get("ok")),
               f"bot={r.get('user')} team={r.get('team')}")
    except Exception as e:                              # noqa: BLE001
        _check("Slack auth.test", False, str(e))


def _check_discord() -> None:
    """Identity call over plain HTTP — no gateway, so a running Loki is
    untouched. The MESSAGE CONTENT intent can only be verified by connecting,
    so it stays a reminder here rather than a check."""
    import urllib.error
    import urllib.request

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    owner = os.environ.get("DISCORD_OWNER_ID", "")
    _check("DISCORD_BOT_TOKEN set", bool(token.strip()),
           (token[:8] + "…") if token else "missing")
    _check("DISCORD_OWNER_ID format",
           owner.isdigit() and 15 <= len(owner) <= 20, owner or "missing")
    try:
        import discord                                  # noqa: F401
        _check("discord.py installed", True)
    except ImportError:
        _check("discord.py installed", False, "pip install -r requirements.txt")
        return
    if not token.strip():
        return
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token.strip()}",
                 "User-Agent": "loki-doctor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            me = json.loads(r.read())
        _check("Discord identity", True, f"bot={me.get('username')}")
    except urllib.error.HTTPError as e:
        _check("Discord identity", False,
               "401 — bad or reset token" if e.code == 401 else f"HTTP {e.code}")
    except Exception as e:                              # noqa: BLE001
        _check("Discord identity", False, str(e))
    _info("reminder: Bot → Privileged Gateway Intents → MESSAGE CONTENT must "
          "be ON, or Loki sees empty messages")
