"""A stand-in for the Antigravity CLI, faithful to the parts Loki depends on.

Every shape below was copied from the real `agy` 1.1.12, not assumed:

* the prompt arrives on **stdin**, with no ``-p``
* one JSON object on **stdout**, success and failure alike:
  ``{conversation_id, status, response, usage, duration_seconds, error?}``
* ``--conversation <id>`` resumes, and a **dead id is not an error** — the real
  CLI warns on stderr and silently starts a fresh conversation with a new id,
  which is why Loki has no retry path here and must store whatever came back

``FAKE_AGY_MODE``: ``echo`` (default), ``quota``, ``badmodel``.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("1.1.12")
        return 0

    mode = os.environ.get("FAKE_AGY_MODE", "echo")
    state = Path(os.environ.get("FAKE_AGY_STATE") or ".")
    state.mkdir(parents=True, exist_ok=True)
    prompt = sys.stdin.read()

    if mode == "badmodel":
        print(json.dumps({"conversation_id": "", "status": "ERROR",
                          "response": "",
                          "error": "invalid model selection (--model \"x\"): "
                                   "model x is not recognized"}))
        return 1
    if mode == "quota":
        print(json.dumps({"conversation_id": "", "status": "ERROR",
                          "response": "",
                          "error": "429 quota exceeded for this plan"}))
        return 1

    asked = argv[argv.index("--conversation") + 1] \
        if "--conversation" in argv else None
    sid = asked
    if asked and not (state / f"{asked}.json").exists():
        # what the real CLI does: warn, then carry on as a new conversation
        print(f'warning: conversation "{asked}" not found', file=sys.stderr)
        sid = None
    sid = sid or str(uuid.uuid4())

    store = state / f"{sid}.json"
    turns = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
    turns.append(prompt)
    store.write_text(json.dumps(turns, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({
        "conversation_id": sid,
        "status": "SUCCESS",
        "response": f"turn {len(turns)} of {sid}: {prompt.strip()[:120]}",
        "duration_seconds": 0.1,
        "num_turns": len(turns),
        "usage": {"total_tokens": 10},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
