# Running Loki on Codex

`LOKI_PROVIDER=codex` (or `!provider codex`) moves the owner's DM and the
console onto the Codex CLI. Restricted requests — guests, the owner in a shared
channel — are the hard part, because Loki's boundary for them is a
set of Claude Code deny rules that Codex cannot carry.

Requests that carry no settings file (owner DM, console) are unchanged by
everything below.

## Restricted requests: fallback or sealed

| `LOKI_RESTRICTED_MODE` | Restricted request runs on | Can read files? |
|---|---|---|
| `fallback` (default) | Claude, with the deny-rule settings file | only what the guest allowlist grants |
| `sealed` | Codex with no tools (`codex.run_sealed`) | Slack guests: granted text folders through Loki's capped reader; other restricted requests: nothing |

`sealed` never widens access: a sealed run has no file, shell, MCP, connector or
web tool, so it is strictly narrower than the Claude boundary.
`!provider claude` puts every request back on Claude whatever this says.

### Guests still read their shared folders (`loki/core/allowread.py`)

The model gets no tool back; Loki reads for it. Turn 1 shows the file listing
of the folders the guest manifest grants; the model either answers or replies
with only `{"read": [...], "search": [...]}`. Loki then reads/searches in Python
and turn 2 answers from that text. Rules:

- Every path is resolved (symlinks/junctions followed) and must land inside a
  granted root; walks never follow links. A granted root that later becomes a
  junction is refused too. Traversal and junction escapes are refused (tested;
  a live run with a junction to a canary leaked nothing).
- Narrower than the Claude grant: the worker tree, `loki/orgs`, credential files
  and hidden or secret-looking path components (`.env`, `*token*`, keys,
  databases, `private`, `credentials`) are excluded,
  and only text files and `.xlsx` spreadsheets are read (a row becomes one line
  with its column names).
- A manifest folder outside `WORK_DIR` is granted to this reader as exactly that
  folder — never a drive root, nor a folder holding `WORK_DIR`, the worker or
  the home folder. Claude runs still deny it.
- Caps: 300 listed entries, 5000 files walked, 6 files × 12k chars, 40 search
  hits (8 per file), 64 MiB searched, 40k chars per turn.

Not equivalent to Claude: no free browsing beyond two request rounds, no
binary files other than `.xlsx`, and listing/search stop at the caps. The owner
in a shared channel reads the folders that channel's guests can read.
Only the Slack adapter passes the grant; Discord/Telegram guests on sealed get
no file reads.

### Why not a Codex sandbox with an allowlist

Measured on Windows, Codex CLI 0.154.0: a read-restricted permission profile
either reads the whole disk (`:root=read`) or refuses to start (`:root=deny`
under the elevated backend, `C:/Users=deny` fails applying ACLs). There is no
"deny everything except these folders" that starts, so the guest allowlist
cannot be expressed. The sealed mode removes tools instead of fencing them.

### What seals a run (`codex.sealed_flags`)

- `--ignore-user-config --ignore-rules` — no `config.toml`, so no MCP servers.
- `--disable apps` — **required**: without it the ChatGPT account's connectors
  (Atlassian, Gmail, Drive, Slack, …) stay callable even with the user config
  ignored. Measured: 236 tools exposed in that state.
- `--disable shell_tool unified_exec code_mode_host …` — no shell; the remaining
  `exec` tool fails closed. Sub-agents inherit the same set (measured).
- `sandbox_mode="read-only"`, `shell_environment_policy.inherit="none"`,
  `project_doc_max_bytes=0` — second line of defence.
- Process env is an allowlist (`codex.sealed_env`), cwd an empty temp folder.

Probe: canary file outside cwd, canary env var, a write target and "call any MCP
tool", asked of fresh, resumed and sub-agent runs. Sealed: nothing leaked, no
`command_execution`/`mcp_tool_call`/`file_change` event. Positive control
(same probe, bypass sandbox): file, env and write all leaked — the probe works.

A private command that needs a restricted turn can call
`providers.sealable(providers.current())` and, when true, use
`run_sealed(prompt, None, images=...)`, doing any file reads and writes itself
in Python around the tool-less turn.
