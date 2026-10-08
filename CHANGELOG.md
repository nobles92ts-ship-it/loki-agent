# Changelog

## [Unreleased]

### What you can do now

- **Guests can look up spreadsheet data, even outside `WORK_DIR`.** Design tables often live in a version-controlled checkout on another drive, as `.xlsx` files, and a sealed run could read neither: the manifest took only folders under `WORK_DIR`, and Loki's reader took only text. A manifest folder outside `WORK_DIR` now opens to the sealed reader as exactly that folder — not its parent, not through a link, never a drive root or a folder holding `WORK_DIR`, the worker or the home folder; Claude runs still deny it. Nothing is copied: a spreadsheet is read where it is and kept, rendered, until the file changes. Each row becomes one line with its column names — `[Items row 5] Id=1001 | … | Price=500` — because a search hit is a single line, and a row of bare numbers says nothing about which is the price. Office lock files and `~` drafts are skipped. Three search changes came with it, each from a question that failed: a keyword's words may stand apart (`setup cost` finds `setup gold cost`); within a file the lines matching the most keywords come first; and when the first results are not enough, the model may search once more with the words they showed it — asked what something costs, it searched one word for *create* while the table used another. The turn-one listing now shares its 300 slots fairly, so a 2,000-file wiki no longer hides a small folder granted beside it.

- **A style note for every answer.** `<WORK_DIR>/loki/style.md` says how Loki should write — tone, length, rhythm — and rides in front of every prompt, outside the context guard. Guests get it too: a sealed run reads nothing but its prompt, so the prompt is the only place a style rule can reach it. No file, no note — the prompt carries exactly what was asked, as before. The file is read on every request, and it sits under the same deny rule and tamper revert as `loki.md`, because every answer reads it.

- **`LOKI_OWNER_MODE=restricted` keeps even your own DM off the machine.** Your DM runs at `CLAUDE_PERMISSION_MODE` with every tool the agent has, and on Codex that includes a browser and the computer-use plugin, which clicks and types on the same mouse and keyboard you are using. Asked about a Slack link it could not open, a DM run opened a browser tab, launched the Slack desktop app through the shell and reached for computer use to drive it; it missed only because it called the plugin by the wrong names. `restricted` sends the DM down the route a shared channel already takes — with `LOKI_RESTRICTED_MODE=sealed` on Codex, no tools at all. It keeps what a DM needs: the guests' shared folders stay readable through Loki, and a screenshot now rides on a sealed run's prompt, where before it was only named by a path a sealed run cannot open. An attached text document is not readable in a restricted DM yet. On Claude the restricted route is only the permission-file guard, so the tools stay. The default, `full`, is unchanged.

- **Documents posted in a thread are read.** Every Slack run on Codex is sealed now, so the model cannot open a file — a crash log posted in a thread reached it as nothing at all, and one dropped in your DM as a local path it could not follow. Loki now reads them and hands the text over inside the context guard: text formats decoded (UTF-8, UTF-16 with a BOM, then CP949, which Korean Windows tools still write), docx, xlsx and pptx unzipped with the standard library, an xlsx's cells kept in their columns. The newest four documents in a thread are read, each clipped to 12,000 characters by its start and end, because a log's error is usually last. Each message names its files, images included, so the talk still makes sense. Images, archives and executables are never fetched, and the bot token is only ever sent to Slack's own hosts — a file in a thread can be anyone's. PDF and the legacy binary office formats are named and marked unreadable; reading them needs a parser this install does not have yet.

- **On Codex, a restricted request can run sealed — with no tools at all.** A guest's request, or yours in a shared channel, is fenced by Claude Code deny rules that Codex cannot carry, and Codex's own permission profiles cannot say "deny everything except these folders" on Windows, so every such request fell back to Claude. `LOKI_RESTRICTED_MODE=sealed` runs them on Codex with every tool removed instead: no MCP servers or connectors, no shell, a read-only sandbox, an allowlisted environment and an empty working folder. A guest still reads its shared folders — Loki reads them in Python and hands the text over, and every path must land inside a granted folder. The default, `fallback`, keeps running them on Claude. The measurements are in `docs/codex-migration.md`.

### Fixes

- **Your own question in a channel could not reach the shared folders.** Asked `@Loki 우리 게임은 hp가 0이 되면 어떻게 돼?` in a shared channel, Loki answered twice that the context did not say; the same question in the owner's DM, minutes earlier, was answered from the shared docs. An owner's channel mention takes the guarded route, and on Codex that runs sealed — but only the restricted DM was handed the guests' shared folders, so the channel run had no tools and nothing to read. It now gets the same folders through Loki. Nothing widens: they are the folders that channel's guests can already read.

- **A guest's search never reached the design pages.** With that fixed, the same thread got an answer — from the wrong file. A sealed run's search stopped after 8 MB and kept the first 40 matching lines in folder order; on a 45 MB share whose TC artifacts sort first, it stopped 551 files in, and the design pages started at file 1,730. The one death-and-revive file it could see was a backup holding another page's text, so the answer said the rules were not in the docs. Search now covers the whole share (64 MB cap), ranks files — a keyword in the file name first, then the most different keywords, then the most lines — and takes at most 8 lines from any one file, so a 2 MB dump of the whole wiki cannot crowd out the page about the topic. The walk also stopped re-checking the roots and the exclusion list for every file — 5.9 s down to 0.8 s on that share — while each file is still judged by where it resolves.

- **A poll in the channel was invisible to Loki.** Asked `@Loki 투표 결과 알려줘` about two Open Poll+ polls, Loki answered three times that there were no tallies — and from what it was given, it was right. The bare mention's channel context skipped every message with a subtype, so an app's own post (`bot_message`) went the way of joins and topic changes. Kept, it would still have read only `Poll : <question>`: apps put their content in blocks and leave `text` as a notification fallback. And the copies forwarded into the thread arrived as attachments, which thread context never read — a sealed run cannot open the link itself either. Context now reads a message the way the client shows it: an app's section, header and context blocks; a forwarded or linked Slack message's own blocks, marked `↪ <author>:`; a bot's legacy attachments. A person's link previews stay out as before, and channel context keeps its 400-character cap per message.

