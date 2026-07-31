"""Entrypoint: ``python -m loki [command]``.

    python -m loki                 run the worker (platform from LOKI_PLATFORM)
    python -m loki slack|discord|telegram
                                   run the worker on that platform
    python -m loki status          is the worker up right now?
    python -m loki doctor          full install check + liveness
    python -m loki gateway …       install | uninstall | ensure | stop | restart

One process serves one platform; run two processes to serve both — they share
``state/`` safely.
"""
from __future__ import annotations

import os
import sys

from loki.core import config

PLATFORMS = ("slack", "discord", "telegram")
GATEWAY_SUBS = ("install", "uninstall", "ensure", "stop", "restart", "status")

USAGE = f"""usage: python -m loki [command]

  (none) | {' | '.join(PLATFORMS):<18}run the worker
  {'status':<27}is the worker up right now?
  {'doctor':<27}full install check + liveness
  {'gateway <sub>':<27}{' | '.join(GATEWAY_SUBS)}
"""


def _run_worker(name: str) -> int:
    if name not in PLATFORMS:
        print(f"[loki] Unknown platform: {name} — expected one of "
              f"{', '.join(PLATFORMS)}", file=sys.stderr)
        return 2
    config.validate_core()
    if name == "discord":
        from loki.platforms.discord import adapter
    elif name == "telegram":
        from loki.platforms.telegram import adapter
    else:
        from loki.platforms.slack import adapter
    adapter.run()
    return 0


def _gateway(argv: list[str]) -> int:
    from loki.core import diagnostics, gateway
    sub = (argv[0] if argv else "").lower()
    if sub == "status":
        return diagnostics.status()
    if sub not in GATEWAY_SUBS:
        print(f"usage: python -m loki gateway "
              f"<{' | '.join(GATEWAY_SUBS)}>", file=sys.stderr)
        return 2
    return getattr(gateway, sub)()


def main() -> None:
    argv = sys.argv[1:]
    cmd = (argv[0].lower() if argv
           else os.environ.get("LOKI_PLATFORM", "slack").strip().lower())

    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        sys.exit(0)
    if cmd == "status":
        from loki.core import diagnostics
        sys.exit(diagnostics.status())
    if cmd == "doctor":
        from loki.core import diagnostics
        sys.exit(diagnostics.doctor())
    if cmd == "gateway":
        sys.exit(_gateway(argv[1:]))
    sys.exit(_run_worker(cmd))


if __name__ == "__main__":
    main()
