"""Which `!names` are already taken — asked of the live dispatch, not a list.

An alias is matched **last**, after private commands, checklists, the built-in
vocabulary and plugins (see the adapter's `_dispatch`). So an alias whose name
any of those also answers to is dead on arrival: it saves, it lists, and it
never fires.

:mod:`loki.core.alias` used to guard that with a hardcoded ``RESERVED`` set,
which covered the shipped commands and nothing else — a fork's own commands and
anything in ``plugins/`` (the supported extension point) were invisible to it,
so ``!alias add tc …`` was accepted against a live ``!tc``. A hand-maintained
list of what the code dispatches drifts from what the code dispatches.

So don't maintain one: **ask the patterns themselves**. Every dispatcher already
owns a compiled regex; this module finds them in the loaded ``loki.*`` modules
(plus plugin modules, which importlib names ``loki_plugin_*``) and probes each
one with the candidate command. Anything matching is a real shadow, whether it
shipped with Loki, arrived in a plugin, or exists only in someone's fork.

``RESERVED`` stays for the opposite job — names deliberately held for commands
that *don't exist yet*, which no live pattern can possibly report.
"""
from __future__ import annotations

import re
import sys

# Modules whose patterns must not be probed: `alias` owns the catch-all
# `!<anything>` matcher, so scanning it would report every name as taken.
_SKIP_MODULES = {"loki.core.alias", "loki.core.registry"}
_PLUGIN_PREFIX = "loki_plugin_"


def _sources() -> list[tuple[str, re.Pattern]]:
    """(label, pattern) for every command matcher currently loaded.

    Only patterns anchored at ``^!`` are considered — that is the shape of a
    command matcher, and it keeps the id/mention regexes living beside them out
    of the probe.
    """
    found: list[tuple[str, re.Pattern]] = []
    # Plugins first, and from the loader rather than sys.modules: `plugins.load`
    # execs each file without registering the module, so a scan would miss every
    # real plugin — the half of the problem that made this module necessary.
    try:
        from . import plugins
        found += [(p["name"], p["re"]) for p in plugins.load()]
    except Exception:
        pass
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or mod_name in _SKIP_MODULES:
            continue
        if not (mod_name.startswith("loki.") or mod_name.startswith(_PLUGIN_PREFIX)):
            continue
        label = (mod_name[len(_PLUGIN_PREFIX):] if mod_name.startswith(_PLUGIN_PREFIX)
                 else mod_name.rsplit(".", 1)[-1])
        try:
            members = list(vars(mod).items())
        except Exception:
            continue
        for _, value in members:
            if isinstance(value, re.Pattern) and str(value.pattern).startswith("^!"):
                found.append((label, value))
    return found


def shadowed_by(name: str) -> str | None:
    """Which dispatcher swallows ``!name`` before an alias could fire?

    Returns the module that owns the matcher, or None when the name is free.
    Both a bare call and a call with arguments are probed, because plenty of
    commands only match when something follows (``^!block\\s+(\\S+)$``).
    """
    name = (name or "").strip().lstrip("!")
    if not name:
        return None
    probes = (f"!{name}", f"!{name} x")
    for label, pattern in _sources():
        try:
            if any(pattern.match(p) for p in probes):
                return label
        except Exception:                   # a plugin's regex, not ours to trust
            continue
    # A plugin can be granted by NAME (`!org allow <org> <name>`) even when its
    # MATCH is shaped differently, so the declared names count as taken too.
    try:
        from . import plugins
        if name.lower() in {n for n, _, _ in plugins.listing()}:
            return "plugins"
    except Exception:
        pass
    return None


def taken(name: str) -> bool:
    return shadowed_by(name) is not None