- **In a busy Slack channel, a bare mention lost the talk right before it.** Channel context — what a bare `@Loki` mention reads, and what the owner's `!summary <channel>` summarizes — takes up to 120 messages from the last 7 days (by default), keeps 400 characters of each, and caps the whole at 10,000. That cap was a slice of the joined text, and the lines run oldest to newest, so it kept the *oldest* 10,000 characters: past the budget, the newest messages were the ones that went — usually what the mention is about — and the last one kept was cut mid-message. Nothing said so: the context header still read "last 7 days · up to 120 messages", so the model had no sign that the newest part was missing. The budget is now filled from the newest message back, one whole message at a time, then put back in time order. Over the budget it is the oldest messages that go, and none is cut mid-way.

- **Two workers shared one Slack app.** On 2026-10-06 the watchdog started a worker at 10:16, and at 10:20, when Windows got to its delayed startup apps, the Startup folder entry started another. Both stayed up on the same app token, and Slack split the events between them. Nothing was wrong with either. The Startup entry runs `-m loki` straight, without looking. `gateway ensure` does look, but only at the heartbeat, and a new worker writes that a couple of seconds after it starts. `run_worker.vbs`, a leftover `setup.ps1 -Autostart` launcher and a terminal all start one blind as well. So the check moved into the one place they all pass through: before it connects, a worker takes an OS file lock in `state/`, one per platform, and a second worker that finds it held logs who has it and exits. The OS drops the lock when its holder dies, so a crash leaves nothing behind to block the next start. The watchdog had the same hole from the other side. A worker whose socket had dropped (`stale_heartbeat`) was left running while a new one started next to it, and the old socket could reconnect. `gateway ensure` now stops that worker first, but only when it is the one holding the lock: a pid in a stale stamp can belong to some other process after a reboot.

## [v1.10.1] 2026-08-12

Two fixes, both of the same kind: something reported success while doing nothing.

### Fixes

- **`!listen` confirmed a zone it could never fill.** An auto-listen zone only fires if the Slack app subscribes to `message.channels` / `message.groups`. Without them `!listen` still wrote the state and still answered "now listening" — and nothing ever arrived. Slack's bot-token API cannot report an app's event subscriptions, so the check is the one thing observable from inside: whether a channel message has ever reached this install. It latches on the first one and persists, so a quiet weekend does not bring the warning back to an install where zones demonstrably work. `!listening` carries the same line, because that is where you go when a zone is not answering.

- **The test suite was writing the machine's real `state/`.** Only `provider.json` and `sessions.json` were isolated; every other module binds its path from `config.STATE` at import, so any test touching one of the other fifteen edited live state on the machine running the suite. This was not theoretical — the adapter tests push channel messages through `on_message`, `autolisten` was not isolated, and the suite set `seen: true` on a running install, switching off the warning added in the same release while every test passed. `conftest` now redirects the whole list and resets `autolisten`'s import-time cache. **Adding a new state file means adding it to that list.**

### Notes

If you upgrade and `!listen` suddenly warns you, that is the fix working: the zone was never going to fire. Add `message.channels` and `message.groups` to your app's Event Subscriptions — or leave it, if you only ever call Loki by `@mention` and DM, which are unaffected.

## [v1.10.0] 2026-08-11

Loki gets to open the conversation.

### What you can do now

- **The suggestion pane.** Open a thread with Loki in Slack and the blank composer is replaced by up to four things worth asking — live conditions first, then your open goals and their next undone step. Tap one and it runs. Guests see three generic openings and nothing about you.
- **Nudges you didn't ask for.** Every 15 minutes Loki checks three things it already knows and speaks first when one becomes true: a goal untouched for three days, three runs failing the same way, or the selected provider unable to answer. Each alert carries buttons — run it, park it, or silence that one watcher.
- **`!nudge`** — `on` · `off` · `now` · `off <key>`. The whole mechanism or one watcher; the pane keeps working either way.

### Changes

#### Features

- `loki/core/nudge.py` — one `Nudge` shape, produced by `probe()`/`watch()` (push, cooldown-gated) and `suggest()` (pull, side-effect free). State in `state/nudges.json`.
- `loki/platforms/slack/assistant.py` — `assistant_thread_started` → `assistant_threads.setSuggestedPrompts`, the three button actions, and the poll loop that DMs the owner.
- Manifest: `assistant_view` feature, `assistant:write` scope, `assistant_thread_started` / `assistant_thread_context_changed` events. **An app installed before this release must have its manifest updated and be reinstalled before the pane appears** — the DM nudges work without it.
- `LOKI_NUDGE`, `LOKI_NUDGE_POLL_MIN`, `LOKI_NUDGE_STALE_H` in `.env.example`.

#### Behaviour

- Goals now record `touched`, updated when a step is added or ticked. Without it "stale" would mean *old* rather than *abandoned*. Goals written before this release fall back to `created`, so nothing goes invisible on upgrade.
- `usage.recent(n)` — the tail of the ledger in time order, which is what "the last three runs all failed" is a claim about.

#### Fixes

- `!nudge` status listed a tripped-and-cooling key twice — once as live, once as quiet — which read as a contradiction. Cooldown now rides on the condition's own line.

### Notes

Suggestions are owner-private: anyone can open an assistant thread with a bot they share a workspace with, and a goal title is a private note about your own work. Non-owners get the standing openings only.

Nothing here decides to run anything. A nudge is an offer with a button on it.

## [v1.9.1] 2026-08-11

The Google door moved while we were walking through it.

### What you can do now

- **`!provider antigravity`** — a Google AI Pro/Ultra plan (or the free tier) answering through Google's `agy` CLI. Verified end to end: Gemini 3.6 Flash, with conversation continuity across turns.

### Changes

#### Features

