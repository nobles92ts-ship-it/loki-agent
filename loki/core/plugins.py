"""Custom `!commands` — one file per command, dropped into ``plugins/``.

The built-in vocabulary lives in :mod:`loki.core.commands`. This is the other
half: the commands *your* team needs, which have no business being in a shared
repo. A plugin is a plain module:

    # plugins/deploy.py
    MATCH = r"^!deploy\\s+(\\S+)$"
    HELP  = "!deploy <service> — kick off a deploy"

    def handle(match, ctx):
        return f"deploying {match.group(1)}…"

``handle`` gets the regex match plus the same ``ctx`` dict
:func:`loki.core.commands.handle` documents, and returns the text to post (or
None to decline and let the message fall through to normal chat).

Two rules keep this from becoming a back door:

- **Owner-only by default.** Set ``OWNER_ONLY = False`` to open a plugin up,
  and even then only orgs explicitly granted it (``!org allow <org> <name>``)
  may run it. A file appearing in a folder must never silently hand guests a
  new way to reach the machine.
- **Built-ins win.** Plugins are matched after the core commands, so no plugin
  can shadow ``!stop`` or ``!org``.

For heavy work — a pipeline that runs for an hour and streams progress — use
the platform's ``private_commands`` hook instead. It owns its own threading;
this returns one string.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from . import config, orgs
from .config import log

_loaded: list[dict] = []
_scanned = False


def plugins_dir() -> Path:
    return config.BASE / "plugins"


def _load_one(path: Path) -> dict | None:
    """Import one plugin file. A broken plugin is skipped and logged — it must
    never take the worker down with it."""
    try:
        spec = importlib.util.spec_from_file_location(f"loki_plugin_{path.stem}",
                                                      path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pattern, fn = getattr(mod, "MATCH", None), getattr(mod, "handle", None)
        if not pattern or not callable(fn):
            log.warning("plugin %s skipped — needs MATCH and handle()", path.name)
            return None
        return {
            "name": getattr(mod, "NAME", path.stem).lower(),
            "re": re.compile(pattern, re.IGNORECASE | re.DOTALL),
            "handle": fn,
            "owner_only": bool(getattr(mod, "OWNER_ONLY", True)),
            "help": getattr(mod, "HELP", ""),
        }
    except Exception:
        log.exception("plugin %s failed to load", path.name)
        return None


def load() -> list[dict]:
    """Discover plugins once, at first use."""
    global _scanned
    if _scanned:
        return _loaded
    _scanned = True
    d = plugins_dir()
    if not d.is_dir():
        return _loaded
    for path in sorted(d.glob("*.py")):
        if path.name.startswith("_"):          # _shared.py etc. are helpers
            continue
        p = _load_one(path)
        if p:
            _loaded.append(p)
    if _loaded:
        log.info("plugins loaded: %s", ", ".join(p["name"] for p in _loaded))
    return _loaded


def reload() -> int:
    """Re-scan from disk — for tests and for picking up an edit without a
    restart."""
    global _scanned
    _loaded.clear()
    _scanned = False
    return len(load())


def _permitted(plugin: dict, ctx: dict) -> bool:
    if ctx.get("is_owner"):
        return True
    if plugin["owner_only"]:
        return False
    # Opened up, but still only for orgs explicitly granted this command.
    return orgs.allows_command(ctx.get("org"), plugin["name"])


def handle(text: str, ctx: dict) -> str | None:
    """Try each plugin in name order. Returns the reply, or None if none
    matched (or the caller wasn't allowed to run the one that did)."""
    for plugin in load():
        m = plugin["re"].match((text or "").strip())
        if not m:
            continue
        if not _permitted(plugin, ctx):
            return None                        # silent: don't advertise it
        try:
            return plugin["handle"](m, ctx)
        except Exception as e:                 # noqa: BLE001 — one bad plugin
            log.exception("plugin %s raised", plugin["name"])
            return config.t("plugin_error", name=plugin["name"], e=e)
    return None


def listing() -> list[tuple[str, str, bool]]:
    """(name, help, owner_only) for `!plugins`."""
    return [(p["name"], p["help"], p["owner_only"]) for p in load()]
