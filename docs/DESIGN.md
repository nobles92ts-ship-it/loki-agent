# Design Notes

Why Loki looks the way it does. The codebase was hardened through adversarial design review before v1 — most of the decisions below exist because a failure mode was found first.

## Architecture

```
loki/
├── core/                 platform-agnostic
│   ├── config.py         .env loading · paths · logging · i18n (en/ko) · claude auto-detect
│   ├── brain.py          claude -p subprocess · result parsing · quota/timeout · kill switch
│   ├── jobs.py           serial queue (one claude at a time) · per-thread session map
│   ├── dedup.py          event-id seen-set with TTL (platforms redeliver)
│   └── prompt.py         context wrapping + injection guard
└── platforms/
    ├── base.py           the 4-hook adapter contract (normalize/authorize/submit/reply)
    └── slack/            Socket Mode adapter + app manifest
```

## Key decisions

| Decision | Why |
|---|---|
| **Brain = official `claude` CLI subprocess** | Uses your subscription login exactly like a terminal session — no API key to manage, no token extraction, clean ToS posture. |
| **Prompt via stdin, not argv** | Windows `.cmd` shims drop/mangle positional args with spaces/unicode. stdin is loss-free. |
| **Strip `CLAUDE_CODE*` / `ANTHROPIC_*` env before spawning** | If Loki is (re)started from inside a Claude Code session, the child would silently inherit *that session's* auth instead of the machine login. Stripping pins identity to `~/.claude`. |
| **Bounded concurrency, ordered conversations** | Up to `JOB_CONCURRENCY` (default 2) `claude -p` processes run in parallel, but jobs from the *same* conversation never overlap — `--resume` continuity stays intact. Every job gets an id: `!jobs` lists them, `!cancel <id>` kills exactly one, `!stop` cancels all. |
| **Event dedup with TTL** | Slack redelivers events (3 s ack rule, reconnects). Without a seen-set you answer twice. |
| **Socket Mode** | No inbound port, no public URL, works behind NAT — right default for "a bot on my desk". |
| **`--permission-mode plan` default + boot self-test** | The safe default must be *verified*, not assumed: boot asks Claude to write a probe file and refuses to start if it succeeds. Fail-closed beats fail-open. |
| **Guest hard-cap in code** | Authorization decides the permission mode *per caller* at dispatch time — a config mistake can't grant guests write power. |
| **Injection guard framing** | Thread/channel context is data. The wrapper says so explicitly and pins the instruction to the final `[REQUEST]` line. Not a silver bullet (see SECURITY.md) — but it removes the cheap attacks. |
| **`CREATE_NO_WINDOW` + process-group tree-kill** | `.cmd` spawns visible consoles on Windows (flashing black boxes); timeouts must kill the whole child tree, not just the shim. |
| **UTF-8 forced everywhere** | Legacy consoles (cp949 etc.) crash naive `print` on emoji; `pythonw` has no stdout at all. Both handled at import time. |
| **Metadata-only logging** | Logs are for ops (who/when/how long/result reason), not surveillance. Message bodies never touch disk. |
| **i18n via one `t()` table** | Bot UX in English or Korean (`LOKI_LANG`) without forking strings across the codebase. |

## Known limits (v1)

- **Windows-first.** `taskkill`, console suppression and the `.cmd` shim are Windows-specific; the core logic isn't. Ports welcome (see roadmap).
- **Per-request latency ≈ 15–30 s** — each request is a fresh `claude -p` process (plus `--resume` for thread continuity). Fine for chat-ops; not for realtime.
- **Single instance per Slack app.** Socket Mode load-balances events across connections; two Lokis on one app each get half the messages.
- **Subscription limits are shared.** Owner and guests draw from the same rolling window.