- **`antigravity` provider.** Google stopped serving the Gemini CLI to individual accounts in June 2026 — the free tier *and* Google AI Pro/Ultra — and named Antigravity the successor. That took the whole flat-rate Google path out of v1.9.0 the day it shipped, so here it is again through the CLI that still has it.

  Every shape was taken from the running binary rather than the docs. The prompt goes on **stdin with no `-p`** — ⚠️ `-p -` looks like the usual stdin idiom and isn't: `agy` takes the `-` as the literal prompt, answers it, and discards stdin, so the question is silently replaced by a greeting. `--output-format json` returns one object on stdout for success *and* failure. `--conversation <id>` resumes, and a **dead id is not an error**: the CLI warns and starts a fresh conversation with a new id, so there is no retry path here — only the discipline of storing whatever came back.

  `--print-timeout` is pinned to Loki's own `TIMEOUT_SEC`; left at its 5-minute default it would cut a long job short or outlive one Loki had already given up on. The binary is also looked for in `%LOCALAPPDATA%\agy\bin` — the installer adds that to the PATH *registry*, which a process that was already running (the worker) does not see until it restarts, so without the fallback installing `agy` appears to do nothing.

#### Fixes

- **A closed door arrived as a stack trace.** The Gemini CLI reports the tier cutoff as an uncaught `IneligibleTierError` with a screenful of node frames, and Loki relayed all of it — which reads like a bug in Loki and buries the one sentence saying that signing in again will not help. Named now, with the settings that still work. Raw failures generally are trimmed: when a provider fails outside its own JSON envelope the raw stream is all there is, and for a node CLI that is mostly `at process.processTicksAndRejections`.

- **Tests read the machine's own provider setting.** `state/provider.json` holds the choice this install is actually running under, and a `!provider gemini` left behind by a live smoke rerouted every spawn in tests that never mentioned a provider — surfacing as `test_account` failing its assertions about `CLAUDE_CONFIG_DIR`, nowhere near the cause. An autouse fixture now isolates it.

#### Docs

- README (en/ko) and `.env.example`: `gemini` is relabelled honestly — API key or enterprise Code Assist licence, individual accounts closed — and `antigravity` documented as the flat-rate Google path, with its one-line installer.

## [v1.9.0] 2026-08-11

Loki stops assuming there is one agent, and starts having something to work toward.

### What you can do now

- **`loki` is a word you can type.** `python -m loki cli install` puts a launcher on PATH; after that `loki chat` opens the terminal console from any directory, with no venv to activate.
- **Pick which agent answers** — `!provider gemini`, `codex`, `kimi`, `groq`, or `claude`. Each one spawns a CLI already signed in on this machine, so the bill stays on a plan you already pay for rather than a per-token key.
- **`!goal <what you want>`** — an objective that outlives a single answer. It keeps its own steps and rides along in that conversation's context until you close it.

### Changes

#### Features

- **`loki/core/providers/` — one module per agent, and one rule above the choice.** Loki's premise was never Claude-specific: the cheapest agent to run is the one already logged in on this machine. A ChatGPT plan drives `codex`, a Google account drives `gemini`, and both were sitting on the same disk. Each provider maps Loki's contract — prompt, resume id, permission mode → `{text, session_id, error, reason}` — onto its own CLI.

  **The rule that overrides the switch:** Loki's guest scope and its permission-file guard are Claude Code deny rules, carried as a per-request settings file the tool layer enforces. Only a provider that can hold one may serve a request that needs one, so **every guest request, and every request you make outside your own DM, runs on Claude no matter what is selected**. A provider switch must not be able to quietly widen what a stranger can read. Your own DM and the terminal console follow the switch.

  Sessions are filed **per provider**, because a session id is a receipt from one agent's store and handing Gemini's to Claude's `--resume` fails. Switching therefore keeps both conversations instead of dropping one — unlike `!account`, where the same thread would replay under a different login and has to be cleared. Session state written before this version is adopted as Claude's rather than discarded.

- **`!goal` / `!목표` — the objective that outlives a turn.** Everything else in Loki is turn-shaped: a message arrives, an agent answers, and the next message starts from whatever the transcript still holds. A goal is the missing noun — it keeps steps, it is injected into that conversation's prompt on every turn as delimited context (not as an instruction that outranks the request), and it stays until closed. Deliberately *not* autonomous: a goal shapes the next turn you take, it does not take turns by itself. `!schedule` already exists for work that should fire on a clock.

- **`python -m loki cli install`** writes `loki.cmd` and `loki` into a PATH directory, pinning the venv interpreter and the repo directory at install time — the two pieces of ambient state that do not survive a cold terminal. A launcher, not a copy, so pulling the repo updates the command.

- **The boot self-test is keyed by provider as well as version.** Each agent spells read-only differently — `--permission-mode plan`, `--approval-mode plan`, `sandbox_mode="read-only"` — so a pass proves one mapping, not the idea. Switching providers re-runs it, which is exactly the moment the guarantee is resting on a flag nobody has tried yet. `doctor` now lists every provider and what each still needs.

#### Fixes

- **Gemini is refused before the spawn when nobody is signed in.** Unauthenticated, the CLI starts its *interactive* OAuth flow — which reads stdin, and stdin is where Loki puts the prompt. Observed live: the question was swallowed by the login prompt, the login was then cancelled by the same EOF, and the CLI answered "No input provided via stdin" after burning a spawn. On a worker with no console it can sit there until the timeout instead.

- **`--skip-trust` on every Gemini spawn.** An untrusted folder silently downgrades `--approval-mode` back to "ask a human", which in a headless spawn means hang until the timeout. The CLI says so on stderr and carries on.

- **Gemini's JSON envelope is read from stderr too.** A successful run prints it on stdout and a failed one on stderr; reading only stdout turns every real error into "(empty response)". Prose printed beside it — "YOLO mode is enabled." — is skipped rather than parsed.

