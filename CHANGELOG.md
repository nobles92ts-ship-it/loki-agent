# Changelog

## [v1.6.4] 2026-07-29

Staying up, and making it yours.

- **Is it still running?** — `python -m loki status` answers from the heartbeat alone (no network); `python -m loki doctor` adds the full install check. The worker stamps `state/health.json` on a timer and after every finished job, and **down means either half failing**: the process is gone, *or* the heartbeat went stale. A hung worker that's still technically running is just as broken as one that exited, and the old `tools/diag.py` couldn't tell you about either.
- **Keeping it up** — `python -m loki gateway install` registers Loki with whatever the OS already has: a Scheduled Task on Windows plus a 5-minute watchdog (Windows will start a task but won't restart a crashed one), a systemd user unit with `Restart=on-failure` on Linux, a launchd agent with `KeepAlive` on macOS. Also `gateway ensure` (start only if down — safe on a timer), `stop`, `restart`, `uninstall`. Supervision stays the OS's job; a second Python process watching the first is one more thing that can die quietly.
- **Plugins** — custom commands now live in `plugins/`, one file per command, discovered at boot: define `MATCH` and `handle(match, ctx)` and you're done. `!plugins` lists them. **Owner-only by default**, and opening one up takes *two* steps — `OWNER_ONLY = False` in the plugin **and** `!org allow <org> <name>` — because a file appearing in a folder must never silently hand guests a new way to reach the machine. Built-ins are matched first, so no plugin can shadow `!stop`. A plugin that fails to import, or raises, is contained rather than fatal. See [docs/PLUGINS.md](docs/PLUGINS.md). `plugins/*.py` is gitignored, so your commands survive pulling upstream.
- The `private_commands.py` hook is unchanged and still the right tool for long-running pipelines that own their own threading — plugins return one string.
- `tools/diag.py` is now a shim over `loki.core.diagnostics`, so it and `doctor` can't drift apart.
- **Tests** — +30 (205 total).
- [docs/ROADMAP.md](docs/ROADMAP.md) updated: model fallback on quota moved to *considered and not planned* (a subscription's usage window is one shared pool, so there's nothing smaller to fall back to), and the webhook entry now records that `!schedule` already reaches the same outcome without an inbound port.

## [v1.6.3] 2026-07-28

Discord, and a memory.

- **Discord adapter** — `python -m loki discord` (or `LOKI_PLATFORM=discord`) serves Discord over the gateway, with the same posture as Slack: DMs are the owner's private line, guild `@mentions` are open to anyone but hard-forced read-only, threads carry their own conversation, and guests get the `loki.md` scope, org tiers and rate limits unchanged. Needs `DISCORD_BOT_TOKEN`, `DISCORD_OWNER_ID`, and the **MESSAGE CONTENT** privileged intent — without it Discord delivers empty message bodies, so Loki refuses to start and says so instead of looking connected and deaf. `!check` stays Slack-only (it depends on Slack's interactive buttons). One process per platform; run two to serve both, they share `state/` safely.
- **Conversation memory** — a DM is now one running conversation instead of a fresh session per message: previously the session key was the message's own timestamp, so every top-level DM started over. Threads behaved correctly already and still do. A channel's top level deliberately gets no session — several people share it, and one person's context must not leak into the next person's answer. Sessions expire after `SESSION_IDLE_MIN` minutes of silence (default 120, `0` = never) and persist across restarts in `state/sessions.json`. **`!new`** (`!새대화`, `!리셋`) drops one on demand.
- **Queue orders by conversation** — the job queue now serialises on `session_key`, so two quick DMs can't resume the same Claude session concurrently. Jobs without a session key (channel mentions) still run in parallel as before.
- **Shared command router** — `!jobs`, `!cancel`, `!stop`, `!usage`, `!schedule`, `!learn`, `!new`, `!listen`/`!unlisten`/`!listening`, `!block`/`!unblock` and `!org` moved from the Slack adapter into `loki/core/commands.py`, so every platform inherits one vocabulary instead of a near-miss reimplementation. The owner gate lives inside the router — an adapter can't forget it. Adapters supply only what they alone know (name lookup, mention id extraction). Slack behaviour is unchanged; `!summary` and `!check` stay adapter-side because they aren't plain-text replies.
- **Permission-file tamper guard** (`loki/core/guard.py`) — `loki.md`, `orgs/*.md` and `.env` decide who may read and run what, so any run outside the owner's own DM is both tool-denied from writing them and snapshot-compared afterwards; anything changed is reverted and the owner is DM'd. Authority is read from the transport (user id + DM channel), never from message content.
- **Fix**: `core.orgs` hardcoded Slack's `U…`/`C…` id shapes, so `!org add`/`!org bind` silently rejected every Discord id. Core now validates ids generically; adapters do the precise matching.
- **Fix**: a stale `--resume` id survived a failed retry and was tried again on the next turn. It's dropped now.
- **Tests** — +87 (175 total): sessions, the shared router, and the Discord adapter's surface classification, chunking and async bridge.
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — what's planned and why, with the shape each item would take: process supervision (`doctor` + restart-on-crash), a `plugins/` directory so custom commands stop sharing one file, a model fallback chain so hitting the usage limit degrades instead of failing, per-user sessions in shared channels, token-level usage. Also records what was considered and deliberately *not* planned, so the same ground isn't re-covered.

