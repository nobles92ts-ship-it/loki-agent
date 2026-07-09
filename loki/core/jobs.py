"""Serial job queue — one claude at a time, platform-agnostic."""
from __future__ import annotations

import queue
import threading
from typing import Callable

from .config import log

JOBS: "queue.Queue[dict]" = queue.Queue()

# conversation key (e.g. thread ts) -> claude session_id, for --resume continuity
sessions: dict[str, str] = {}
sess_lock = threading.Lock()


def start(handler: Callable[[dict], None],
          on_error: Callable[[dict, Exception], None]) -> None:
    """Start the serial worker loop in a daemon thread."""
    def _loop() -> None:
        while True:
            job = JOBS.get()
            try:
                handler(job)
            except Exception as e:                # noqa: BLE001 — report, keep serving
                log.exception("job crashed")
                try:
                    on_error(job, e)
                except Exception:
                    log.exception("on_error failed")
            finally:
                JOBS.task_done()

    threading.Thread(target=_loop, daemon=True).start()
