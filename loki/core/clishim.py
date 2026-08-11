"""``loki`` as a word you can type anywhere — ``python -m loki cli install``.

``python -m loki chat`` works from the repo with the venv active, which is two
conditions too many for something meant to be reached mid-thought. This writes
a small launcher onto ``PATH`` that pins both: the interpreter (the repo's venv,
resolved now and baked in, so no activation) and the working directory (the
repo, so ``-m loki`` resolves).

A launcher, not a copy. It is three lines that exec the real thing, so pulling
the repo updates the command — there is no second version to forget about.

Two files per platform because Windows terminals disagree: ``loki.cmd`` for
cmd.exe and PowerShell, and an extensionless ``loki`` shell script for Git Bash
and WSL, which ignore ``.cmd``. Both are written everywhere; the wrong one is
inert.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from . import config

NAME = "loki"
REPO = config.BASE

# Where to put it, best first. These are the user-writable directories that are
# already on PATH on a normal install — npm's is there for anyone who has the
# agent CLIs Loki spawns, which is everyone this feature is for.
def _candidates() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        return [p for p in (Path(appdata) / "npm" if appdata else None,
                            home / ".local" / "bin") if p]
    return [home / ".local" / "bin", Path("/usr/local/bin")]


def _on_path(d: Path) -> bool:
    want = str(d).rstrip("\\/").lower()
    return any(str(Path(p)).rstrip("\\/").lower() == want
               for p in os.environ.get("PATH", "").split(os.pathsep) if p)


def target_dir() -> tuple[Path, bool]:
    """(directory, already_on_PATH). Prefers a PATH directory that exists."""
    cands = _candidates()
    for d in cands:
        if d.is_dir() and _on_path(d):
            return d, True
    for d in cands:
        if d.is_dir():
            return d, False
    return cands[0], _on_path(cands[0])


def python() -> str:
    """The interpreter to bake in — this venv's, not whatever runs later.

    ``sys.executable`` is a ``pythonw.exe`` when the worker was started windowed,
    and a windowed interpreter has no console to print into. The console command
    would produce silence, which is a hard failure to diagnose, so swap it back.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def _write(path: Path, body: str, executable: bool) -> None:
    path.write_text(body, encoding="utf-8", newline="")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                   | stat.S_IXOTH)


def paths(d: Path | None = None) -> list[Path]:
    d = d or target_dir()[0]
    return [d / f"{NAME}.cmd", d / NAME]


def install() -> int:
    d, on_path = target_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        # The launcher runs from the repo so `-m loki` resolves regardless of
        # where the user happens to be standing when they type it.
        _write(d / f"{NAME}.cmd",
               f'@echo off\r\ncd /d "{REPO}"\r\n"{python()}" -m loki %*\r\n',
               executable=False)
        _write(d / NAME,
               f'#!/bin/sh\ncd "{REPO}" || exit 1\nexec "{python()}" -m loki "$@"\n',
               executable=True)
    except OSError as e:
        print(f"[loki] could not write the launcher into {d}: {e}",
              file=sys.stderr)
        return 1
    print(f"[loki] installed → {d / (NAME + '.cmd')}")
    print(f"[loki]             {d / NAME}")
    print(f"[loki] python    → {python()}")
    if on_path:
        print("[loki] `loki chat` works from any terminal "
              "(open a new one if this is the first install).")
    else:
        print(f"[loki] ⚠️ {d} is not on PATH — add it, or call the file directly.")
    return 0


def uninstall() -> int:
    removed = 0
    for p in paths():
        try:
            p.unlink()
            removed += 1
            print(f"[loki] removed {p}")
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[loki] could not remove {p}: {e}", file=sys.stderr)
            return 1
    if not removed:
        print("[loki] nothing installed.")
    return 0


def status() -> int:
    d, on_path = target_dir()
    found = [p for p in paths(d) if p.exists()]
    print(f"[loki] launcher dir : {d} ({'on PATH' if on_path else 'NOT on PATH'})")
    print(f"[loki] installed    : {', '.join(p.name for p in found) or 'no'}")
    print(f"[loki] would run    : {python()} -m loki  (cwd {REPO})")
    return 0 if found else 1