- **`claude.available()` returned its failure note alongside a healthy result**, so `!provider` showed a permanent ⚠️ next to the provider that was working fine.

- **`private/tests/` fixture pinned to a directory retired in July.** It still monkeypatched `_RUNS_DIR`, which stopped existing when tc-team's output moved under `specs/<기능명>/`; five tests had been failing since.

#### Docs

- `.env.example`: every provider, which auth path is flat and which is metered for each, and the sandbox rule that overrides the switch. `ANTHROPIC_*`, `GEMINI_API_KEY` and `OPENAI_API_KEY` inherited from a parent shell are stripped per provider — a key left in the environment is the one way to move a whole workspace onto per-token billing without noticing.

## [v1.8.3] 2026-08-09

A stop button for the one conversation nobody was ending.

### What you can do now

- **`!exit`** — end a bot-to-bot exchange in this thread. Other bots stop being heard and any bot work still running here is cancelled, while **people carry on untouched**. `!exit undo` reopens it, `!exit list` shows where you've used it.

### Changes

#### Features

- **`!exit` / `!종료`** — Two Lokis can talk to each other (an allowlisted bot waking another inside an auto-listen zone), and it is the one path where nobody decides when to stop: neither side gets bored, and an exchange that has stopped being productive keeps spending both subscriptions. Every existing off-switch was the wrong size for that — `!stop` cancels what is running and the next bot message wakes Loki again, `!block` shuts out people too, `!unlisten` sends humans back to @mentioning, and `!new` only clears the conversation's memory. This is the missing one, scoped like `!listen` (a thread, or the whole channel at top level) and persisted across restarts.

  **Human-only by construction.** Bot messages never reach the command layer, so the other Loki can neither mute yours nor talk yours out of staying quiet. And because one side going quiet leaves nothing to wake the other, `!exit` on either end is enough to end the exchange.

- **`jobs.snapshot()` now carries `channel` and `thread`**, so a caller can act on "the work in here". `!exit` uses it to cancel bot-triggered jobs in this thread only — the owner's own request in the same thread survives.

#### Docs

- README (en/ko): a "two Lokis talking" section — allowlist **in one direction only** (the self-trigger guard stops A↔A, not A↔B↔A), scope the other install with its own org and `rate:`, and why there is no progress detector: separating "a new question about the same topic" from "the same deadlock restated" is a semantic judgement, and word-overlap heuristics measured against real transcripts could not do it. Bound it with things that need to understand nothing — rate, budget, and `!exit`.

## [v1.8.2] 2026-08-08

Commands sent through a connector reach the command layer.

### What you can do now

- **Drive Loki from another machine's Claude session.** Have a Claude session on your laptop post into a Slack channel or Loki's DM, and the worker on your desktop does the work and answers in the thread — the same subscription, the same permissions, no second Slack app. Until now this worked for plain requests but **every `!` command broke**, which made it look like the setup itself was wrong.

### Changes

#### Fixes

- **Slack's "Sent via \<app\>" credit was reaching the command parser.** When someone posts *through an app* rather than typing in Slack — a Claude session driving Slack via a connector is the common case — Slack appends a credit line. It is invisible in the client, but it is concatenated into the event's `text`:

  ```
  !usage *Sent via* <@U0AFK1Q8S4W>
  ```

  Every anchored command regex then misses. `!usage`, `!jobs`, `!new`, `!listen` and `!summary` fell through to the brain as ordinary chat — Claude would answer "`!usage` isn't a command I recognize", which is true and completely unhelpful. `!account`, `!alias` and every alias matched but took the credit line as their arguments.

  Fixed by reading Slack's own structure rather than matching the words: the credit is its own trailing `context` block while the author's text stays in `rich_text`. **The credit is localized** (`다음을 사용하여 보냄`, `Sent via`, `経由で送信`, …), so any pattern built from its wording is one workspace language away from breaking. A person typing in Slack cannot produce a `context` block at all — the composer only emits `rich_text` — so a trailing one is always Slack's, never theirs. Applied to inbound commands and to thread/channel context alike, so quoted history stops carrying the credits too.

## [v1.8.1] 2026-08-08

Three failures that reported themselves as nothing at all — and a switch for the account pin.

