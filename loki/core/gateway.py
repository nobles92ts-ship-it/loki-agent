"""Keeping the worker up — autostart, watchdog, stop/restart.

Supervision is the OS's job, not ours: a second Python process watching the
first is one more thing that can die quietly. So ``install`` registers Loki
with whatever the platform already has — a scheduled task, a systemd user
unit, a launchd agent — and gets out of the way.

The one piece we do own is :func:`ensure`: *start the worker if it isn't
alive*. It reads the heartbeat (:mod:`loki.core.health`) and does nothing when
things are fine, so it is safe to run on a timer. That's what makes crash
recovery work on Windows, where the task scheduler can start a job but won't
restart a crashed one.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, health
from .config import log

TASK_MAIN = "Loki_Agent"
TASK_WATCHDOG = "Loki_Agent_Watchdog"
WATCHDOG_MINUTES = 5

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


# ─────────────────────────── launching ───────────────────────────
def _python(windowless: bool = True) -> str:
    """The interpreter to relaunch with — pythonw on Windows so no console
    flashes on every watchdog tick."""
    exe = Path(sys.executable)
    if IS_WIN and windowless:
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def spawn() -> int:
    """Start a detached worker. Returns its pid."""
    kw: dict = {"cwd": str(config.BASE)}
    if IS_WIN:
        kw["creationflags"] = (subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NO_WINDOW)
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([_python(), "-m", "loki"],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kw)
    log.info("gateway spawned worker pid=%s", p.pid)
    return p.pid


def ensure() -> int:
    """Start the worker only if it isn't alive. Safe to run on a timer.

    Exit 0 either way — a watchdog that reports failure for "nothing to do"
    trains you to ignore it.
    """
    s = health.snapshot()
    if s["alive"]:
        print(f"[OK] worker alive (pid {s['pid']}) — nothing to do")
        return 0
    reason = s.get("reason", "unknown")
    pid = spawn()
    print(f"[..] worker was down ({reason}) — started pid {pid}")
    log.warning("watchdog restarted worker (was: %s)", reason)
    return 0


def stop() -> int:
    """Stop the running worker."""
    s = health.snapshot()
    pid = s.get("pid")
    if not s["known"] or not health.pid_running(pid):
        print("[OK] worker is not running")
        health.clear()
        return 0
    if pid == os.getpid():                     # paranoia: never self-terminate
        print("[X ] refusing to stop myself")
        return 1
    try:
        from . import brain
        brain.tree_kill(pid)                   # takes the claude children too
    except Exception:
        log.exception("tree_kill failed, falling back to terminate")
        try:
            os.kill(int(pid), 9)
        except Exception:
            log.exception("kill failed")
            print(f"[X ] could not stop pid {pid}")
            return 1
    for _ in range(20):                        # give it up to ~2s to go
        if not health.pid_running(pid):
            break
        time.sleep(0.1)
    health.clear()          # so the watchdog sees "stopped", not "stale"
    print(f"[OK] stopped pid {pid}")
    return 0


def restart() -> int:
    stop()
    time.sleep(1.0)                            # let the socket close cleanly
    pid = spawn()
    print(f"[OK] started pid {pid}")
    return 0


# ─────────────────────────── OS registration ───────────────────────────
def install() -> int:
    if IS_WIN:
        return _install_windows()
    if IS_MAC:
        return _install_launchd()
    return _install_systemd()


def uninstall() -> int:
    if IS_WIN:
        return _uninstall_windows()
    if IS_MAC:
        return _uninstall_launchd()
    return _uninstall_systemd()


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=config.NO_WINDOW if IS_WIN else 0)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"


# ── Windows: two scheduled tasks (start at logon + watchdog every 5 min) ──
def _vbs(name: str, args: str, windowless: bool) -> Path:
    """A tiny launcher so the task starts in the repo root with no console."""
    path = config.STATE / name
    path.write_text(
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{config.BASE}"\r\n'
        f'sh.Run """{_python(windowless)}"" {args}", 0, False\r\n',
        encoding="utf-8")
    return path


def _startup_link() -> Path:
    """The per-user Startup folder entry — no elevation required, unlike a
    scheduled task with an ONLOGON trigger."""
    appdata = os.environ.get("APPDATA", "")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" /
            "Programs" / "Startup" / "Loki_Agent.vbs")


