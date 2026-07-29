# Writing a Plugin

The `!` commands Loki ships with live in `loki/core/commands.py`. The ones
*your* team needs live in `plugins/` — one file per command, discovered at
boot, never committed to this repo.

```python
# plugins/deploy.py
MATCH = r"^!deploy\s+(\S+)$"
HELP  = "!deploy <service> — kick off a deploy"

def handle(match, ctx):
    return f"deploying {match.group(1)}…"
```

Drop that in, restart, and `!deploy api` works. `!plugins` lists what's loaded.

## The contract

| Name | Required | Meaning |
|---|---|---|
| `MATCH` | ✅ | Regex that triggers the command. Matched case-insensitively against the message with mention markup already stripped. |
| `handle(match, ctx)` | ✅ | Returns the text to post, or `None` to decline — the message then falls through to a normal chat request. |
| `HELP` | | One line shown by `!plugins`. |
| `NAME` | | Name used by `!plugins` and `!org allow`. Defaults to the filename. |
| `OWNER_ONLY` | | Defaults to **`True`**. See below before changing it. |

`ctx` is the same dict `core.commands.handle` documents: `channel`, `thread`,
`session_key`, `is_dm`, `is_owner`, `org`, `name_of`, `user_ids`,
`is_user_id`, `is_channel_id`.

Files starting with `_` are treated as shared helpers and never loaded as
commands, so `_shared.py` next to your plugins is fine.

## Permissions

**Owner-only by default, deliberately.** A file appearing in a folder must
never silently hand guests a new way to reach the machine.

Opening one up takes two steps, not one:

1. `OWNER_ONLY = False` in the plugin
2. `!org allow <org> <name>` for each org that may run it

Both are required. `OWNER_ONLY = False` on its own opens the command to
*nobody* — an unaffiliated guest still can't run it. That's the point: the
plugin author declares "this could be shared", and the owner decides who
actually gets it.

Plugins are matched **after** the built-in commands, so no plugin can shadow
`!stop`, `!org`, or anything else Loki depends on.

## Failure is contained

- A plugin that fails to import is logged and skipped — the others still load,
  and the worker still starts.
- A plugin that raises at runtime reports the error to the caller instead of
  taking down the request.
- A plugin missing `MATCH` or `handle` is skipped with a warning.

You'll see all three in `state/worker.log`.

## When *not* to use a plugin

A plugin returns one string. That makes it the wrong shape for long work:
a pipeline that runs for an hour, streams progress into a thread, and needs its
own threading and cancellation.

For that, use the platform's `private_commands.py` hook (see
`loki/platforms/slack/private_commands.example.py`). It runs before the
router, owns its own execution, and can post to the conversation whenever it
likes. The two coexist — use the small one until it stops fitting.

## Distribution

`plugins/*.py` is gitignored, so your commands stay yours when you pull
updates from upstream. Files ending `.py.example` are tracked, which is how
the shipped example gets to you — name yours the same way if you want to share
one without enabling it.
