"""Entrypoint: ``python -m loki`` — validate core config, start the platform."""
from __future__ import annotations

from loki.core import config


def main() -> None:
    config.validate_core()
    # v1: Slack is the only platform. Future: read LOKI_PLATFORMS and start each.
    from loki.platforms.slack import adapter
    adapter.run()


if __name__ == "__main__":
    main()
