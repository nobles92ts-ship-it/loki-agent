#!/usr/bin/env python3
"""Loki connection diagnostics — run from the repo root:

    venv\\Scripts\\python.exe tools\\diag.py

Checks env settings, the claude CLI, and the configured platform's credentials
(an identity call only — no gateway connection, so a running Loki is
unaffected). Exit 0 = all good.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ok = True


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    mark = "[OK]" if passed else "[X ]"
    print(f"{mark} {label}" + (f" — {detail}" if detail else ""))
    if not passed:
        ok = False


def _check_slack() -> None:
    bot = os.environ.get("SLACK_BOT_TOKEN", "")
    app = os.environ.get("SLACK_APP_TOKEN", "")
    owner = os.environ.get("ALLOWED_USER_ID", "")
    check("SLACK_BOT_TOKEN format", bot.startswith("xoxb-"),
          (bot[:9] + "…") if bot else "missing")
    check("SLACK_APP_TOKEN format", app.startswith("xapp-"),
          (app[:9] + "…") if app else "missing")
    check("ALLOWED_USER_ID set", bool(owner), owner or "missing")
    if not bot.startswith("xoxb-"):
        return
    try:
        from slack_sdk import WebClient
        r = WebClient(token=bot).auth_test()
        check("Slack auth.test", bool(r.get("ok")),
              f"bot={r.get('user')} team={r.get('team')}")
    except Exception as e:                              # noqa: BLE001
        check("Slack auth.test", False, str(e))


def _check_discord() -> None:
    """Identity call over plain HTTP — no gateway, so a running Loki is
    untouched. The MESSAGE CONTENT intent can only be verified by connecting,
    so it stays a reminder here rather than a check."""
    import json
    import urllib.error
    import urllib.request

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    owner = os.environ.get("DISCORD_OWNER_ID", "")
    check("DISCORD_BOT_TOKEN set", bool(token.strip()),
          (token[:8] + "…") if token else "missing")
    check("DISCORD_OWNER_ID format", owner.isdigit() and 15 <= len(owner) <= 20,
          owner or "missing")
    try:
        import discord                                  # noqa: F401
        check("discord.py installed", True)
    except ImportError:
        check("discord.py installed", False,
              "pip install -r requirements.txt")
        return
    if not token.strip():
        return
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token.strip()}",
                 "User-Agent": "loki-diag/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            me = json.loads(r.read())
        check("Discord identity", True,
              f"bot={me.get('username')}#{me.get('discriminator')}")
    except urllib.error.HTTPError as e:
        check("Discord identity", False,
              "401 — bad or reset token" if e.code == 401 else f"HTTP {e.code}")
    except Exception as e:                              # noqa: BLE001
        check("Discord identity", False, str(e))
    print("[i ] reminder: Bot → Privileged Gateway Intents → MESSAGE CONTENT "
          "must be ON, or Loki sees empty messages")


def main() -> int:
    from loki.core import config          # loads .env, auto-detects claude

    check("WORK_DIR set", bool(config.WORK_DIR), config.WORK_DIR or "missing")
    if config.WORK_DIR:
        check("WORK_DIR exists", os.path.isdir(config.WORK_DIR), config.WORK_DIR)

    platform = (os.environ.get("LOKI_PLATFORM", "slack") or "slack").lower()
    print(f"[i ] platform: {platform}")

    from loki.core import brain
    ver = brain.claude_version()
    check("claude CLI reachable", ver not in ("", "?"), f"{config.CLAUDE_CMD} → {ver}")

    if platform == "discord":
        _check_discord()
    else:
        _check_slack()

    mode = config.PERMISSION_MODE
    print(f"[i ] permission mode: {mode}"
          + ("  (FULL write/execute — see docs/SECURITY.md)" if mode != "plan" else " (read-only)"))
    print(f"[i ] language: {config.LANG} · model: {config.MODEL or '(account default)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
