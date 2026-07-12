# Changelog

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