def _install_windows() -> int:
    """Autostart via the Startup folder, crash recovery via a repeating task.

    Deliberately avoids ``/SC ONLOGON``: that trigger needs administrator
    rights, and asking a chat bot's setup to run elevated is a bad trade for
    something the Startup folder does for free.
    """
    start_vbs = _vbs("loki_start.vbs", "-m loki", True)
    ensure_vbs = _vbs("loki_ensure.vbs", "-m loki gateway ensure", True)

    ok = True
    try:
        link = _startup_link()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text(start_vbs.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[OK] '{link.name}' in Startup — starts Loki at login")
    except Exception as e:                                  # noqa: BLE001
        print(f"[X ] could not write the Startup entry: {e}")
        ok = False

    rc, out = _run(["schtasks", "/Create", "/TN", TASK_WATCHDOG, "/SC",
                    "MINUTE", "/MO", str(WATCHDOG_MINUTES),
                    "/TR", f'wscript.exe "{ensure_vbs}"', "/F"])
    if rc:
        print(f"[X ] watchdog task failed: {out}")
        ok = False
    else:
        print(f"[OK] '{TASK_WATCHDOG}' — checks every {WATCHDOG_MINUTES} min "
              "and restarts Loki if it died")

    print("     remove with:  python -m loki gateway uninstall")
    return 0 if ok else 1


def _uninstall_windows() -> int:
    rc, out = _run(["schtasks", "/Delete", "/TN", TASK_WATCHDOG, "/F"])
    print(f"[OK] removed '{TASK_WATCHDOG}'" if rc == 0
          else f"[i ] '{TASK_WATCHDOG}': {out}")
    # TASK_MAIN existed in an earlier cut that used an ONLOGON trigger; remove
    # it too so upgrading installs don't leave a stray task behind.
    _run(["schtasks", "/Delete", "/TN", TASK_MAIN, "/F"])
    try:
        _startup_link().unlink(missing_ok=True)
        print("[OK] removed the Startup entry")
    except Exception as e:                                  # noqa: BLE001
        print(f"[i ] Startup entry: {e}")
    return 0


# ── Linux: systemd user unit (Restart=on-failure does the watchdog) ──
def _systemd_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "loki.service"


def _install_systemd() -> int:
    unit = _systemd_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"""[Unit]
Description=Loki agent
After=network-online.target

[Service]
WorkingDirectory={config.BASE}
ExecStart={_python(False)} -m loki
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
""", encoding="utf-8")
    rc, out = _run(["systemctl", "--user", "daemon-reload"])
    if rc:
        print(f"[i ] wrote {unit}\n[X ] systemctl unavailable: {out}")
        return 1
    rc, out = _run(["systemctl", "--user", "enable", "--now", "loki"])
    if rc:
        print(f"[i ] wrote {unit}\n[X ] enable failed: {out}")
        return 1
    print(f"[OK] {unit} — enabled, restarts on failure")
    print("     logs:  journalctl --user -u loki -f")
    return 0


def _uninstall_systemd() -> int:
    _run(["systemctl", "--user", "disable", "--now", "loki"])
    _systemd_path().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    print("[OK] systemd unit removed")
    return 0


# ── macOS: launchd agent (KeepAlive does the watchdog) ──
def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.loki.agent.plist"


def _install_launchd() -> int:
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.loki.agent</string>
  <key>ProgramArguments</key>
  <array><string>{_python(False)}</string><string>-m</string><string>loki</string></array>
  <key>WorkingDirectory</key><string>{config.BASE}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
""", encoding="utf-8")
    _run(["launchctl", "unload", str(plist)])          # ignore if not loaded
    rc, out = _run(["launchctl", "load", str(plist)])
    if rc:
        print(f"[i ] wrote {plist}\n[X ] launchctl load failed: {out}")
        return 1
    print(f"[OK] {plist} — loaded, restarts on exit")
    return 0


def _uninstall_launchd() -> int:
    plist = _plist_path()
    _run(["launchctl", "unload", str(plist)])
    plist.unlink(missing_ok=True)
    print("[OK] launchd agent removed")
    return 0
