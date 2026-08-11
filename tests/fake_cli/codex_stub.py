"""A stand-in for the Codex CLI, faithful to the parts Loki depends on.

The real one is installed on the development machine but its sign-in has
expired, and re-authenticating is a browser flow. So the shapes below were
taken from the real CLI's ``--help`` and from one live run that got far enough
to fail, and this reproduces them:

* events stream as **JSONL on stdout** — ``thread.started`` carrying the
  conversation id, then ``turn.started``, then either ``item.completed`` or an
  ``error`` / ``turn.failed`` pair
* the final answer is **not** in the stream. It is written to the file named by
  ``--output-last-message``, which is the handoff this stub exists to exercise:
  Loki creates the temp file, the child writes it, Loki reads and unlinks it
* ``exec resume <id>`` continues, and takes neither ``--sandbox`` nor ``--cd``

``FAKE_CODEX_MODE``: ``echo`` (default), ``reauth``, ``quota``.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main() -> int:
    argv = sys.argv[1:]
    mode = os.environ.get("FAKE_CODEX_MODE", "echo")
    state = Path(os.environ.get("FAKE_CODEX_STATE") or ".")
    state.mkdir(parents=True, exist_ok=True)

    resume = argv[2] if argv[:2] == ["exec", "resume"] else None
    sid = resume or str(uuid.uuid4())
    last = argv[argv.index("--output-last-message") + 1] \
        if "--output-last-message" in argv else None
    prompt = sys.stdin.read()

    emit({"type": "thread.started", "thread_id": sid})
    emit({"type": "turn.started"})

    if mode == "reauth":
        msg = ("Your access token could not be refreshed because your refresh "
               "token was already used. Please log out and sign in again.")
        emit({"type": "error", "message": msg})
        emit({"type": "turn.failed", "error": {"message": msg}})
        return 1
    if mode == "quota":
        msg = "429 Too Many Requests: rate limit reached"
        emit({"type": "turn.failed", "error": {"message": msg}})
        return 1

    store = state / f"{sid}.json"
    turns = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
    turns.append(prompt)
    store.write_text(json.dumps(turns, ensure_ascii=False), encoding="utf-8")

    answer = f"turn {len(turns)} of {sid}: {prompt.strip()[:120]}"
    emit({"type": "item.completed",
          "item": {"type": "agent_message", "text": answer}})
    emit({"type": "turn.completed"})
    if last:
        Path(last).write_text(answer, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
