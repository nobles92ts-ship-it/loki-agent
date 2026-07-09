"""Platform adapter contract.

Every platform (Slack today; Telegram, Discord, Home Assistant, Signal,
WhatsApp tomorrow) is a thin adapter around ``loki.core`` implementing the
same four hooks:

1. **normalize** — turn a platform event into a job dict:
   ``{channel, thread, text, user, event_id, in_thread, is_mention}``
   (strip platform artifacts like ``<@mention>`` markup; drop bot/self events)

2. **authorize** — decide, per caller, the permission mode or reject:
   - owner (allowlist) → the configured mode (``plan`` or ``bypassPermissions``)
   - anyone else → **forced read-only ``plan``**, and only in public
     surfaces (never DM). No caller id → reject.

3. **submit** — gather conversation context (thread / recent channel
   history), wrap it with the injection guard (``loki.core.prompt``), and
   enqueue on the serial queue (``loki.core.jobs``). Ack fast — the
   platform's delivery timeout is usually a few seconds.

4. **reply** — deliver the result back, chunked to the platform's message
   size limit, threading the conversation where the platform supports it.

Cross-cutting rules every adapter must keep:
- dedup every event via ``loki.core.dedup.already_seen`` (platforms redeliver)
- a ``!stop`` command for the owner only
- never log message bodies (metadata only)

See ``docs/PLATFORMS.md`` for a worked example.
"""
from __future__ import annotations


class Adapter:
    """Interface sketch (duck-typed — adapters are modules, not subclasses)."""

    name: str = "base"

    def run(self) -> None:                       # blocks forever
        raise NotImplementedError
