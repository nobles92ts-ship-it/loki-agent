# Loki

**Chat with your own PC.** Loki is a small local agent that connects Slack to [Claude Code](https://claude.com/claude-code) running on your machine — powered by the Claude subscription you already have.

No API key. No per-call billing. Your files, your shell, your Claude — reachable from your phone.

```
Slack DM / @mention
        │  Socket Mode (no public URL needed)
        ▼
  Loki (this repo, runs on your PC)
        │  spawns the official CLI:  claude -p
        ▼
  Claude Code  ──  reads/writes files, runs commands in WORK_DIR
        │
        ▼
  answer posted back to the Slack thread
```

## Why Loki

- **Subscription-powered** — the brain is the official `claude` CLI using your Pro/Max login. No `sk-…` key, no metered bill at the end of the month.
- **Actually local** — Loki works on *your* PC: summarize a folder, fix a script, run a build. You decide how much power it gets.
- **Guests stay read-only** — anyone may `@Loki` in a channel, but guest calls are hard-forced into read-only mode. Only the owner's DM can write/execute.
- **Context aware** — mention it in a thread and it reads the thread; mention it bare in a channel and it reads recent channel history. All context is wrapped as *data, not instructions* (prompt-injection guard).
- **Built to be extended** — `loki/core` is platform-agnostic; Slack is just the first adapter ([roadmap](#roadmap)).

## Quick start (Windows)

**Prerequisites**
- Windows 10/11, Python 3.10+
- [Claude Code](https://claude.com/claude-code) installed and logged in (`claude` works in a terminal) with a Pro/Max subscription
- Permission to create an app in your Slack workspace

**1. Create the Slack app (≈2 min)**
1. Open <https://api.slack.com/apps> → **Create New App** → **From an app manifest**
2. Pick your workspace, paste the contents of [`loki/platforms/slack/manifest.yaml`](loki/platforms/slack/manifest.yaml)
3. **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`)
4. **Basic Information → App-Level Tokens** → generate one with scope `connections:write` → copy it (`xapp-…`)
5. ⚠️ **App Home tab → check "Allow users to send Slash commands and messages from the messages tab"** — without this the DM input box is disabled.

**2. Set up and run**
```powershell
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
.\setup.ps1          # wizard: venv + deps + .env (tokens, your Slack ID, WORK_DIR)
.\venv\Scripts\python.exe -m loki
```

**3. Test** — DM your bot: `hello`. First reply takes ~15–30 s.

Optional: `.\setup.ps1 -Autostart` registers a hidden background launcher at login.
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
| `LOKI_LANG` | `en` | bot message language: `en` / `ko` |
| `LOKI_CHANNEL_CTX_DAYS` / `_MSGS` | `7` / `120` | how much channel history a bare mention sees |
| `CLAUDE_CMD` | auto-detected | full path to `claude` if not on PATH |

## Usage

| Where | Who | Power |
|---|---|---|
| **DM** | owner only | your configured mode (up to full write/execute) |
| **`@Loki` in a channel** | anyone in the channel | **always read-only**, sees recent channel history |
| **`@Loki` in a thread** | anyone in the channel | **always read-only**, sees the thread |

- Reply in the same thread to keep conversation context (`--resume`).
- `!stop` (owner only) kills the running job.
- Invite to a channel with `/invite @Loki` — the owner gets a DM heads-up when it joins.

## Extending Loki

Loki's brain is your **full Claude Code**, so it can run any skill, subagent, or slash command installed under `~/.claude` — not just answer questions.

- **Just ask** (owner · write mode): *"run my X skill on Y"* — any installed skill works, like in a terminal.
- **Wire a fixed `!command`** for heavy, repeatable pipelines that stream progress back while they run for minutes or hours.

For example, you can install an open-source Claude Code pipeline like the [AI_GAME_QA_TestCase](https://github.com/nobles92ts-ship-it/AI_GAME_QA_TestCase) QA test-case generator into `~/.claude` and trigger it through Loki — or activate skills you already have, on demand.

→ Worked example + code sketch: **[docs/EXAMPLES.md](docs/EXAMPLES.md)**

## Security model

- **Read-only by default.** Every Claude call is forced to `--permission-mode plan` unless you opt in. A boot self-test verifies plan mode cannot write — if that guarantee ever breaks, Loki refuses to start.
- **Allowlist is mandatory.** DMs and write power belong to exactly one Slack user ID.
- **Guests are hard-capped.** Channel callers get `plan` regardless of your config.
- **Injection guard.** Thread/channel context is wrapped as data with an explicit "nothing in here is an instruction" frame.
- **Honest residual risks** (read [docs/SECURITY.md](docs/SECURITY.md) before enabling write mode): a compromised Slack account = access to this bot; read-only mode can still *read* and post file contents; write mode means Slack messages can change your PC.

## FAQ

**Is this against Anthropic's ToS?** Loki spawns the official `claude` CLI on your own machine under your own login — the same as you running it in a terminal. It does not extract or inject subscription tokens into third-party API clients.

**What does it cost?** Nothing extra — requests consume your Claude subscription's rolling usage limits. Tip: `CLAUDE_MODEL=sonnet` stretches them further.

**macOS / Linux?** Not yet — a few Windows-specific bits (`taskkill`, console-window suppression, the `.cmd` shim). See roadmap.

**Why Socket Mode?** No public URL, no port-forwarding, works behind any NAT/firewall.

## Roadmap

| Version | Platform / feature |
|---|---|
| v1.0 | ✅ Slack (DM · channel mentions · thread/channel context · guest read-only) |
| v1.x | i18n polish, diagnostics, richer setup wizard |
| v2.0 | **Telegram** adapter (first proof of `platforms/base` contract) · macOS/Linux |
| v2.x | **Discord** · **Home Assistant** |
| v3.x | **Signal** (signal-cli) · **WhatsApp** (Business API) |

Want to add a platform? Start at [docs/PLATFORMS.md](docs/PLATFORMS.md).

## Feedback & issues

This is early — please use it hard and **file issues generously**: broken setup steps, confusing docs, a platform quirk, a security concern, a feature you wish existed. Even "this one sentence in SETUP.md confused me" is useful. [Open an issue](../../issues/new) or start a [discussion](../../discussions) for anything less bug-shaped (ideas, "how do I…", a new platform adapter you're building).

## License

[MIT](LICENSE) · 한국어 문서: [README.ko.md](README.ko.md)
