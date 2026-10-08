"""Which brain answers — and the one rule that overrides the choice.

Loki's premise is that the agent already logged in on this machine is the
cheapest one to use: a subscription you are paying for anyway, spawned as a
subprocess, rather than tokens billed per request. That premise is not specific
to Claude. A ChatGPT plan drives ``codex``, a Google account drives ``gemini``,
and both are sitting on the same disk. This package makes that a setting
instead of a rewrite.

**The rule.** Loki's guest scope and its permission-file guard are Claude Code
deny rules — a per-request settings file the tool layer enforces. Only
providers that can carry one may serve a request that needs one, and the rest
of Loki decides that a request needs one by passing ``settings_file``. So
:func:`run` sends any such request to Claude no matter what the switch says.
In practice: your own DM and the terminal console follow the switch; guests,
and you in a shared channel, always get the sandboxed provider. A provider
switch must not be able to quietly widen what a stranger can read.

The one exception narrows rather than widens: with ``LOKI_RESTRICTED_MODE=
sealed``, a provider that declares ``SEALED`` serves those requests itself with
no tools at all (:func:`sealable`). Nothing is readable, so nothing is wider.

Switching does **not** clear remembered conversations, because it does not have
to. A session id belongs to the provider that issued it, so :mod:`loki.core.
sessions` files them under the provider's name and a switch simply looks at a
different drawer — flip back and yesterday's thread is still there.

One quirk falls out of that and is deliberately left alone. A conversation that
always falls back to Claude — a channel, a guest — has its Claude session ids
filed under whichever provider is *selected*, because that is the drawer both
the read and the write use. Reads and writes therefore agree and the thread
works; what it costs is that switching providers starts such a conversation
fresh, even though Claude answered it either way. Fixing it properly means
every caller resolving the effective provider before it reads, which is three
adapters' worth of reordering to recover one thread nobody lost data in.
"""
from __future__ import annotations

import json
import threading

from .. import allowread, config
from ..config import log
from . import antigravity, base, claude, codex, gemini, groq, kimi

# One probe per CLI per process. `!provider` and `doctor` ask every provider
# for its version *and* its readiness, and each answer is a node process — left
# uncached that is ten cold starts to print six lines.
_versions: dict[str, str] = {}

# Order matters only for display — `!provider` lists them in this order.
ALL = {m.NAME: m for m in (claude, antigravity, codex, kimi, gemini, groq)}
DEFAULT = claude.NAME
FALLBACK = claude                       # the provider that can hold a sandbox

STATE_FILE = config.STATE / "provider.json"
_lock = threading.Lock()

tree_kill = base.tree_kill              # one killer, whoever spawned


def _probe_version(cmd: str, args: list[str]) -> str:
    """Ask a CLI its version. "?" means "could not run it at all".

    Cached for the life of the process. ``!provider`` and ``doctor`` ask every
    provider for both its version and its readiness, and readiness is *defined*
    as "did it answer --version" — so an uncached probe is two node cold starts
    per provider to print six lines of status.

    Spawned through :func:`base.spawn` so every child of Loki is launched the
    same way: explicit stdin, its own process group, no console window. These
    are ``.cmd`` shims on Windows, and a probe that spawns them differently
    from a real run is a probe that can disagree with one.
    """
    if cmd in _versions:
        return _versions[cmd]
    try:
        out, _err, rc, expired = base.spawn([cmd, *args], "", config.WORK_DIR,
                                            base.clean_env(), None, timeout=30)
        lines = (out or "").strip().splitlines()
        version = lines[0].strip() if lines and not expired and rc == 0 else "?"
    except Exception:
        version = "?"
    _versions[cmd] = version
    return version


# ─────────────────────────── the choice ───────────────────────────
def configured() -> str:
    """The name in .env — the setting a restart goes back to."""
    name = (config.PROVIDER or "").strip().lower()
    return name if name in ALL else DEFAULT


def name() -> str:
    """The provider answering right now: the runtime switch, else the setting."""
    with _lock:
        try:
            chosen = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("name")
        except Exception:
            chosen = None
    return chosen if chosen in ALL else configured()


def current():
    return ALL[name()]


def set_current(new: str) -> bool:
    """Switch. False when the name is unknown or already selected."""
    new = (new or "").strip().lower()
    if new not in ALL or new == name():
        return False
    with _lock:
        try:
            STATE_FILE.write_text(json.dumps({"name": new}), encoding="utf-8")
        except Exception:
            log.exception("provider.json write failed")
            return False
    log.info("provider switched to %s", new)
    return True


def get(spec: str | None):
    """A provider by name, or the current one when ``spec`` is empty/unknown."""
    return ALL.get((spec or "").strip().lower()) or current()


# ─────────────────────────── running a turn ───────────────────────────
def sealable(mod) -> bool:
    """May a restricted request run on ``mod`` with every tool taken away?

    Only when the owner opted in (``LOKI_RESTRICTED_MODE=sealed``) *and* the
    provider has a measured tool-less mode. The model cannot read directly;
    Slack guest grants can be supplied by Loki's capped host-side reader.
    """
    return config.RESTRICTED_MODE == "sealed" and bool(getattr(mod, "SEALED", False))


def run(prompt: str, resume_id: str | None,
        permission_mode: str | None = None,
        settings_file: str | None = None,
        cwd: str | None = None,
        job: dict | None = None,
        provider: str | None = None,
        read_roots: list | None = None) -> dict:
    """One turn on the chosen provider, sealed Codex, or Claude fallback.

    ``read_roots``: the folders a guest's manifest grants. Claude enforces those
    through ``settings_file`` and ignores this; a sealed run reads them through
    :mod:`loki.core.allowread` instead.

    Returns the contract every caller already expects:
    ``{text, session_id, error, reason, provider}``.
    """
    mode = permission_mode or config.PERMISSION_MODE
    mod = get(provider)
    if settings_file and not getattr(mod, "SANDBOX", False):
        if sealable(mod):
            log.info("provider %s cannot hold a sandbox — this request runs "
                     "sealed (no tools)", mod.NAME)
            if read_roots:
                return allowread.run(mod, prompt, resume_id, read_roots, job=job)
            return mod.run_sealed(prompt, resume_id, cwd=cwd, job=job)
        log.info("provider %s cannot hold a sandbox — this request runs on %s",
                 mod.NAME, FALLBACK.NAME)
        mod = FALLBACK
    return mod.run(prompt, resume_id, mode, settings_file=settings_file,
                   cwd=cwd, job=job)


# ─────────────────────────── introspection ───────────────────────────
def describe(mod) -> dict:
    ok, note = mod.available()
    return {"name": mod.NAME, "label": mod.LABEL, "plan": mod.PLAN,
            "sandbox": bool(getattr(mod, "SANDBOX", False)),
            "ready": ok, "note": note, "version": mod.version(),
            "current": mod.NAME == name()}


def listing() -> list[dict]:
    return [describe(mod) for mod in ALL.values()]
