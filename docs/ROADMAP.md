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

## Shipped since this page was written

- **Process supervision** — `python -m loki status` / `doctor` / `gateway …`,
  a heartbeat in `state/health.json`, and OS-level restart-on-crash. (v1.6.4)
- **Plugin directory** — `plugins/`, one file per command, owner-only by
  default. See [PLUGINS.md](PLUGINS.md). (v1.6.4)

## Next up

### 1. Per-user sessions in shared channels

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

### 2. Token-level usage

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
| **Model fallback on quota** (opus → sonnet → haiku) | A subscription's rolling usage window is **one shared pool**, not a per-model budget — when it's gone, the smaller model is gone too. And a bot configured with `CLAUDE_MODEL=sonnet`, which is the sensible setting for exactly this reason, has nowhere left to fall. The idea only pays off on a per-model cap, which isn't the limit most installs hit. |
| **Inbound webhooks** (external event → Loki) | Genuinely useful — CI fails, Loki investigates and reports. Two reasons not to: it needs an inbound port, which breaks the "no public URL, works behind any NAT" property that makes Loki easy to run; and `!schedule` already reaches the same outcome outbound ("every morning check CI and report"), since the brain can call APIs itself. Revisit only if something needs sub-minute reaction time. |
| **Loki-side memory layer** | Claude Code already has one. A second memory that disagrees with the first is worse than none. `!learn` stays a plain inbox on purpose. |
| **Context compression** | Claude Code compacts its own context. |
| **MCP / sub-agents / computer use** | Inherited from the brain. Loki wrapping them would only restrict them. |
| **Per-platform tool restrictions** | Loki gates on *who is asking* (owner / org member / guest), not which app they typed in. People are the trust boundary here, not platforms. |
