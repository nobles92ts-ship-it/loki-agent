# Adding a Platform Adapter

`loki/core` is platform-agnostic. A platform (Telegram, Discord, Home Assistant, …) is one module implementing four hooks around it — Slack (`loki/platforms/slack/adapter.py`, ~280 lines) is the reference implementation.

## The contract (see `loki/platforms/base.py`)

| # | Hook | Responsibility |
|---|---|---|
| 1 | **normalize** | platform event → job dict `{channel, thread, text, user, event_id, in_thread, is_mention}`; strip mention markup; drop bot/self events |
| 2 | **authorize** | owner (allowlist) → configured permission mode · anyone else → **forced `plan`**, public surfaces only (never DM) · no caller id → reject |
| 3 | **submit** | gather context (thread / recent history), wrap with `loki.core.prompt.build_prompt` (injection guard), enqueue via `loki.core.jobs` — ack fast, platforms have delivery timeouts |
| 4 | **reply** | deliver the result, chunked to the platform's message limit, threaded where supported |

Cross-cutting rules every adapter must keep:

- **dedup** every event via `loki.core.dedup.already_seen` — platforms redeliver
- an owner-only **`!stop`** command → `loki.core.brain.stop_current()`
- **metadata-only logging** — never message bodies
- reuse `loki.core.config.t()` for user-facing strings (add keys for both `en`/`ko`)

## Skeleton (Telegram sketch)

```python
# loki/platforms/telegram/adapter.py
from ...core import brain, config, dedup, jobs
from ...core.prompt import build_prompt

def _handle(job):            # runs on the serial queue
    prompt = build_prompt(context="", question=job["text"])
    res = brain.run_claude(prompt, resume_id=None,
                           permission_mode=job["permission_mode"])
    _reply(job, res["text"])                      # chunk to 4096 chars

def _on_update(update):      # normalize + authorize + submit
    if update.from_bot or dedup.already_seen(str(update.update_id)):
        return
    is_owner = str(update.user_id) == config.require("TELEGRAM_OWNER_ID")
    if not is_owner:
        return                                    # or forced-plan group rules
    jobs.JOBS.put({...,"permission_mode": config.PERMISSION_MODE})

def run():
    jobs.start(_handle, _on_job_error)
    # long-poll / webhook loop …
```

Then register it in `loki/__main__.py` (v2 will read `LOKI_PLATFORMS` and start each enabled adapter).

## Roadmap order (difficulty-sorted)

1. **Telegram** — simplest bot API, long-polling (no public URL)
2. **Discord** — gateway websocket, similar shape
3. **Home Assistant** — webhook/conversation agent
4. **Signal** — via `signal-cli` (external dependency)
5. **WhatsApp** — Business API access is the barrier

PRs welcome — keep the security invariants (allowlist, guest read-only, injection guard, dedup) or they won't be merged.
