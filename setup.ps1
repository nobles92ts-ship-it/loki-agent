# Loki setup wizard — venv + dependencies + .env + connection smoke test.
#   .\setup.ps1              interactive setup
#   .\setup.ps1 -Autostart   also register a hidden launcher at Windows login
# Works on Windows PowerShell 5.1 and PowerShell 7+.
param([switch]$Autostart)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

Write-Host ""
Write-Host "==============================================" -ForegroundColor DarkCyan
Write-Host "  Loki setup — your Claude Code, on Slack"      -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor DarkCyan
Write-Host ""

# ── 1. prerequisites ─────────────────────────────────────────────────────────
try { $pyExe = (Get-Command python).Source } catch {
    Write-Host "[X] Python not found on PATH. Install Python 3.10+ first: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
$pyVer = (& $pyExe --version) 2>&1
Write-Host "[OK] $pyVer  ($pyExe)"
try {
    $claudeVer = (claude --version) 2>&1
    Write-Host "[OK] Claude Code CLI: $claudeVer"
} catch {
    Write-Host "[X] 'claude' not found on PATH." -ForegroundColor Red
    Write-Host "    Install Claude Code and log in first: https://claude.com/claude-code"
    Write-Host "    (Or set CLAUDE_CMD in .env to its full path later.)"
    exit 1
}

# ── 2. venv + dependencies ───────────────────────────────────────────────────
if (-not (Test-Path "$root\venv")) {
    Write-Host "[..] Creating venv…"
    & $pyExe -m venv "$root\venv"
}
Write-Host "[..] Installing dependencies…"
& "$root\venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "$root\requirements.txt"
Write-Host "[OK] Dependencies installed"

# ── 3. .env wizard ───────────────────────────────────────────────────────────
$envPath = "$root\.env"
$writeEnv = $true
if (Test-Path $envPath) {
    $ans = Read-Host "[?] .env already exists. Overwrite it? (y/N)"
    if ($ans -notmatch '^[yY]') { $writeEnv = $false; Write-Host "[OK] Keeping existing .env" }
}
if ($writeEnv) {
    Write-Host ""
    Write-Host "Paste the two tokens from your Slack app (api.slack.com/apps):" -ForegroundColor Cyan
    do { $bot = Read-Host "  Bot User OAuth Token (xoxb-...)" } until ($bot -like 'xoxb-*')
    do { $app = Read-Host "  App-Level Token      (xapp-...)" } until ($app -like 'xapp-*')
    Write-Host ""
    Write-Host "Your Slack member ID (Slack profile -> ... -> Copy member ID):" -ForegroundColor Cyan
    do { $owner = Read-Host "  ALLOWED_USER_ID (U...)" } until ($owner -match '^[UW][A-Z0-9]{6,}$')
    Write-Host ""
    do {
        $workDir = Read-Host "  WORK_DIR - the folder Claude works in (e.g. $HOME\Documents)"
    } until ($workDir -and (Test-Path $workDir -PathType Container))
    $lang = Read-Host "  Bot message language en/ko (default: en)"
    if ($lang -notmatch '^(en|ko)$') { $lang = 'en' }
    Write-Host ""
    Write-Host "Permission mode:" -ForegroundColor Cyan
    Write-Host "  plan              = read-only (safe default)"
    Write-Host "  bypassPermissions = FULL write/execute on this PC via Slack - read docs/SECURITY.md first"
    $mode = Read-Host "  Enable full write mode? (y/N)"
    $permission = 'plan'
    if ($mode -match '^[yY]') { $permission = 'bypassPermissions' }
    Write-Host ""
    Write-Host "Guest access (channels):" -ForegroundColor Cyan
    Write-Host "  Anyone in a channel Loki joins can query it - read-only, and ONLY within"
    Write-Host "  paths you list in <WORK_DIR>\loki\loki.md (created empty on first run;"
    Write-Host "  empty list = guests see nothing). Silence a channel: DM '!block <channel_id>'."

    $envBody = @(
        "SLACK_BOT_TOKEN=$bot",
        "SLACK_APP_TOKEN=$app",
        "ALLOWED_USER_ID=$owner",
        "WORK_DIR=$workDir",
        "CLAUDE_PERMISSION_MODE=$permission",
        "LOKI_LANG=$lang"
    ) -join "`n"
    [IO.File]::WriteAllText($envPath, $envBody + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] .env written"
}

# ── 4. Slack connection smoke test ──────────────────────────────────────────
Write-Host "[..] Testing Slack auth…"
$smoke = & "$root\venv\Scripts\python.exe" "$root\tools\diag.py" 2>&1
$smoke | ForEach-Object { Write-Host "    $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] Diagnostics failed - fix the items above and re-run .\setup.ps1" -ForegroundColor Red
    exit 1
}

# ── 5. optional autostart ────────────────────────────────────────────────────
if ($Autostart) {
    $startup = [Environment]::GetFolderPath('Startup')
    $vbs = Join-Path $startup 'Loki_Agent.vbs'
    $vbsBody = "Set sh = CreateObject(""WScript.Shell"")`r`n" +
               "sh.CurrentDirectory = ""$root""`r`n" +
               "sh.Run """"""$root\venv\Scripts\pythonw.exe"""" -m loki"", 0, False`r`n"
    [IO.File]::WriteAllText($vbs, $vbsBody, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Autostart registered: $vbs"
}

Write-Host ""
Write-Host "Done. Start Loki with:" -ForegroundColor Green
Write-Host "    .\venv\Scripts\python.exe -m loki"
Write-Host "or double-click run_worker.vbs (background, no console)."
Write-Host "Then DM your bot in Slack: hello"
Write-Host ""
