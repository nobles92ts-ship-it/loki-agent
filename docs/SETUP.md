# Setup Guide

Full walkthrough for getting Loki running. The short version lives in the [README](../README.md#quick-start-windows).

## 0. Prerequisites

| What | Check |
|---|---|
| Windows 10/11 | — |
| Python 3.10+ | `python --version` |
| Claude Code, logged in | `claude --version` works, and `claude` in a terminal answers (Pro/Max subscription) |
| Slack workspace app permission | you can open <https://api.slack.com/apps> and create an app |

> Loki's brain is the official `claude` CLI under **your login** — the bot consumes your subscription's rolling usage limits, nothing else.

## 1. Create the Slack app

1. <https://api.slack.com/apps> → **Create New App** → **From an app manifest**
2. Pick your workspace → paste all of [`loki/platforms/slack/manifest.yaml`](../loki/platforms/slack/manifest.yaml) → **Create**
   *(the app is named "Loki" — rename freely in the manifest before pasting)*
3. **Install App → Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`)
4. **Basic Information → App-Level Tokens → Generate Token and Scopes**
   - name it anything (e.g. `socket`), add scope **`connections:write`** → copy the `xapp-…` token
5. ⚠️ **App Home tab → scroll to "Show Tabs" → check *"Allow users to send Slash commands and messages from the messages tab"***
   Without this, the DM input box is greyed out and you cannot message the bot at all.

## 2. Run the wizard

```powershell
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
.\setup.ps1
```

The wizard creates the venv, installs dependencies, asks for the two tokens, your Slack member ID (allowlist), the working directory, language, and permission mode — then runs `tools\diag.py` to verify everything.

**Finding your Slack member ID:** your Slack profile → **⋯ (More)** → **Copy member ID** (`U…`).

Prefer manual setup? Copy `.env.example` → `.env` and fill it in, then:

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Start Loki

```powershell
.\venv\Scripts\python.exe -m loki
```

- First boot in read-only mode runs a ~20 s **self-test** proving that plan mode cannot write files (fail-closed — Loki refuses to start if the guarantee breaks).
- Then: `Connecting to Slack (Socket Mode)…` → DM your bot `hello`.

Background / autostart options:
- Double-click `run_worker.vbs` — runs hidden, survives closing the terminal.
- `.\setup.ps1 -Autostart` — registers a launcher in your Startup folder (runs at login).

## 4. Using it in channels

- `/invite @Loki` into a channel → the owner gets a DM heads-up.
- Anyone in the channel can `@Loki <question>` — **always read-only**, regardless of your `.env`.
- Bare channel mentions see the channel's recent history (default 7 days / 120 messages); thread mentions see the thread.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Can't type in the bot's DM | App Home toggle unchecked | Step 1-5 above, then reload Slack (Ctrl+R) |
| `invalid_auth` in diagnostics | wrong/revoked token, or token from another app | re-copy `xoxb-`/`xapp-` from *this* app |
| `Could not find the claude executable` | `claude` not on PATH for the worker | set `CLAUDE_CMD=` full path in `.env` |
| Boot exits with `Missing required setting: …` | `.env` incomplete | fill the key it names (fail-closed by design) |
| Channel mention does nothing | bot not in the channel, or scopes changed without reinstall | `/invite @Loki`; after any manifest scope change: **api.slack.com → your app → Install App → Reinstall** |
| Replies stop mid-conversation / duplicated | **two Loki processes on the same app** — Socket Mode splits events between connections | keep exactly one instance per Slack app |
| `⏱️ Timed out` | request bigger than `TIMEOUT_SEC` | raise `TIMEOUT_SEC` or split the ask |
| Subscription limit message | your Claude plan's rolling window is exhausted | wait for the reset; consider `CLAUDE_MODEL=sonnet` |
| Korean/emoji garbled in logs | legacy console codepage | already handled (UTF-8 forced); report if you still see it |