## [v1.6.2] 2026-07-14

Shared checklists.

- **Checklists** — `!check` posts a shared, clickable checklist: a title line ending in `:`, then one item per line (or a comma-separated list). Each item is a ☐/☑ button that toggles for **everyone** — button labels re-render on `chat.update`, so state stays in sync across viewers (Slack's native `checkboxes` are per-user input and don't sync). Toggle by tapping, or by talking in the checklist's thread: `done 2`, `undo 2 3`, `done all`. Owner creates; anyone who can see it can toggle. Persisted in `state/checklists/`.
- **Manifest** — enables **Interactivity** (`settings.interactivity.is_enabled`) so button clicks arrive over the Socket Mode connection (no Request URL). Apps built from this repo's manifest get it; installs from before v1.6.2 flip it on once under **Interactivity & Shortcuts**. Creating a checklist and `done N` work without it.
- **Tests** — +22 checklist cases (88 total), green on the CI matrix.

## [v1.6.1] 2026-07-13

- **Fix**: the org cache could serve stale data when a create/edit sequence landed within one filesystem-timestamp tick (coarse Windows mtime) — e.g. a double `!org add` of the same user right after `!org create` could bypass dedup. CRUD writes now invalidate the cache explicitly (no timestamp reliance for our own writes) and the change stamp includes file size. Caught by CI on the Windows runners.

## [v1.6.0] 2026-07-13

Per-company tiers.

- **Organizations** — when several companies/teams share one Loki, give each its own tier: `!org create <name>` makes `loki/orgs/<name>.md` (one file = one org, human-editable, applied next request, fail-closed) holding **members**, **bound channels**, **readable folders**, **allowed `!commands`** and a **rate override**. Onboard a whole Slack Connect channel with `!org bind <name> [channel]`, individuals with `!org add <name> @user`; grant fixed pipelines with `!org allow <name> <command>`. Resolution per request: owner → explicit member → bound channel → unaffiliated guest (global `loki.md`, unchanged).
- **Isolation & posture** — orgs never change the permission mode (members stay read-only); each org reads only *its* folders; the org registry itself (`loki/orgs/`) is tool-denied to everyone but the owner; `!usage` now reports by org; the private-command hook receives `ctx["org"]` and `orgs.allows_command()` for gating (see the updated example).
- **Compatibility** — no `orgs/` folder → behavior identical to v1.5.x. No new Slack scopes or events.
- **Tests** — +13 org cases (66 total), green on the CI matrix.

## [v1.5.0] 2026-07-13

Talk without the @.

- **Auto-listen zones** — owner opt-in `!listen`: in a thread it registers that thread, at channel top level the whole channel. Everyone there then talks to Loki **without a mention**. `!unlisten` stops (most specific zone first), `!listening` lists zones. Permissions unchanged: guests stay read-only + rate-limited, `!block` overrides a zone, @mentions inside a zone aren't double-answered (they keep flowing through `app_mention`), and bot messages are ignored — no reply loops. Korean aliases: `!청취` `!청취해제` `!청취목록`. Persisted in `state/autolisten.json`.
- **Manifest** — adds `message.channels` + `message.groups` bot events (no new OAuth scopes). Existing installs: add the two events under **Event Subscriptions**; new installs get them from the manifest.
- **Tests** — +7 zone cases (53 total), green on the CI matrix.

## [v1.4.1] 2026-07-11

- **Fix**: guest rate-limit "try again in N min" could report 61 at the top of the window (max should be 60) — ceiling math corrected. Was also a timing-dependent CI flake.

## [v1.4.0] 2026-07-11

Replies that render, and images.

- **Markdown → Slack mrkdwn** — Claude answers in CommonMark, so headers, `**bold**`, `[links](url)`, and `- bullets` used to show their raw punctuation in Slack. Loki now converts them to Slack's dialect (`*bold*`, `<url|text>`, `•`, `~strike~`, headers → bold, tables → monospaced code block). Code spans and fences are protected; conversion is applied only to model output, never the bot's own strings.
- **Image input** — drop a screenshot in your DM (caption optional) and Loki downloads it and hands the local path to Claude to analyze. Owner-only; 20 MB cap.
- **File output** — when the owner's reply references a local output file (`.html/.png/.pdf/.csv/...` under `WORK_DIR`, size-capped, max 4), Loki uploads it to the thread.
- **Tests** — +12 mrkdwn cases (46 total), green on the CI matrix.

