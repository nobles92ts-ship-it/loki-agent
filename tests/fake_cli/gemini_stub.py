"""A stand-in for the Gemini CLI, faithful to the parts Loki depends on.

Loki's Gemini support cannot be exercised on a machine that is not signed in to
a Google account, and signing in is a browser flow — which would leave the
whole provider covered only by unit tests of its own argument parsing. This is
the other half: a real executable, spawned the real way, speaking the real
contract, so the integration is tested even when the account is not available.

What it reproduces, all of it observed from the actual CLI rather than assumed:

* the prompt arrives on **stdin**, with no ``-p``
* success prints ``{"response", "session_id", "stats"}`` on **stdout**
* failure prints ``{"session_id", "error": {type, message, code}}`` on
  **stderr** and exits non-zero — the asymmetry that makes a stdout-only
  parser report every error as an empty answer
* ``--session-id`` names a new conversation, ``--resume`` continues one
* resuming an id it has never seen fails the way the real one does, so the
  retry path has something to retry against

Behaviour is steered by ``FAKE_GEMINI_MODE``: ``echo`` (default), ``quota``,
``authfail``. Transcripts go under ``FAKE_GEMINI_STATE`` so a test can assert
that a resumed turn actually saw the earlier one.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _fail(session_id: str, message: str, code: int) -> int:
    print(json.dumps({"session_id": session_id,
                      "error": {"type": "Error", "message": message,
                                "code": code}}), file=sys.stderr)
    return code


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("0.54.4")
        return 0
    mode = os.environ.get("FAKE_GEMINI_MODE", "echo")
    state = Path(os.environ.get("FAKE_GEMINI_STATE") or ".")
    state.mkdir(parents=True, exist_ok=True)

    prompt = sys.stdin.read()
    resume = _flag(argv, "--resume")
    sid = resume or _flag(argv, "--session-id") or str(uuid.uuid4())
    store = state / f"{sid}.json"

    if mode == "authfail":
        return _fail(sid, "Please set an Auth method in your settings.json or "
                          "specify one of the following environment variables "
                          "before running: GEMINI_API_KEY, "
                          "GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA", 41)
    if mode == "quota":
        return _fail(sid, "Resource exhausted: quota exceeded", 429)

    if resume and not store.exists():
        return _fail(sid, f'Invalid session identifier "{resume}". '
                          "Use --list-sessions to see available sessions.", 1)

    turns = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
    turns.append(prompt)
    store.write_text(json.dumps(turns, ensure_ascii=False), encoding="utf-8")

    # Echo enough for a test to prove both that the prompt arrived intact and
    # that a resumed turn can see the earlier ones.
    print(json.dumps({
        "response": f"turn {len(turns)} of {sid}: {prompt.strip()[:120]}",
        "session_id": sid,
        "stats": {"turns": len(turns)},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
