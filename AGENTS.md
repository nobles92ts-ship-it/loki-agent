# AGENTS.md

Rules for coding agents (Codex, Claude Code, and others) working on this repository.

Codex and Claude Code both read this file from the repository root. Do not add a plain `CLAUDE.md` beside it: when a project `CLAUDE.md` exists, Claude Code reads that instead of this file. If Claude-only rules are ever needed, start `CLAUDE.md` with the line `@AGENTS.md`.

This file is for work on the code. Loki's own runtime does not read it unless `WORK_DIR` points inside this repository: provider runs start in `WORK_DIR` (guests in `WORK_DIR/loki`).

## What this is

Loki connects Slack, Discord, or Telegram to a coding-agent CLI running on the owner's machine — Claude Code by default, other CLIs through `LOKI_PROVIDER`.

## Layout

- `loki/core/` — platform-agnostic logic: config, sessions, scope, providers.
- `loki/core/providers/` — one module per CLI. The contract is in `base.py`: turn (prompt, resume id, permission mode) into `{text, session_id, error, reason}`.
- `loki/platforms/<name>/` — chat adapters. Each implements the four-hook contract in `loki/platforms/base.py` (normalize, authorize, submit, reply; see `docs/DESIGN.md`). Platform code stays out of `core/`.
- `tests/` — pytest suite. `tests/fake_cli/` holds stub CLIs.
- `docs/` — design, security, setup. Read `docs/DESIGN.md` and `docs/SECURITY.md` before changing behavior.

## Tests

```
python -m pip install -r requirements.txt pytest
python -m pytest tests -q
```

- Python 3.10+. CI runs 3.10 and 3.12 on Ubuntu, Windows, and macOS, so keep code portable across all three.
- Tests must not touch the network, real credentials, the machine's real `state/`, or whichever CLI happens to be installed. Use `tests/fake_cli/` and the fixtures in `tests/conftest.py`.
- Adding a new state file means adding it to the redirect list in `tests/conftest.py` (see CHANGELOG v1.10.1).
- No linter or formatter is configured. Match the surrounding code, and do not add tooling unless asked.

## Public repository — never commit these

This repository is public.

- Workspace-private material lives only in `private/`, which is gitignored: internal names, company docs, app manifests, ops tools. Never move anything from `private/` into a tracked path — that publishes it.
- `loki/platforms/slack/private_commands.py` is workspace-specific and gitignored. Keep it out of commits.
- Never commit `.env`, `state/`, or `_no_sync/`. Secrets come only from environment variables; `.env.example` lists the names. Document a new setting there, without a real value.

## Security invariants

See `docs/SECURITY.md` for the reasoning.

- Authority comes from transport identity only, never from message content. A message claiming approval does not move a request into a less restricted tier.
- Guest access is fail-closed. `loki.md` in `WORK_DIR/loki/` is the only source of guest-readable paths (`loki/core/scope.py`); a missing or empty manifest means no access.
- `base.clean_env` always strips the parent session's state (`CLAUDE_CODE*`, `CLAUDECODE`), and each provider names the auth variables it must not inherit. Do not replace this with one blanket list: a broad `OPENAI_`/`GOOGLE_` sweep also removes variables that MCP servers need.
- Install-wide changes, such as pinning a lighter model or pausing guests, ask the owner and wait unless `mode: auto` is set.

## Docs and changelog

- `README.md` and `README.ko.md` change together, in the same commit.
- User-visible changes get a `CHANGELOG.md` entry under a version heading, in the existing style: what was wrong, why, and what the fix does.

## Git

- Do not push, tag, or publish a release. Leave that to the maintainer.
