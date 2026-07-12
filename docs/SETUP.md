# Setup Guide

Full walkthrough for getting Loki running. The short version lives in the [README](../README.md#quick-start-windows).

## 0. Prerequisites

| What | Check |
|---|---|
| Windows 10/11 · macOS · Linux | — |
| Python 3.10+ | `python --version` |
| Claude Code, logged in | `claude --version` works, and `claude` in a terminal answers (Pro/Max subscription) |
| Slack workspace app permission | you can open <https://api.slack.com/apps> and create an app |

> Loki's brain is the official `claude` CLI under **your login** — the bot consumes your subscription's rolling usage limits, nothing else.

## Before you start — choose your permission posture

Decide what Loki should be allowed to do; the wizard sets it up accordingly.

| You want | During setup | After setup |
|---|---|---|
| **Solo, safe** — analysis, lookups, summaries | keep permission mode `plan` (default) | nothing |
| **Solo, full power** — write files, run commands from your DM | answer `y` to write mode (`bypassPermissions`) — read [SECURITY.md](SECURITY.md) first | nothing |
| **Team channels** — colleagues may query it | either mode (guests are independent of it) | `/invite @Loki` to channels, then list shared folders in `<WORK_DIR>\loki\loki.md` |
| **Team + one trusted pipeline** — named users may trigger a fixed heavy command | as above | wire a `!command` for named users — [EXAMPLES.md](EXAMPLES.md) |

Whatever you pick: **guests are always read-only** and see **only** the paths you put in `loki.md` (created empty on first boot — sharing is opt-in, folder by folder). You can silence any channel with `!block <channel_id>` from your DM. The full tier table lives in the [README](../README.md#permissions--who-can-do-what).

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

macOS / Linux:

```bash
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
./setup.sh
```

The wizard creates the venv, installs dependencies, asks for the two tokens, your Slack member ID (allowlist), the working directory, language, permission mode, an optional **dedicated-account** config dir, and the **guest hourly limit** — then runs `tools\diag.py` to verify everything.

### Optional: give Loki its own Claude account

By default Loki uses whatever account `claude` is logged into. To run it under a **separate** account (e.g. a work login kept apart from your personal one), log that account into a dedicated config dir once, then point `.env` at it:

```powershell
$env:CLAUDE_CONFIG_DIR = "C:\Users\You\.claude-loki"   # any empty dir
claude                                                  # run /login → choose the account
# then in .env:  CLAUDE_CONFIG_DIR=C:\Users\You\.claude-loki
```

Claude stores `.credentials.json` inside that dir (Windows/Linux), so it's a fully independent login. macOS uses the system Keychain and doesn't isolate per-dir — use a separate OS user there instead.

**Finding your Slack member ID:** your Slack profile → **⋯ (More)** → **Copy member ID** (`U…`).

Prefer manual setup? Copy `.env.example` → `.env` and fill it in, then:

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Start Loki

```powershell
.\venv\Scripts\python.exe -m loki      # Windows
```
```bash
./venv/bin/python -m loki              # macOS / Linux
```

- First boot in read-only mode runs a ~20 s **self-test** proving that plan mode cannot write files (fail-closed — Loki refuses to start if the guarantee breaks).
- Then: `Connecting to Slack (Socket Mode)…` → DM your bot `hello`.

Background / autostart — Windows:
- Double-click `run_worker.vbs` — runs hidden, survives closing the terminal.
- `.\setup.ps1 -Autostart` — registers a launcher in your Startup folder (runs at login).

Background / autostart — macOS / Linux:
- Quick: `nohup ./venv/bin/python -m loki >/dev/null 2>&1 &`
- Linux (systemd user service) — `~/.config/systemd/user/loki.service`, then `systemctl --user enable --now loki`:

  ```ini
  [Unit]
  Description=Loki agent
  After=network-online.target

  [Service]
  WorkingDirectory=%h/loki-agent
  ExecStart=%h/loki-agent/venv/bin/python -m loki
  Restart=on-failure

  [Install]
  WantedBy=default.target
  ```

- macOS (launchd) — `~/Library/LaunchAgents/com.loki.agent.plist`, then `launchctl load` it (replace `YOU` with your username):

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0"><dict>
    <key>Label</key><string>com.loki.agent</string>
    <key>ProgramArguments</key>
    <array>
      <string>/Users/YOU/loki-agent/venv/bin/python</string>
      <string>-m</string><string>loki</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/YOU/loki-agent</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
  </dict></plist>
  ```

## 4. Using it in channels

- `/invite @Loki` into a channel → the owner gets a DM heads-up.
- Anyone in the channel can `@Loki <question>` — **always read-only**, regardless of your `.env`.
- Bare channel mentions see the channel's recent history (default 7 days / 120 messages); thread mentions see the thread.
- **Mention-free zones**: the owner can say `@Loki !listen` in a thread (that thread) or at channel top level (whole channel) — everyone there then talks to Loki without a mention. `!unlisten` stops, `!listening` lists. Guests keep their read-only + rate-limit guardrails.

> **Upgrading from ≤ v1.4.x?** Auto-listen needs two extra bot events. In your app config ([api.slack.com/apps](https://api.slack.com/apps) → your app) open **Event Subscriptions → Subscribe to bot events** and add `message.channels` and `message.groups`, then Save. No new OAuth scopes, so there's no reinstall prompt. Apps created from the current manifest already have them.

### Per-company access (organizations)

Sharing Loki with another company — a Slack Connect channel or external folks in your workspace? Give them their own tier instead of the global guest share:

1. `!org create acme` → creates `WORK_DIR/loki/orgs/acme.md`
2. Edit that file's `## Allowed paths` — the folders *only this org* may read
3. Connect the people: `!org bind acme` inside their shared channel (whole channel), and/or `!org add acme @person` (individuals)

Optional: `- rate: 20` under `## Settings` for a per-org hourly cap, `!org allow acme <command>` to grant a fixed pipeline. Everything lives in the org's markdown file — edit it any time, no restart. See the README's Organizations section for the resolution rules.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Can't type in the bot's DM | App Home toggle unchecked | Step 1-5 above, then reload Slack (Ctrl+R) |
| `invalid_auth` in diagnostics | wrong/revoked token, or token from another app | re-copy `xoxb-`/`xapp-` from *this* app |
| `Could not find the claude executable` | `claude` not on PATH for the worker | set `CLAUDE_CMD=` full path in `.env` |
| Boot exits with `Missing required setting: …` | `.env` incomplete | fill the key it names (fail-closed by design) |
| Channel mention does nothing | bot not in the channel, or scopes changed without reinstall | `/invite @Loki`; after any manifest scope change: **api.slack.com → your app → Install App → Reinstall** |
| Image analysis / file upload does nothing | `files:read` / `files:write` scopes missing (upgraded from an older version) | **api.slack.com → your app → Install App → Reinstall** to grant the new scopes |
| Guest gets "outside the shared scope" | the path isn't in the guest allowlist | add the folder to `<WORK_DIR>\loki\loki.md` under `## Allowed paths` (applies immediately) |
| Guests get no reply in one channel | channel was `!block`ed | DM the bot `!unblock <channel_id>` |
| Replies stop mid-conversation / duplicated | **two Loki processes on the same app** — Socket Mode splits events between connections | keep exactly one instance per Slack app |
| `⏱️ Timed out` | request bigger than `TIMEOUT_SEC` | raise `TIMEOUT_SEC` or split the ask |
| Subscription limit message | your Claude plan's rolling window is exhausted | wait for the reset; consider `CLAUDE_MODEL=sonnet` |
| Korean/emoji garbled in logs | legacy console codepage | already handled (UTF-8 forced); report if you still see it |