## [v1.3.0] 2026-07-11

Account control, abuse control, and private commands.

- **Dedicated account** — `CLAUDE_CONFIG_DIR` points the spawned `claude` at its own config dir, so Loki authenticates as a specific account independent of your terminal login (e.g. work vs personal). Set authoritatively over any inherited env; Windows/Linux isolate `.credentials.json` per dir. The setup wizard asks for it.
- **Guest rate limiting** — `GUEST_RATE_PER_HOUR` (default 10; `0` = unlimited) caps each non-owner's requests per rolling hour to protect your subscription. Owners are never limited; the wizard asks for the value. Persisted in `state/ratelimit.json`.
- **Private command hook** — copy `loki/platforms/slack/private_commands.example.py` → `private_commands.py` (gitignored) and implement `try_handle(ctx)`; it runs before normal dispatch, so you can gate a heavy pipeline to named trusted users and stream progress — without touching core or forking.
- **Tests** — +7 cases (rate limiter windows/isolation/disable, dedicated-account env passthrough + parent-auth stripping). 34 total, green on the CI matrix.

## [v1.2.0] 2026-07-10

Cross-platform, proactive, and observable.

- **macOS / Linux support** — POSIX process groups (`start_new_session` + `killpg`) replace Windows-only tree-kill; `setup.sh` wizard; systemd/launchd autostart examples in SETUP.md.
- **Scheduler** — owner DM `!schedule daily|weekly|once … <prompt>` (+ `list` / `remove`). Fires run at the owner's permission mode and post back to the DM. Missed recurring slots roll forward (no catch-up spam); a missed `once` fires on boot. Persisted in `state/schedules.json`.
- **Parallel jobs** — up to `JOB_CONCURRENCY` (default 2) Claude processes at once; same-conversation jobs stay strictly ordered so `--resume` continuity holds. `!jobs` lists running/queued with ids, `!cancel <id>` kills exactly one, `!stop` now cancels everything. Cancelled jobs no longer resurrect through the stale-resume retry.
- **`!usage [days]`** — usage report (calls, ok/fail, total time, by user / by kind) from a metadata-only ledger (`state/usage.jsonl`, 90-day retention).
- **`!learn <note>`** — appends to a private learnings inbox (`state/learnings.md`) to feed your own memory/review process.
- **Test suite + CI** — 27 pytest cases over the core (allowlist fail-closed, queue ordering/cancel, scheduler math, dedup, i18n parity, output parsing); GitHub Actions matrix: Ubuntu / Windows / macOS × Python 3.10 / 3.12.
- Korean command aliases for everything new: `!작업목록` `!취소` `!사용량` `!예약` `!학습`.

## [v1.1.0] 2026-07-10

Guest access, made opt-in and observable.

- **Guest path allowlist** — `<WORK_DIR>/loki/loki.md` is the single list of what non-owners may read. Everything else (the rest of WORK_DIR, other drives, `~/.claude`) is tool-denied per request via a `--settings` file. Fail-closed: an empty manifest means guests see nothing. Edits apply without a restart.
- **No side doors** — guests also lose `Bash`, `Skill` and `Task`; their working directory is pinned to the loki folder; the shared scope is explained to the model in-prompt.
- **Channel kill switch** — owner DM commands `!block <channel_id>` / `!unblock <channel_id>` (persisted in `state/blocked_channels.json`); channel-invite notices now include a one-tap block hint.
- **Owner `!summary <channel_id>`** — summarize another channel's recent conversation from your DM.
- Docs: permission tier table, owner command reference, permission-posture setup guide.

## [v1.0.0] 2026-07-09

First public release.

- **Slack adapter** (Socket Mode): owner DMs + channel `@mentions`
- **Claude Code brain**: spawns the official `claude -p` under your subscription login — no API key, no metered billing
- **Permission model**: read-only `plan` by default with a fail-closed boot self-test; `bypassPermissions` opt-in; guests always forced read-only
- **Context awareness**: thread mentions see the thread, bare channel mentions see recent channel history (windowed, capped) — all wrapped in a prompt-injection guard
- **Conversation continuity** per thread via `--resume`
- **Ops hardening**: serial job queue, event dedup (Slack redelivery), `!stop` kill switch, timeout tree-kill, UTF-8 enforcement, hidden console windows, metadata-only logging
- **i18n**: bot messages in English (default) or Korean (`LOKI_LANG=ko`)
- **Setup wizard** (`setup.ps1`) + autostart launcher + connection diagnostics (`tools/diag.py`)
