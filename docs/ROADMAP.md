# Roadmap

What's planned, why, and what it would take. Items are ordered by value, not by
difficulty. If you want one of these, say so in an [issue](../../issues) — or
build it and open a PR; the ones below are all shaped to be contributable.

The shipped history lives in [CHANGELOG.md](../CHANGELOG.md); the README's
roadmap table is the short version of this page.

## Guiding constraint

Loki's brain is Claude Code. Anything Claude Code already does well — memory,
context compaction, MCP, sub-agent delegation, computer use — Loki should
*not* reimplement. Wrapping those adds a layer that can only get in the way.

The gaps worth filling are the ones Claude Code can't see: **the bridge, the
operations around it, and the door other people extend it through.**

---

## Next up

### 1. Process supervision — `doctor`, heartbeat, restart-on-crash

**Problem.** If the worker dies, nothing says so. You find out when someone
messages the bot and gets silence. For a team that routes real work through
Loki, downtime is invisible until it's already cost someone an hour.

**Shape.**

- the worker stamps `state/heartbeat.json` on a timer
- `python -m loki doctor` reads it and answers *alive* / *silent for N minutes*,
  alongside the config checks `tools/diag.py` already does
- lean on the OS for the restart itself — systemd `Restart=on-failure`,
  launchd `KeepAlive`, a Windows Scheduled Task. The docs already show these;
  this promotes them from an example to a supported path.

**Why not a watchdog process.** A second process that watches the first is one
more thing that can die quietly. The OS already solved this.

### 2. Plugin directory — one file per command

**Problem.** Custom commands go in a single gitignored `private_commands.py`
with one `try_handle` entry point. That works for one command and gets crowded
fast — and it's the exact seam other companies need, since a fork that adds
commands has to keep merging that one file.

**Shape.** A `plugins/` directory where one file = one command, discovered at
boot, using the **same context dict** `core/commands.py` already documents:

```python
# plugins/deploy.py
MATCH = r"^!deploy\s+(\S+)$"

def handle(match, ctx):
    return f"deploying {match.group(1)}…"
```

Keep `private_commands.py` working — it's live in real installs.

**Open question.** Whether plugins may run at owner permission by default, or
must opt in per plugin. Leaning toward opt-in: a dropped-in file shouldn't
silently inherit write access to the machine.

### 3. Model fallback chain

**Problem.** The binding limit on a subscription-powered bot is the rolling
usage window. Today hitting it ends the request with "🚦 limit reached".

**Shape.** `CLAUDE_MODEL_FALLBACK=sonnet,haiku` in `.env`. `brain.run_claude`
already classifies `reason == "quota"`; on that, retry down the chain and tell
the user which model actually answered. **Degrade instead of dying.**

Cheap to build, and it's the difference between a bot that stops working at
3pm and one that gets a bit less sharp.

### 4. Per-user sessions in shared channels

**Problem.** A DM or thread keeps its Claude session so follow-ups continue. A
channel's top level deliberately doesn't — several people share it, and one
person's context must not surface in another person's answer. The cost is that
channel conversations never continue; every message starts over.

**Shape.** Key the session on `(channel, user)` instead of dropping it:
`sessions.key_for()` grows a `user` argument and returns `chan:<channel>:<user>`.
Each person gets continuity, nobody sees anyone else's context. The job queue
already serialises on the session key, so concurrency comes along for free, and
idle expiry keeps the table from growing without bound.

This one is close to free — it's a change to a policy function with tests
already around it.

### 5. Token-level usage

`!usage` reports calls, successes and wall time. The thing that actually runs
out is tokens, and that isn't visible anywhere. Claude Code's headless output
carries usage data, so this is mostly plumbing it into `core/usage.py` and
widening the `!usage` report.

---

## Platforms

`loki/core` is platform-agnostic and two adapters exist, which is enough to
prove the contract holds. See [PLATFORMS.md](PLATFORMS.md) for what writing a
third involves — the `!` command vocabulary, session policy, scope, orgs and
rate limiting all come for free.

| | Notes |
|---|---|
| **Telegram** | simplest bot API, long polling, no public URL. The natural next one. |
| **Home Assistant** | webhook / conversation agent |
| **Signal** | via `signal-cli` — an external dependency to install and keep alive |
| **WhatsApp** | Business API access is the real barrier, not the code |

---

## Considered and deliberately not planned

Recording these so the same ground isn't re-covered.

| | Why not |
|---|---|
| **Inbound webhooks** (external event → Loki) | Genuinely useful — CI fails, Loki investigates and reports. But it needs an inbound port, which breaks the "no public URL, works behind any NAT" property that makes Loki easy to run. Revisit only with a design that keeps the connection outbound. |
| **Loki-side memory layer** | Claude Code already has one. A second memory that disagrees with the first is worse than none. `!learn` stays a plain inbox on purpose. |
| **Context compression** | Claude Code compacts its own context. |
| **MCP / sub-agents / computer use** | Inherited from the brain. Loki wrapping them would only restrict them. |
| **Per-platform tool restrictions** | Loki gates on *who is asking* (owner / org member / guest), not which app they typed in. People are the trust boundary here, not platforms. |
