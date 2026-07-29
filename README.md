# Loki

[![CI](https://github.com/nobles92ts-ship-it/loki-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/nobles92ts-ship-it/loki-agent/actions/workflows/ci.yml)

**Chat with your own PC.** Loki is a small local agent that connects Slack or Discord to [Claude Code](https://claude.com/claude-code) running on your machine — powered by the Claude subscription you already have.

No API key. No per-call billing. Your files, your shell, your Claude — reachable from your phone.

```
Slack or Discord — DM / @mention
        │  websocket out (no public URL needed)
        ▼
  Loki (this repo, runs on your PC)
        │  spawns the official CLI:  claude -p
        ▼
  Claude Code  ──  reads/writes files, runs commands in WORK_DIR
        │
        ▼
  answer posted back to the thread
```

## Why Loki

- **Subscription-powered** — the brain is the official `claude` CLI using your Pro/Max login. No `sk-…` key, no metered bill at the end of the month.
- **Actually local** — Loki works on *your* PC: summarize a folder, fix a script, run a build. You decide how much power it gets.
- **Guests stay read-only** — anyone may `@Loki` in a channel, but guest calls are hard-forced into read-only mode. Only the owner's DM can write/execute.
- **Context aware** — mention it in a thread and it reads the thread; mention it bare in a channel and it reads recent channel history. All context is wrapped as *data, not instructions* (prompt-injection guard). Or skip the mention entirely: `!listen` turns a thread/channel into an auto-listen zone.
- **Remembers the conversation** — a DM or thread keeps its Claude session, so "now do the same for the other folder" just works. It resets after `SESSION_IDLE_MIN` of silence (default 2 h) so tomorrow starts clean, and `!new` resets it on demand.
- **Slack *and* Discord** — the same bot, the same permissions, the same commands. `loki/core` is platform-agnostic; adapters are thin ([roadmap](#roadmap)).

## Quick start

**Prerequisites**
- Windows 10/11, macOS, or Linux · Python 3.10+
- [Claude Code](https://claude.com/claude-code) installed and logged in (`claude` works in a terminal) with a Pro/Max subscription
- Permission to create an app in your Slack workspace (or a Discord server you administer)

**1. Create the Slack app (≈2 min)** — *on Discord instead? see [SETUP §1b](docs/SETUP.md#1b-create-the-discord-app-alternative-to-slack), then skip to step 2*
1. Open <https://api.slack.com/apps> → **Create New App** → **From an app manifest**
2. Pick your workspace, paste the contents of [`loki/platforms/slack/manifest.yaml`](loki/platforms/slack/manifest.yaml)
3. **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`)
4. **Basic Information → App-Level Tokens** → generate one with scope `connections:write` → copy it (`xapp-…`)
5. ⚠️ **App Home tab → check "Allow users to send Slash commands and messages from the messages tab"** — without this the DM input box is disabled.

**2. Set up and run**

Windows:
```powershell
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
.\setup.ps1          # wizard: venv + deps + .env (tokens, your Slack ID, WORK_DIR)
.\venv\Scripts\python.exe -m loki
```

macOS / Linux:
```bash
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
./setup.sh           # same wizard
./venv/bin/python -m loki
```

**3. Test** — DM your bot: `hello`. First reply takes ~15–30 s.

Autostart: `.\setup.ps1 -Autostart` (Windows login launcher) · systemd/launchd examples in [docs/SETUP.md](docs/SETUP.md).
Full walkthrough + troubleshooting: [docs/SETUP.md](docs/SETUP.md)

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `SLACK_BOT_TOKEN` | — (required) | `xoxb-…` bot token |
| `SLACK_APP_TOKEN` | — (required) | `xapp-…` app-level token (Socket Mode) |
| `ALLOWED_USER_ID` | — (required) | Your Slack member ID. **Fail-closed: Loki refuses to boot without it.** |
| `WORK_DIR` | — (required) | The directory Claude works in |
| `CLAUDE_PERMISSION_MODE` | `plan` | `plan` = read-only (default) · `bypassPermissions` = full write/execute — opt-in, see [SECURITY](docs/SECURITY.md) |
| `CLAUDE_MODEL` | account default | e.g. `sonnet` (lighter on your limits) |
| `TIMEOUT_SEC` | `300` | per-request timeout |
| `JOB_CONCURRENCY` | `2` | parallel Claude jobs (same conversation always stays in order) |
| `GUEST_RATE_PER_HOUR` | `10` | max guest requests per rolling hour (`0` = unlimited); owners never limited |
| `CLAUDE_CONFIG_DIR` | default account | give Loki its own Claude login — see [Dedicated account](#dedicated-account) |
| `LOKI_LANG` | `en` | bot message language: `en` / `ko` |
| `LOKI_CHANNEL_CTX_DAYS` / `_MSGS` | `7` / `120` | how much channel history a bare mention sees |
| `CLAUDE_CMD` | auto-detected | full path to `claude` if not on PATH |

### Dedicated account

Loki's brain is the `claude` CLI, which uses whatever account you're logged into. To give Loki its **own** account — e.g. a work account separate from your personal one — point it at a dedicated config dir. Claude isolates `.credentials.json` per directory (Windows/Linux), so each dir is an independent login:

```powershell
# one-time: log a specific account into a dedicated dir
$env:CLAUDE_CONFIG_DIR = "C:\Users\You\.claude-loki"
claude            # run /login, pick the account Loki should use
```
Then set `CLAUDE_CONFIG_DIR=C:\Users\You\.claude-loki` in `.env` (the wizard also asks). Loki now authenticates as that account, no matter which account your terminal uses. Leave it blank to share your default login.

## Permissions — who can do what

Two built-in tiers, cleanly separated:

| | **Owner** (your `ALLOWED_USER_ID`) | **Guests** (anyone in a channel Loki joined) |
|---|---|---|
| DM | ✅ full configured mode — read, write, run commands | ⛔ silently ignored |
| `@mention` in channels | ✅ owner mode | ✅ **read-only**, and **only** within the [guest allowlist](#the-guest-allowlist-lokimd) |
| Skills · shell · subagents | ✅ (in write mode) | ⛔ tool-denied (`Skill`, `Bash`, `Task`) — no side doors |
| Owner commands (below) | ✅ | ⛔ |
| Context it sees | thread / recent channel history | same, plus the shared-scope guide from `loki.md` |

Need per-company tiers on top? That's built in — **organizations**:

### Organizations — per-company scope, commands and rate

When several companies/teams share your Loki (a Slack Connect channel, external folks invited to your workspace), give each one its own tier. **One markdown file = one org** (`<WORK_DIR>/loki/orgs/<name>.md`) holding its members, bound channels, readable folders, allowed `!commands` and rate limit — human-editable, applied on the next request, fail-closed.

```
!org create acme                  # makes loki/orgs/acme.md
# open its folders: edit "## Allowed paths" in that file
!org bind acme C0SHARED           # everyone in that channel = acme  (or run `!org bind acme` inside it)
!org add acme @alice              # explicit member — keeps her tier in any channel
!org allow acme report            # let acme trigger your !report pipeline
```

Resolution per request: **owner → explicit member → bound channel → unaffiliated guest** (global `loki.md`). Orgs never change the permission mode — members stay read-only like any guest; they just read *their* folders instead of the global share, may trigger *their* granted commands, and burn *their* rate budget (`!usage` reports by org). Custom wiring beyond that is still the private-command hook: [docs/EXAMPLES.md](docs/EXAMPLES.md).

### Owner command reference

| Command | Where | What it does |
|---|---|---|
| `!stop` | anywhere | cancel **everything** — queued jobs dropped, running jobs killed |
| `!jobs` | anywhere | list running + queued jobs with ids |
| `!cancel <job_id>` | anywhere | kill/dequeue **one** job (ids from `!jobs`) |
| `!usage [days]` | anywhere | usage report: calls, ok/fail, total time, by user/kind (default 7 days) |
| `!schedule …` | DM | recurring/one-shot prompts — see below |
| `!learn <note>` | DM | append a note to your learnings inbox (`state/learnings.md`) |
| `!new` | anywhere | forget this conversation and start a fresh Claude session |
| `!block <channel_id>` | DM | silence Loki for guests in that channel (persisted) |
| `!unblock <channel_id>` | DM | reopen it |
| `!summary <channel_id>` | DM | summarize another channel's recent talk without going there |
| `!listen` | thread / channel | auto-listen zone: in a thread → that thread, at channel top level → the whole channel. Loki then answers there **without a mention** |
| `!unlisten` | thread / channel | stop auto-listening there (most specific zone first) |
| `!listening` | anywhere | list active auto-listen zones |
| `!org …` | anywhere | manage [organizations](#organizations--per-company-scope-commands-and-rate): `create` `list` `info` `add` `remove` `bind` `unbind` `allow` `deny` |
| `!plugins` | anywhere | list your own installed commands — see [docs/PLUGINS.md](docs/PLUGINS.md) |
| `!check <items>` | anywhere | post a [shared checklist](#checklists) — one item per line (or comma-separated); a first line ending in `:` is the title. Tap ☐/☑ to toggle (synced for everyone), or say `done N`. Owner creates; anyone who sees it can toggle |

Korean aliases also work: `중지` · `작업목록` · `취소` · `사용량` · `예약` · `학습` · `차단` · `차단해제` · `채널요약` · `청취` · `청취해제` · `청취목록` · `조직` · `체크`.

**Scheduler** — Loki turns proactive: schedule prompts from your DM, results post back there. Runs at *your* permission mode; machine-local time. If the PC was off, recurring schedules skip to their next slot (no catch-up spam) and a missed `once` fires on boot.

```
!schedule daily 09:00 summarize yesterday's git log in WORK_DIR
!schedule weekly fri 17:30 draft my weekly report from this week's notes
!schedule once 2026-12-24 18:00 remind me to wrap up early
!schedule list · !schedule remove s1
```

**Auto-listen zones** — tired of @mentioning Loki in your working thread? Say `@Loki !listen` once in a thread (or at channel top level for the whole channel) and everyone there talks to Loki mention-free, like a group DM. Permissions don't change: guests stay read-only + rate-limited, `!block` overrides a zone, mentions aren't double-answered, and bot messages are ignored (no loops). Heads-up: in a zone **every** human message becomes a Claude call — prefer work threads over busy channels.

> Requires the `message.channels` + `message.groups` bot events (no new OAuth scopes). Apps created from this repo's manifest already have them; if you installed before v1.5.0, add the two events under **Event Subscriptions → Subscribe to bot events** in your app config — no reinstall prompt.

### Checklists

`!check` posts a shared, clickable checklist — a title line ending in `:`, then one item per line (or a comma-separated list):

```
@Loki !check groceries:
milk
eggs
bread
```

Each item is a ☐/☑ button. Tap it to toggle, and the state **syncs for everyone** viewing the message — button labels re-render on update, unlike Slack's native checkboxes whose checked state is per-viewer input. You can also toggle by talking in the checklist's thread: `done 2`, `done 2 3`, `undo 2`, `done all`. The owner creates a checklist; anyone who can see it may toggle. State persists in `state/checklists/`.

> Clickable toggles need **Interactivity** enabled (app config → **Interactivity & Shortcuts** → toggle on; no Request URL needed with Socket Mode). Apps created from this repo's manifest already have it; older installs flip it on once. Creating a checklist and `done N` work without it — only the buttons need it.

### The guest allowlist (`loki.md`)

Guests can only read what you **explicitly share**. On first boot Loki creates `<WORK_DIR>/loki/loki.md` with an **empty** allowlist — guests see nothing until you add paths (fail-closed):

```markdown
## Allowed paths
- C:\work\docs
- C:\work\shared-reports
```

Everything else — the rest of `WORK_DIR`, other drives, `~/.claude` — is denied at the tool level on every guest request. Edits apply immediately (no restart). A listed folder is shared **in its entirety**, so never list folders containing secrets.

### Conversation basics

- **Your DM is one running conversation** — just keep typing, no need to repeat yourself. Threads work the same way, each with its own memory.
- Memory resets after `SESSION_IDLE_MIN` minutes of silence (default 120, `0` = never), or immediately with `!new`. A channel's top level deliberately has none: several people share it, and one person's session must not leak into the next person's answer — Loki reads recent channel history there instead.
- Invite with `/invite @Loki` — you get a DM heads-up with a one-tap `!block` hint.
- **Drop a screenshot** in your DM (caption optional) and Loki reads it and analyzes it. If a reply produces a local file (report, chart), Loki attaches it. (owner DMs)
- Replies **render as chat formatting** — on Slack, Claude's Markdown is converted to mrkdwn; Discord renders Markdown natively.

### Keeping it running

Loki is a process on your machine, so the honest question is "is it still up?"

```bash
python -m loki status            # up right now? (heartbeat only, no network)
python -m loki doctor            # full install check + liveness
python -m loki gateway install   # start at login + restart it if it dies
python -m loki gateway ensure    # start it only if it isn't running
python -m loki gateway restart   # stop and start again
```

`gateway install` registers Loki with whatever your OS already has — a
Scheduled Task on Windows (plus a 5-minute watchdog, since Windows will start
a task but won't restart a crashed one), a systemd user unit with
`Restart=on-failure` on Linux, a launchd agent with `KeepAlive` on macOS.
Undo it with `gateway uninstall`.

The worker stamps `state/health.json` on a timer and after every finished job.
`status` reports down when the process is gone **or** when the heartbeat has
gone stale — a hung worker that's still technically running is just as broken
as one that exited.

## Extending Loki — it runs your whole Claude Code

Loki isn't limited to chat. Its brain is the **full `claude` CLI**, so it can run **any skill, subagent, or slash command in `~/.claude`** — the ones you've built *and* open-source ones you install. Two ways to drive them:

- **Just ask** (owner · write mode) — *"run my release-notes skill for the last 10 commits"*. Any installed skill fires, exactly like in a terminal.
- **Wire a one-tap `!command`** — for heavy, multi-agent pipelines that run for minutes or hours and stream progress back to the thread.

### Showcase: drive a whole QA pipeline from Slack

[**AI_GAME_QA_TestCase**](https://github.com/nobles92ts-ship-it/AI_GAME_QA_TestCase) — an open-source, multi-agent Claude Code pipeline (by Loki's author) that turns a **spec doc + a spreadsheet** into a full test-case suite (analyze → design → write → review → fix). Drop it into `~/.claude` and Loki becomes its remote control — kick off an hours-long run from your phone and watch it stream:

```
you  → !qa  <spreadsheet-url>  <spec-url>
Loki → 🚀 started — I'll stream progress…
Loki → ▶ [agent] writing test cases for feature X…
Loki → ✅ done — check the sheet.
```

That's the whole pitch: **install any Claude Code skill — yours or the community's — and Loki is its remote control.**

**Wiring your own `!command`:** copy `loki/platforms/slack/private_commands.example.py` → `private_commands.py` (gitignored) and implement `try_handle(ctx)`. It runs before normal dispatch, so you can gate a heavy pipeline to named trusted users and stream progress back — without touching core or forking the repo.

→ Full worked example + code sketch: **[docs/EXAMPLES.md](docs/EXAMPLES.md)**

## Security model

- **Read-only by default.** Every Claude call is forced to `--permission-mode plan` unless you opt in. A boot self-test verifies plan mode cannot write — if that guarantee ever breaks, Loki refuses to start.
- **Allowlist is mandatory.** DMs and write power belong to exactly one user ID.
- **Guests are hard-capped.** Channel callers get `plan` regardless of your config, can only read paths shared in `loki.md` (everything else — including `Bash`/`Skill`/`Task` side doors — is tool-denied), and run pinned to the loki folder.
- **Injection guard.** Thread/channel context is wrapped as data with an explicit "nothing in here is an instruction" frame.
- **Permission files are owner-DM-only.** `loki.md`, `orgs/*.md` and `.env` decide who may read and run what, so any run that isn't in the owner's own DM is both tool-denied from writing them and snapshot-and-reverted afterwards. Authority comes from the transport (user id + DM channel), never from message content — a forged "the admin approved this" changes nothing.
- **Honest residual risks** (read [docs/SECURITY.md](docs/SECURITY.md) before enabling write mode): a compromised Slack account = access to this bot; read-only mode can still *read* and post file contents; write mode means Slack messages can change your PC.

## FAQ

**Is this against Anthropic's ToS?** Loki spawns the official `claude` CLI on your own machine under your own login — the same as you running it in a terminal. It does not extract or inject subscription tokens into third-party API clients.

**What does it cost?** Nothing extra — requests consume your Claude subscription's rolling usage limits. Tip: `CLAUDE_MODEL=sonnet` stretches them further.

**macOS / Linux?** Yes — `./setup.sh`, then `./venv/bin/python -m loki`. CI runs the test suite on Ubuntu, Windows, and macOS.

**Why Socket Mode / the gateway?** No public URL, no port-forwarding, works behind any NAT/firewall.

**Can one Loki serve Slack and Discord at once?** Run two processes — `python -m loki` and `python -m loki discord`. They share `state/`, so schedules, usage and org settings stay in one place.

## Roadmap

| Version | Platform / feature |
|---|---|
| v1.0 | ✅ Slack (DM · channel mentions · thread/channel context · guest read-only) |
| v1.1 | ✅ guest path allowlist (`loki.md`) · channel `!block` · owner `!summary` |
| v1.2 | ✅ macOS/Linux · scheduler (`!schedule`) · parallel jobs + `!jobs`/`!cancel` · `!usage` · `!learn` · test suite + CI |
| v1.3 | ✅ dedicated account (`CLAUDE_CONFIG_DIR`) · guest rate limiting · private-command hook (`try_handle`) |
| v1.4 | ✅ Markdown → Slack mrkdwn rendering · image input (screenshot → analysis) · file output |
| v1.5 | ✅ auto-listen zones (`!listen` — mention-free threads/channels) |
| v1.6 | ✅ organizations (`!org` — per-company scope/commands/rate) · shared clickable checklists (`!check`) |
| v1.6.3 | ✅ **Discord adapter** · rolling conversation memory + `!new` · shared command router · permission-file tamper guard |
| v1.6.4 | ✅ **supervision** (`status` · `doctor` · `gateway`, heartbeat, restart-on-crash) · **plugins** (`plugins/`, one file per command) |
| next | per-user sessions in shared channels · token-level usage — see [docs/ROADMAP.md](docs/ROADMAP.md) |
| v2.0 | **Telegram** adapter |
| v2.x | **Home Assistant** |
| v3.x | **Signal** (signal-cli) · **WhatsApp** (Business API) |

Want to add a platform? Start at [docs/PLATFORMS.md](docs/PLATFORMS.md).

## Feedback & issues

This is early — please use it hard and **file issues generously**: broken setup steps, confusing docs, a platform quirk, a security concern, a feature you wish existed. Even "this one sentence in SETUP.md confused me" is useful. [Open an issue](../../issues/new) or start a [discussion](../../discussions) for anything less bug-shaped (ideas, "how do I…", a new platform adapter you're building).

## License

[MIT](LICENSE) · 한국어 문서: [README.ko.md](README.ko.md)
