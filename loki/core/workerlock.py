"""One worker per platform — the lock every launcher runs into.

A worker can be started from several places: the Startup-folder entry, the
watchdog's ``gateway ensure``, ``run_worker.vbs``, a terminal. None of them
knows about the others, and a second worker on the same app token is not a
spare: the platform splits events between the two connections, so Loki looks
like it randomly ignores messages.

Each launcher checking first would still leave a gap between the check and
the start. So the check lives in the one place every launcher passes through:
the worker takes an OS file lock in ``state/`` before it connects, and a
second worker finds it held and exits. The OS drops the lock when its holder
dies, however it dies, so a crash never leaves a lock behind that blocks the
next start.

The lock is per platform, because one Slack worker and one Discord worker side
by side is a supported setup. It is per install too, because it lives in
``state/``: two separate copies of Loki on one app token still split events.
"""
from __future__ import annotations

import os

from . import config

_DIR = config.STATE
# The pid sits at the start of the file and the locked byte far past it.
# Windows locks are mandatory: a read whose buffer reaches the locked byte
# fails, even past the end of the file, and `type` or `Get-Content` ask for
# 4 KB at a time. A lock past the end of the file takes no disk space.
_LOCK_AT = 1 << 30
_held: dict[str, int] = {}          # platform -> fd, open for the process's life


def _path(platform: str):
    return _DIR / f"worker_{platform}.lock"


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _LOCK_AT, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def claim(platform: str) -> bool:
    """Take this platform's worker lock and hold it until the process exits.

    False means another worker already holds it.
    """
    fd = os.open(_path(platform), os.O_RDWR | os.O_CREAT, 0o644)
    if not _try_lock(fd):
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, str(os.getpid()).encode("ascii"))
    _held[platform] = fd            # never closed: closing it would let go
    return True


def holder(platform: str) -> int | None:
    """The pid of the worker holding the lock, or None when no worker does
    (or when the holder took it a moment ago and hasn't written its pid yet).

    Asks the lock, not the pid in the file. The file outlives the worker that
    wrote it, and after a reboot its pid can belong to any other process, so a
    pid read from a lock nobody holds means nothing.
    """
    try:
        fd = os.open(_path(platform), os.O_RDWR)
    except FileNotFoundError:
        return None
    try:
        if _try_lock(fd):
            _unlock(fd)
            return None
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 32).decode("ascii", "replace").strip()
        return int(raw) if raw.isdigit() else None
    finally:
        os.close(fd)