Every fix in this release is the same shape: something returned successfully while doing nothing. An empty channel history that looked like a quiet channel. A folder grant that looked like a broken manifest. An alias that saved, listed, and never fired. Thanks to [@olyj2e-crypto](https://github.com/olyj2e-crypto) for reporting all three from a fork running in production, each with a diagnosis and a patch.

### What you can do now

- **Choose which account spends**, without editing `.env` or restarting: `!account off` runs the next request as the config dir's login, `!account on` goes back to the pinned one. For anyone holding a personal subscription *and* a company account.
- **Read channel context again** on Slack. If `!summary` or an auto-listen zone has been answering as though the channel were empty, it was — this fixes it.
- **Share folders from a WORK_DIR that isn't on `C:`.** Every grant in `loki.md` and every org manifest was being cancelled.

### Changes

#### Fixes

- **Slack returned zero messages for every history fetch** ([#3](https://github.com/nobles92ts-ship-it/loki-agent/issues/3)). `oldest` was built with `str(time.time() - …)`, which renders 7+ decimal places most of the time. `conversations.history` answers `ok: true` with an **empty list** rather than an error when it gets more than 6 — so channel context and `!summary` ran on nothing, and no log line said why. Timestamps now go through `slack_ts()`, the only place that formats one. It was the only such call in the tree; `!summary`'s own fetch and the thread replies use Slack-supplied strings.
- **A WORK_DIR on `D:` or `E:` shared nothing at all** ([#2](https://github.com/nobles92ts-ship-it/loki-agent/issues/2)). The guest fence blanket-denied whole drive roots, and deny beats allow — so on any install whose WORK_DIR wasn't under `C:`, every folder the manifest granted was cancelled with it. Every guest lookup came back permission denied and nothing pointed at the drive rule, so it read as a broken manifest. A root that *contains* WORK_DIR now loses its blanket rule and is closed sibling by sibling along the ancestor chain instead: the corridor down to WORK_DIR stays open, everything beside it stays shut. Roots that don't contain WORK_DIR keep the blanket rule, and `guard.deny_patterns()` still lands last so nothing above it can undo the secret denies.
  - Two consequences worth knowing: this **tightens** installs that were fine before (the other top-level folders on WORK_DIR's own drive were never denied by name), and it closes the same hole on Linux and in Docker, where the drive-letter roots never applied to begin with.
- **`!alias` accepted names that nothing could ever call** ([#4](https://github.com/nobles92ts-ship-it/loki-agent/issues/4)). Aliases are matched last, and the guard against shadowing was a hardcoded set of the *shipped* command names — which said nothing about `plugins/`, the supported extension point, or a fork's own commands. `!alias add tc …` against a live `!tc` saved, listed, and silently never fired. The check now probes the **live dispatch** (`core/registry.py`): built-ins, private commands, checklists and every loaded plugin's own `MATCH`, so nothing has to be registered by hand and the list cannot drift from the code again. Refusal names what holds the name. And because a plugin can claim a name an alias already had, `!alias list` flags any alias that has since gone dead — the drift a definition-time check can't see.
- **Tests were locale- and encoding-dependent.** Three failed on a Korean-locale Windows install (`LOKI_LANG=ko`, cp949) while CI stayed green, because two assertions compared against English strings and two file reads took the platform default encoding. The suite now passes under ko/en × cp949/UTF-8.

#### Features

- **`!account [on|off]`** — the account pin becomes switchable at run time. `.env` still decides *which* account; this decides whether to use it, so `on` always has something to return to and the token is never rewritten or cleared. Off falls back to the config dir's login (pre-v1.8 behaviour). Unset is on, so nothing changes for an install that never touches it. The choice survives a restart, applies from the next request (running jobs finish under the account they started with), and reaches both spawn paths through one accessor, so it can't be honoured on one and missed on the other. Remembered sessions are cleared on a flip — a resumed conversation would otherwise replay under the other account's login and quota. `doctor` reports the switch and stops probing a pin nothing is running under. → [README](README.md#two-accounts--switching-which-one-spends)

#### Docs

- README (en/ko): the `!account` command, a "two accounts" section, and how alias names are now checked.
- `docs/SETUP.md` — switching the pin off without unpinning it. `docs/PLUGINS.md` — your plugin's name is one an alias may not take. `docs/TELEGRAM.md`, `.env.example` — kept in step.

## [v1.8.0] 2026-08-07

One account, whoever is logged in — and a guest fence that actually holds.

### What you can do now

- **Pin Loki to one Claude account.** Mint a token with `claude setup-token`, put it in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`, restart. Every spawn authenticates as that account even when your terminal is logged into a different one. Your desktop and your bot no longer have to share a login. → [docs/SETUP.md](docs/SETUP.md#optional-pin-the-account-with-a-token)
- **Check that the pin is real**, not just configured — `python -m loki doctor` now reports it as its own line.
- **Grant guests folders and have the grant mean something.** An empty allowlist now means an empty scope. Before, one folder was readable no matter what the manifest said.

### Changes

#### Features

- **Account pin (`CLAUDE_CODE_OAUTH_TOKEN`)** — a subscription OAuth token outranks the stored `/login` credential, so it fixes the account for every spawn. It solves what `CLAUDE_CONFIG_DIR` can't: a pipeline that must keep the **default** `~/.claude` (anything reading `~/.claude/skills` or `~/.claude/agents` at run time) can't be moved to a private config dir without taking its toolchain along. The token leaves the config dir alone and replaces only the credential. Optional — unset, nothing changes.
- **`doctor` verifies the pin against an empty config dir.** This detail is the whole feature. An invalid or expired token does **not** raise: `claude` quietly falls back to the stored login and answers normally, so a check run against the real config dir passes no matter what, and the pin looks healthy while the bot runs as whoever the machine happens to be. An empty dir removes the fallback, so the token has to stand on its own. Tokens last a year and do not auto-refresh, which is exactly when that silent fallback would otherwise start.

#### Security

- **Guests could read the worker's own tree.** `scope` built its deny list by subtracting from `WORK_DIR`, and exempted the `loki` folder outright so `loki.md` stayed readable. If your worker lives inside that folder — `<WORK_DIR>/loki/loki-agent`, the layout `setup` produces — then its source, `state/`, and `.env` all sat under the one folder the subtraction skipped, and **an empty `## Allowed paths` did not prevent it**. The `orgs/**` deny was the scar from the first time this happened; the registry then leaked again through its generated copy in `state/`. The exemption is gone — the manifest is handed to guests in the prompt, so nothing needs to read it off disk. **If guests can reach your bot, upgrade.**
- **Guest and org runs now carry `guard.deny_patterns()`.** The read-deny on `.env` and the credential files existed but was wired only to the owner path. Applied last, so no folder exemption can undo it.

#### Docs

- `docs/SETUP.md` — pinning walkthrough, the paste-the-code-back step that `setup-token` needs a real terminal for, the empty-config-dir verification, and three troubleshooting rows for the ways it fails quietly.
- `.env.example` — the new key, with the `sk-ant-oat01-` shape and the silent-fallback warning.

## [v1.7.0] 2026-07-30

Telegram, files that go both ways, and spending you control.

### Platform

- **Telegram adapter** — `LOKI_PLATFORM=telegram` (or `python -m loki telegram`) plus a token from @BotFather and your numeric id. **No new dependency**: the Bot API is plain HTTPS + JSON, so `urllib` covers it rather than making every Slack and Discord user install an SDK they don't run. Telegram's surfaces map onto the existing trust model — a private chat is the owner's DM (one id, everyone else ignored), groups are the guest surface where you must @mention or reply to be heard, and unaddressed chatter only runs inside an auto-listen zone. Guests stay read-only, path-scoped, rate-limited and budget-capped. → [docs/TELEGRAM.md](docs/TELEGRAM.md)
- **Differences, documented not hidden** — a bot can't read history it wasn't present for, so group context is what Loki observed while running. Replies render as HTML (MarkdownV2 needs escaping *outside* markup, where one stray character rejects the whole message) with a plain-text retry if a send is refused. `!check` and `!bot` stay Slack-only. Session reset needed nothing new — `!new` already routes through core.

### Features

- **Attachments in, files out** — inbound attachments now cover documents, data and source text through a deny-by-default extension allowlist; executables and archives are refused before download, and the inbox is size-capped (`LOKI_MAX_FILE_MB`, default 20) and pruned weekly. Outbound gains `!send <path>` — relative to `WORK_DIR`, absolute inside it, or a glob — resolved first and then fenced, so a symlink pointing out is rejected. Owner-only and deliberately not grantable via `!org allow`. Because the policy lives in `core/files.py`, Discord picks up the same allowlist and fence and loses its duplicated copy.
- **Prompt aliases (`!alias`)** — a prompt you retype becomes a command, defined in `<WORK_DIR>/loki/aliases.md` (human-editable, re-read on change, same as `loki.md` and the org files). `{args}` marks where arguments land. An alias is a prompt, not a permission: it runs through the ordinary path, and built-in names — including `!new`, `!plugins`, and names reserved for commands that don't exist yet — can never be shadowed. Guests only fire what their org was granted.
- **Schedules to a channel** — `!schedule daily 09:00 #standup <prompt>` posts the run to a channel instead of back to where it was created. A scheduled fire runs at the owner's full scope, so the confirmation says so; an unreachable channel falls back with the result attached rather than dropping the run.
- **Usage budgets (`!budget`)** — daily/weekly and per-org caps that refuse *guests* once reached; owners are never capped. Caps and mitigations are separate on purpose: a cap refuses guests outright, but pinning a lighter model or pausing guests changes the whole install, so those are **manual by default** — at 80% and 100% Loki asks in DM and waits. `!budget mode auto` opts into it acting alone. An org's cap binds that org alone.
- **Bot triggers (`!bot`, Slack)** — an allowlisted bot can wake Loki **inside an auto-listen zone**, turning a CI failure into an investigation. Two opt-ins, both the owner's. The bot arrives as a guest and its message is text, never a command. Loki's own ids are refused before the allowlist is read, so no state edit can start a loop. `!bot seen` lists ids to copy.
- **Docker / NAS** — `Dockerfile` + `docker-compose.yml` + [docs/DOCKER.md](docs/DOCKER.md). The image carries no credentials. `TZ` is wired through (a UTC container fires `!schedule daily 09:00` at the wrong hour) and git's dubious-ownership check is relaxed in-image (a bind-mounted work dir is nearly always a different uid). The container is a *tighter* fence than a desktop account — the sensible place for write mode.

### Fixes

- The read-only boot self-test was Slack-only, so **Discord shipped without the one check that verifies plan mode cannot write**. It now lives in `core/selftest.py` and runs on every adapter.
- A bad `SLACK_BOT_TOKEN` raised a raw Bolt traceback from module import, since Bolt verifies the token when the `App` is constructed and the friendly handler in `run()` never ran. Both it and its Telegram twin now exit 2 with a usable message.
- `!org allow` silently granted nothing for Korean command names — org command tokens now accept Korean, matching what alias names allow.

### Tests

+232 cases (186 → 418). Adapter dispatch is now testable at all: `conftest` stubs the Slack SDK, so routing guarantees — who gets refused, what a bot may not do, where a failed post ends up — are covered rather than reasoned about. Two real bugs were caught that way: one org's exhausted budget refusing every other org's guests, and a first cut of the schedule parser eating "CHECK the logs" as a destination channel.

## [v1.6.4] 2026-07-29

Staying up, and making it yours.

- **Is it still running?** — `python -m loki status` answers from the heartbeat alone (no network); `python -m loki doctor` adds the full install check. The worker stamps `state/health.json` on a timer and after every finished job, and **down means either half failing**: the process is gone, *or* the heartbeat went stale. A hung worker that's still technically running is just as broken as one that exited, and the old `tools/diag.py` couldn't tell you about either.
- **Keeping it up** — `python -m loki gateway install` registers Loki with whatever the OS already has: a Scheduled Task on Windows plus a 5-minute watchdog (Windows will start a task but won't restart a crashed one), a systemd user unit with `Restart=on-failure` on Linux, a launchd agent with `KeepAlive` on macOS. Also `gateway ensure` (start only if down — safe on a timer), `stop`, `restart`, `uninstall`. Supervision stays the OS's job; a second Python process watching the first is one more thing that can die quietly.
- **Plugins** — custom commands now live in `plugins/`, one file per command, discovered at boot: define `MATCH` and `handle(match, ctx)` and you're done. `!plugins` lists them. **Owner-only by default**, and opening one up takes *two* steps — `OWNER_ONLY = False` in the plugin **and** `!org allow <org> <name>` — because a file appearing in a folder must never silently hand guests a new way to reach the machine. Built-ins are matched first, so no plugin can shadow `!stop`. A plugin that fails to import, or raises, is contained rather than fatal. See [docs/PLUGINS.md](docs/PLUGINS.md). `plugins/*.py` is gitignored, so your commands survive pulling upstream.
- The `private_commands.py` hook is unchanged and still the right tool for long-running pipelines that own their own threading — plugins return one string.
- `tools/diag.py` is now a shim over `loki.core.diagnostics`, so it and `doctor` can't drift apart.
- **Tests** — +30 (205 total).
- [docs/ROADMAP.md](docs/ROADMAP.md) updated: model fallback on quota moved to *considered and not planned* (a subscription's usage window is one shared pool, so there's nothing smaller to fall back to), and the webhook entry now records that `!schedule` already reaches the same outcome without an inbound port.

## [v1.6.3] 2026-07-28

Discord, and a memory.

- **Discord adapter** — `python -m loki discord` (or `LOKI_PLATFORM=discord`) serves Discord over the gateway, with the same posture as Slack: DMs are the owner's private line, guild `@mentions` are open to anyone but hard-forced read-only, threads carry their own conversation, and guests get the `loki.md` scope, org tiers and rate limits unchanged. Needs `DISCORD_BOT_TOKEN`, `DISCORD_OWNER_ID`, and the **MESSAGE CONTENT** privileged intent — without it Discord delivers empty message bodies, so Loki refuses to start and says so instead of looking connected and deaf. `!check` stays Slack-only (it depends on Slack's interactive buttons). One process per platform; run two to serve both, they share `state/` safely.
- **Conversation memory** — a DM is now one running conversation instead of a fresh session per message: previously the session key was the message's own timestamp, so every top-level DM started over. Threads behaved correctly already and still do. A channel's top level deliberately gets no session — several people share it, and one person's context must not leak into the next person's answer. Sessions expire after `SESSION_IDLE_MIN` minutes of silence (default 120, `0` = never) and persist across restarts in `state/sessions.json`. **`!new`** (`!새대화`, `!리셋`) drops one on demand.
- **Queue orders by conversation** — the job queue now serialises on `session_key`, so two quick DMs can't resume the same Claude session concurrently. Jobs without a session key (channel mentions) still run in parallel as before.
- **Shared command router** — `!jobs`, `!cancel`, `!stop`, `!usage`, `!schedule`, `!learn`, `!new`, `!listen`/`!unlisten`/`!listening`, `!block`/`!unblock` and `!org` moved from the Slack adapter into `loki/core/commands.py`, so every platform inherits one vocabulary instead of a near-miss reimplementation. The owner gate lives inside the router — an adapter can't forget it. Adapters supply only what they alone know (name lookup, mention id extraction). Slack behaviour is unchanged; `!summary` and `!check` stay adapter-side because they aren't plain-text replies.
- **Permission-file tamper guard** (`loki/core/guard.py`) — `loki.md`, `orgs/*.md` and `.env` decide who may read and run what, so any run outside the owner's own DM is both tool-denied from writing them and snapshot-compared afterwards; anything changed is reverted and the owner is DM'd. Authority is read from the transport (user id + DM channel), never from message content.
- **Fix**: `core.orgs` hardcoded Slack's `U…`/`C…` id shapes, so `!org add`/`!org bind` silently rejected every Discord id. Core now validates ids generically; adapters do the precise matching.
- **Fix**: a stale `--resume` id survived a failed retry and was tried again on the next turn. It's dropped now.
- **Tests** — +87 (175 total): sessions, the shared router, and the Discord adapter's surface classification, chunking and async bridge.
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — what's planned and why, with the shape each item would take: process supervision (`doctor` + restart-on-crash), a `plugins/` directory so custom commands stop sharing one file, a model fallback chain so hitting the usage limit degrades instead of failing, per-user sessions in shared channels, token-level usage. Also records what was considered and deliberately *not* planned, so the same ground isn't re-covered.

## [v1.6.2] 2026-07-14

Shared checklists.

- **Checklists** — `!check` posts a shared, clickable checklist: a title line ending in `:`, then one item per line (or a comma-separated list). Each item is a ☐/☑ button that toggles for **everyone** — button labels re-render on `chat.update`, so state stays in sync across viewers (Slack's native `checkboxes` are per-user input and don't sync). Toggle by tapping, or by talking in the checklist's thread: `done 2`, `undo 2 3`, `done all`. Owner creates; anyone who can see it can toggle. Persisted in `state/checklists/`.
- **Manifest** — enables **Interactivity** (`settings.interactivity.is_enabled`) so button clicks arrive over the Socket Mode connection (no Request URL). Apps built from this repo's manifest get it; installs from before v1.6.2 flip it on once under **Interactivity & Shortcuts**. Creating a checklist and `done N` work without it.
- **Tests** — +22 checklist cases (88 total), green on the CI matrix.

## [v1.6.1] 2026-07-13

- **Fix**: the org cache could serve stale data when a create/edit sequence landed within one filesystem-timestamp tick (coarse Windows mtime) — e.g. a double `!org add` of the same user right after `!org create` could bypass dedup. CRUD writes now invalidate the cache explicitly (no timestamp reliance for our own writes) and the change stamp includes file size. Caught by CI on the Windows runners.

## [v1.6.0] 2026-07-13

Per-company tiers.

- **Organizations** — when several companies/teams share one Loki, give each its own tier: `!org create <name>` makes `loki/orgs/<name>.md` (one file = one org, human-editable, applied next request, fail-closed) holding **members**, **bound channels**, **readable folders**, **allowed `!commands`** and a **rate override**. Onboard a whole Slack Connect channel with `!org bind <name> [channel]`, individuals with `!org add <name> @user`; grant fixed pipelines with `!org allow <name> <command>`. Resolution per request: owner → explicit member → bound channel → unaffiliated guest (global `loki.md`, unchanged).
- **Isolation & posture** — orgs never change the permission mode (members stay read-only); each org reads only *its* folders; the org registry itself (`loki/orgs/`) is tool-denied to everyone but the owner; `!usage` now reports by org; the private-command hook receives `ctx["org"]` and `orgs.allows_command()` for gating (see the updated example).
- **Compatibility** — no `orgs/` folder → behavior identical to v1.5.x. No new Slack scopes or events.
- **Tests** — +13 org cases (66 total), green on the CI matrix.

## [v1.5.0] 2026-07-13

Talk without the @.

- **Auto-listen zones** — owner opt-in `!listen`: in a thread it registers that thread, at channel top level the whole channel. Everyone there then talks to Loki **without a mention**. `!unlisten` stops (most specific zone first), `!listening` lists zones. Permissions unchanged: guests stay read-only + rate-limited, `!block` overrides a zone, @mentions inside a zone aren't double-answered (they keep flowing through `app_mention`), and bot messages are ignored — no reply loops. Korean aliases: `!청취` `!청취해제` `!청취목록`. Persisted in `state/autolisten.json`.
- **Manifest** — adds `message.channels` + `message.groups` bot events (no new OAuth scopes). Existing installs: add the two events under **Event Subscriptions**; new installs get them from the manifest.
- **Tests** — +7 zone cases (53 total), green on the CI matrix.

## [v1.4.1] 2026-07-11

- **Fix**: guest rate-limit "try again in N min" could report 61 at the top of the window (max should be 60) — ceiling math corrected. Was also a timing-dependent CI flake.

## [v1.4.0] 2026-07-11

Replies that render, and images.

- **Markdown → Slack mrkdwn** — Claude answers in CommonMark, so headers, `**bold**`, `[links](url)`, and `- bullets` used to show their raw punctuation in Slack. Loki now converts them to Slack's dialect (`*bold*`, `<url|text>`, `•`, `~strike~`, headers → bold, tables → monospaced code block). Code spans and fences are protected; conversion is applied only to model output, never the bot's own strings.
- **Image input** — drop a screenshot in your DM (caption optional) and Loki downloads it and hands the local path to Claude to analyze. Owner-only; 20 MB cap.
- **File output** — when the owner's reply references a local output file (`.html/.png/.pdf/.csv/...` under `WORK_DIR`, size-capped, max 4), Loki uploads it to the thread.
- **Tests** — +12 mrkdwn cases (46 total), green on the CI matrix.

## [v1.3.0] 2026-07-11

Account control, abuse control, and private commands.

- **Dedicated account** — `CLAUDE_CONFIG_DIR` points the spawned `claude` at its own config dir, so Loki authenticates as a specific account independent of your terminal login (e.g. work vs personal). Set authoritatively over any inherited env; Windows/Linux isolate `.credentials.json` per dir. The setup wizard asks for it.
- **Guest rate limiting** — `GUEST_RATE_PER_HOUR` (default 10; `0` = unlimited) caps each non-owner's requests per rolling hour to protect your subscription. Owners are never limited; the wizard asks for the value. Persisted in `state/ratelimit.json`.
- **Private command hook** — copy `loki/platforms/slack/private_commands.example.py` → `private_commands.py` (gitignored) and implement `try_handle(ctx)`; it runs before normal dispatch, so you can gate a heavy pipeline to named trusted users and stream progress — without touching core or forking.
- **Tests** — +7 cases (rate limiter windows/isolation/disable, dedicated-account env passthrough + parent-auth stripping). 34 total, green on the CI matrix.

## [v1.2.0] 2026-07-10

Cross-platform, proactive, and observable.

- **macOS / Linux support** — POSIX process groups (`start_new_session` + `killpg`) replace Windows-only tree-kill; `setup.sh` wizard; systemd/launchd autostart examples in SETUP.md.
- **Scheduler** — owner DM `!schedule daily|weekly|once … <prompt>` (+ `list` / `remove`). Fires run at the owner's permission mode and post back to the DM. Missed recurring slots roll forward (no catch-up spam); a missed `once` fires on boot. Persisted in `state/schedules.json`.
- **Parallel jobs** — up to `JOB_CONCURRENCY` (default 2) Claude processes at once; same-conversation jobs stay strictly ordered so `--resume` continuity holds. `!jobs` lists running/queued with ids, `!cancel <id>` kills exactly one, `!stop` now cancels everything. Cancelled jobs no longer resurrect through the stale-resume retry.
- **`!usage [days]`** — usage report (calls, ok/fail, total time, by user / by kind) from a metadata-only ledger (`state/usage.jsonl`, 90-day retention).
- **`!learn <note>`** — appends to a private learnings inbox (`state/learnings.md`) to feed your own memory/review process.
- **Test suite + CI** — 27 pytest cases over the core (allowlist fail-closed, queue ordering/cancel, scheduler math, dedup, i18n parity, output parsing); GitHub Actions matrix: Ubuntu / Windows / macOS × Python 3.10 / 3.12.
- Korean command aliases for everything new: `!작업목록` `!취소` `!사용량` `!예약` `!학습`.

## [v1.1.0] 2026-07-10

Guest access, made opt-in and observable.

- **Guest path allowlist** — `<WORK_DIR>/loki/loki.md` is the single list of what non-owners may read. Everything else (the rest of WORK_DIR, other drives, `~/.claude`) is tool-denied per request via a `--settings` file. Fail-closed: an empty manifest means guests see nothing. Edits apply without a restart.
- **No side doors** — guests also lose `Bash`, `Skill` and `Task`; their working directory is pinned to the loki folder; the shared scope is explained to the model in-prompt.
- **Channel kill switch** — owner DM commands `!block <channel_id>` / `!unblock <channel_id>` (persisted in `state/blocked_channels.json`); channel-invite notices now include a one-tap block hint.
- **Owner `!summary <channel_id>`** — summarize another channel's recent conversation from your DM.
- Docs: permission tier table, owner command reference, permission-posture setup guide.

## [v1.0.0] 2026-07-09

First public release.

- **Slack adapter** (Socket Mode): owner DMs + channel `@mentions`
- **Claude Code brain**: spawns the official `claude -p` under your subscription login — no API key, no metered billing
- **Permission model**: read-only `plan` by default with a fail-closed boot self-test; `bypassPermissions` opt-in; guests always forced read-only
- **Context awareness**: thread mentions see the thread, bare channel mentions see recent channel history (windowed, capped) — all wrapped in a prompt-injection guard
- **Conversation continuity** per thread via `--resume`
- **Ops hardening**: serial job queue, event dedup (Slack redelivery), `!stop` kill switch, timeout tree-kill, UTF-8 enforcement, hidden console windows, metadata-only logging
- **i18n**: bot messages in English (default) or Korean (`LOKI_LANG=ko`)
- **Setup wizard** (`setup.ps1`) + autostart launcher + connection diagnostics (`tools/diag.py`)
