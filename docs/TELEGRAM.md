# Loki on Telegram

Same Loki, same `claude` CLI on your own machine, reached from Telegram instead
of Slack. No workspace to create, no app manifest — two values in `.env` and
you're talking to your PC.

## Setup (≈2 min)

**1. Create the bot.** Message [@BotFather](https://t.me/BotFather) → `/newbot`
→ pick a name and a username. It replies with a token like `123456:AA…`.

**2. Find your numeric id.** Message [@userinfobot](https://t.me/userinfobot);
it replies with your id. This is the allowlist — the one account that gets the
private chat and write power.

**3. Configure.**

```ini
LOKI_PLATFORM=telegram
TELEGRAM_BOT_TOKEN=123456:AA...
TELEGRAM_OWNER_ID=000000000
WORK_DIR=/home/you/projects
```

**4. Run.** `./venv/bin/python -m loki` (or `.\venv\Scripts\python.exe -m loki`).
Then message your bot `hello`.

Everything else — `WORK_DIR`, `CLAUDE_PERMISSION_MODE`, `GUEST_RATE_PER_HOUR`,
`CLAUDE_CONFIG_DIR`, `LOKI_LANG` — works exactly as it does on Slack.

## How the trust model maps

Telegram has no workspace, so there's no "anyone in the org" tier. The three
surfaces are:

| Surface | Who | What they get |
|---|---|---|
| **Private chat** | `TELEGRAM_OWNER_ID` only | full configured mode — read, write, run, all owner commands |
| Private chat | anyone else | silently ignored |
| **Group** | anyone who @mentions the bot or replies to it | read-only, `loki.md` scope, rate-limited, budget-capped |

Unaddressed group chatter is never processed unless you turn the group into an
auto-listen zone with `!listen`. Bots are ignored outright.

**Turn off group privacy only if you mean it.** BotFather's default (privacy
mode *on*) means the bot only sees messages that mention it — which is exactly
the boundary Loki wants. Leave it on.

## What's different from Slack

- **One conversation per chat.** Telegram has no threads, so a private chat is
  a single continuous Claude session — `!new` (`!새대화`) starts a fresh one,
  and sessions expire on their own after `SESSION_IDLE_MIN`. In supergroups
  with forum topics, each topic is its own conversation.
- **Group context is what the bot saw.** Bots can't read history they weren't
  present for, so context is the recent messages Loki observed while running,
  held in memory. A restart starts that over.
- **No clickable checklists or budget buttons.** Those are Block Kit. `!check`
  is Slack-only; budget mitigations use the text commands
  (`!budget sonnet` / `pause` / `resume`).
- **No `!bot` allowlist.** That's built on Slack bot ids; Telegram ignores
  every bot author outright, as Discord does.
- **Formatting is HTML.** If a reply ever contains markup Telegram rejects,
  Loki resends it as plain text rather than dropping the answer.

## Everything that is the same

`!schedule`, `!alias`, `!budget`, `!org`, `!usage`, `!jobs`, `!cancel`,
`!learn`, `!listen`, `!block`, `!send`, `!new`, `!plugins`, `!stop` — all of
it, because those live in `loki/core/commands.py` and every adapter calls
the same router. File input
uses the same allowlist, `!send` the same WORK_DIR fence, guests the same
`loki.md` scope, and the read-only boot self-test runs identically.

## One platform per process

Every adapter runs the scheduler and shares `state/`, so two against the same
install would fire every schedule twice. Run one per process with its own
state directory:

```bash
python -m loki telegram        # or set LOKI_PLATFORM=telegram
```
