#!/usr/bin/env bash
# Loki setup wizard (macOS/Linux) — venv + dependencies + .env + connection test.
#   ./setup.sh            interactive setup
# For background running / autostart see docs/SETUP.md (systemd + launchd).
set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "=============================================="
echo "  Loki setup — your Claude Code, on Slack"
echo "=============================================="
echo ""

# ── 1. prerequisites ─────────────────────────────────────────────────────────
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[X] Python not found. Install Python 3.10+ first: https://www.python.org/downloads/"
    exit 1
fi
echo "[OK] $("$PY" --version)"
if ! command -v claude >/dev/null 2>&1; then
    echo "[X] 'claude' not found on PATH."
    echo "    Install Claude Code and log in first: https://claude.com/claude-code"
    echo "    (Or set CLAUDE_CMD in .env to its full path later.)"
    exit 1
fi
echo "[OK] Claude Code CLI: $(claude --version)"

# ── 2. venv + dependencies ───────────────────────────────────────────────────
[ -d venv ] || { echo "[..] Creating venv…"; "$PY" -m venv venv; }
echo "[..] Installing dependencies…"
./venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo "[OK] Dependencies installed"

# ── 3. .env wizard ───────────────────────────────────────────────────────────
write_env=1
if [ -f .env ]; then
    read -r -p "[?] .env already exists. Overwrite it? (y/N) " ans
    case "$ans" in y|Y) ;; *) write_env=0; echo "[OK] Keeping existing .env" ;; esac
fi
if [ "$write_env" = 1 ]; then
    echo ""
    echo "Paste the two tokens from your Slack app (api.slack.com/apps):"
    while :; do read -r -p "  Bot User OAuth Token (xoxb-...): " bot
        case "$bot" in xoxb-*) break ;; esac; done
    while :; do read -r -p "  App-Level Token      (xapp-...): " apptok
        case "$apptok" in xapp-*) break ;; esac; done
    echo ""
    echo "Your Slack member ID (Slack profile -> ... -> Copy member ID):"
    while :; do read -r -p "  ALLOWED_USER_ID (U...): " owner
        case "$owner" in U*|W*) break ;; esac; done
    echo ""
    while :; do read -r -p "  WORK_DIR - the folder Claude works in (e.g. $HOME/work): " workdir
        [ -n "$workdir" ] && [ -d "$workdir" ] && break
        echo "    not a folder — try again"; done
    read -r -p "  Bot message language en/ko (default: en): " lang
    case "$lang" in en|ko) ;; *) lang=en ;; esac
    echo ""
    echo "Permission mode:"
    echo "  plan              = read-only (safe default)"
    echo "  bypassPermissions = FULL write/execute on this machine via Slack - read docs/SECURITY.md first"
    read -r -p "  Enable full write mode? (y/N) " mode
    permission=plan
    case "$mode" in y|Y) permission=bypassPermissions ;; esac
    echo ""
    echo "Dedicated Claude account (optional):"
    echo "  By default Loki uses whatever account 'claude' is logged into."
    echo "  To give Loki its OWN account (e.g. keep work and personal separate),"
    echo "  enter a config dir here, then log into it ONCE after setup:"
    echo "     CLAUDE_CONFIG_DIR=<that dir> claude   (run /login inside)"
    read -r -p "  CLAUDE_CONFIG_DIR (blank = use the default account): " acctdir
    echo ""
    echo "Guest access (channels):"
    echo "  Anyone in a channel Loki joins can query it - read-only, and ONLY within"
    echo "  paths you list in <WORK_DIR>/loki/loki.md (created empty on first run;"
    echo "  empty list = guests see nothing). Silence a channel: DM '!block <channel_id>'."
    read -r -p "  Max guest requests per hour, 0 = unlimited (default 10): " rate
    case "$rate" in ''|*[!0-9]*) rate=10 ;; esac
    {
        printf '%s\n' \
            "SLACK_BOT_TOKEN=$bot" \
            "SLACK_APP_TOKEN=$apptok" \
            "ALLOWED_USER_ID=$owner" \
            "WORK_DIR=$workdir" \
            "CLAUDE_PERMISSION_MODE=$permission" \
            "GUEST_RATE_PER_HOUR=$rate" \
            "LOKI_LANG=$lang"
        [ -n "$acctdir" ] && printf 'CLAUDE_CONFIG_DIR=%s\n' "$acctdir"
    } > .env
    echo "[OK] .env written"
fi

# ── 4. Slack connection smoke test ───────────────────────────────────────────
echo "[..] Testing Slack auth…"
if ! ./venv/bin/python tools/diag.py; then
    echo "[X] Diagnostics failed - fix the items above and re-run ./setup.sh"
    exit 1
fi

echo ""
echo "Done. Start Loki with:"
echo "    ./venv/bin/python -m loki"
echo "Keep it running in the background:"
echo "    nohup ./venv/bin/python -m loki >/dev/null 2>&1 &"
echo "(systemd / launchd autostart examples: docs/SETUP.md)"
echo "Then DM your bot in Slack: hello"
echo ""
