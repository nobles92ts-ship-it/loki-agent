#!/usr/bin/env python3
"""Loki connection diagnostics — run from the repo root:

    venv\\Scripts\\python.exe tools\\diag.py

Checks env settings, the claude CLI, and Slack credentials (auth.test only —
no Socket Mode connection, so a running Loki is unaffected). Exit 0 = all good.
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


def main() -> int:
    from loki.core import config          # loads .env, auto-detects claude

    check("WORK_DIR set", bool(config.WORK_DIR), config.WORK_DIR or "missing")
    if config.WORK_DIR:
        check("WORK_DIR exists", os.path.isdir(config.WORK_DIR), config.WORK_DIR)

    bot = os.environ.get("SLACK_BOT_TOKEN", "")
    app = os.environ.get("SLACK_APP_TOKEN", "")
    owner = os.environ.get("ALLOWED_USER_ID", "")
    check("SLACK_BOT_TOKEN format", bot.startswith("xoxb-"),
          (bot[:9] + "…") if bot else "missing")
    check("SLACK_APP_TOKEN format", app.startswith("xapp-"),
          (app[:9] + "…") if app else "missing")
    check("ALLOWED_USER_ID set", bool(owner), owner or "missing")

    from loki.core import brain
    ver = brain.claude_version()
    check("claude CLI reachable", ver not in ("", "?"), f"{config.CLAUDE_CMD} → {ver}")

    if bot.startswith("xoxb-"):
        try:
            from slack_sdk import WebClient
            r = WebClient(token=bot).auth_test()
            check("Slack auth.test", bool(r.get("ok")),
                  f"bot={r.get('user')} team={r.get('team')}")
        except Exception as e:                          # noqa: BLE001
            check("Slack auth.test", False, str(e))

    mode = config.PERMISSION_MODE
    print(f"[i ] permission mode: {mode}"
          + ("  (FULL write/execute — see docs/SECURITY.md)" if mode != "plan" else " (read-only)"))
    print(f"[i ] language: {config.LANG} · model: {config.MODEL or '(account default)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
