"""Entrypoint: ``python -m loki`` — validate core config, start the platform.

Pick the platform with ``LOKI_PLATFORM`` in .env (``slack`` by default) or on
the command line: ``python -m loki discord``. One process serves one platform;
to run two, run two processes — they share ``state/`` safely.
"""
from __future__ import annotations

import os
import sys

from loki.core import config

PLATFORMS = ("slack", "discord")


def main() -> None:
    name = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("LOKI_PLATFORM", "slack")).strip().lower()
    if name not in PLATFORMS:
        print(f"[loki] Unknown platform: {name} — expected one of "
              f"{', '.join(PLATFORMS)}", file=sys.stderr)
        sys.exit(2)
    config.validate_core()
    if name == "discord":
        from loki.platforms.discord import adapter
    else:
        from loki.platforms.slack import adapter
    adapter.run()


if __name__ == "__main__":
    main()
