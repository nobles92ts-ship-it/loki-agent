# Adding a Platform Adapter

`loki/core` is platform-agnostic. A platform is one module implementing four hooks around it. Two ship today — Slack (`loki/platforms/slack/adapter.py`) and Discord (`loki/platforms/discord/adapter.py`) — and reading both side by side is the fastest way to see which parts are yours and which come free.

## The contract (see `loki/platforms/base.py`)

| # | Hook | Responsibility |
|---|---|---|
| 1 | **normalize** | platform event → job dict `{channel, thread, text, user, event_id, in_thread, is_mention, session_key}`; strip mention markup; drop bot/self events |
| 2 | **authorize** | owner (allowlist) → configured permission mode · anyone else → **forced `plan`**, public surfaces only (never DM) · no caller id → reject |
| 3 | **submit** | gather context (thread / recent history), wrap with `loki.core.prompt.build_prompt` (injection guard), enqueue via `loki.core.jobs` — ack fast, platforms have delivery timeouts |
| 4 | **reply** | deliver the result, chunked to the platform's message limit, threaded where supported |

Cross-cutting rules every adapter must keep:

- **dedup** every event via `loki.core.dedup.already_seen` — platforms redeliver
- **metadata-only logging** — never message bodies
- reuse `loki.core.config.t()` for user-facing strings (add keys for both `en`/`ko`)

## What you get for free

Don't reimplement these — an adapter that does will drift from the others.

**`loki.core.commands.handle(text, ctx)`** is the whole `!` vocabulary: `!jobs`, `!cancel`, `!stop`, `!usage`, `!schedule`, `!learn`, `!new`, `!listen`, `!block`, `!org`. It returns the text to post, or `None` when the message isn't a command. It is owner-gated inside, so you cannot forget the check. You supply a small context dict for what only you know:

```python
reply = commands.handle(text, {
    "channel": channel_id, "thread": thread_id_or_none,
    "session_key": sessions.key_for(channel_id, thread_id, is_dm=…),
    "is_dm": …, "is_owner": …,
    "name_of": lambda uid: …,        # user id → display name
    "user_ids": [...],               # ids mentioned in the raw message
    "is_user_id": lambda tok: …,     # does this bare token look like an id?
    "is_channel_id": lambda tok: …,
})
if reply is not None:
    post(reply); return
```

**`loki.core.sessions`** decides what continues a conversation. Call `key_for(channel, thread, is_dm)` and put the result on the job as `session_key`; the queue serialises by it, so two messages never resume the same Claude session at once. Threads and DMs get rolling sessions; a channel's top level deliberately gets none, because several people share it.

**`loki.core.scope` / `orgs` / `ratelimit` / `guard`** are the security layers. Wire all four or your adapter is weaker than the ones beside it: guests get `scope.write_scope_settings(org)` plus `cwd=scope.loki_dir()`, non-owner-DM runs get `guard.snapshot()`/`guard.restore()` around them.

Keep core free of your platform's id shapes. `orgs` once hardcoded Slack's `U…`/`C…` patterns and silently rejected every Discord member id — that's why ids are validated generically in core and precisely in the adapter.

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

Then add it to `PLATFORMS` in `loki/__main__.py`. One process serves one platform; run two processes to serve two (they share `state/` safely).

## Async platforms

`loki.core.jobs` runs Claude on worker threads. If your library owns an asyncio loop (as discord.py does), capture it on connect and bridge every call back:

```python
_loop = asyncio.get_running_loop()          # in your on_ready

def _call(coro, timeout=30.0):              # from a worker thread
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)
```

Never call that bridge from the loop itself — scheduling onto the loop and then blocking on it from the loop deadlocks. Keep an `async def` version for handlers and a thin sync wrapper for workers, like `_alert_owner` / `alert_owner` in the Discord adapter.

## Roadmap order (difficulty-sorted)

1. ~~**Discord** — gateway websocket~~ ✅ shipped in v1.6.3
2. **Telegram** — simplest bot API, long-polling (no public URL)
3. **Home Assistant** — webhook/conversation agent
4. **Signal** — via `signal-cli` (external dependency)
5. **WhatsApp** — Business API access is the barrier

PRs welcome — keep the security invariants (allowlist, guest read-only, injection guard, dedup) or they won't be merged.
