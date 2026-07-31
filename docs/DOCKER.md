# Running Loki in Docker (NAS, home server, VPS)

Loki's one real constraint is that the machine has to be awake. A laptop that
gets closed at 6pm is a bot that stops answering at 6pm and misses every
`!schedule`. Putting it in a container on something that stays on — a Synology
NAS, a mini PC, a home server — fixes that, and the container is also a
tighter fence than a desktop account: Claude can only reach the folders you
mounted.

The image carries the `claude` CLI and Python. It carries **no credentials** —
the Claude login and your Slack tokens are mounted at run time.

## 1. Configure

```bash
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
cp .env.example .env        # fill in SLACK_BOT_TOKEN, SLACK_APP_TOKEN, ALLOWED_USER_ID
mkdir -p work state
```

Leave `WORK_DIR` out of `.env` — compose sets it to `/work` inside the
container. What matters is the left side of the `./work:/work` mount: point it
at the folder you actually want Loki working in.

```yaml
volumes:
  - /volume1/projects:/work        # Synology example
```

**Set `TZ`.** Containers are UTC by default, and the scheduler runs on local
time — without this, `!schedule daily 09:00` fires at 09:00 UTC.

```bash
echo "TZ=Asia/Seoul" >> .env
```

## 2. Log Claude in (once)

The container needs its own Claude login, stored in the `claude-config`
volume so it survives restarts and rebuilds.

```bash
docker compose run --rm --entrypoint claude loki
```

Run `/login` at the prompt, open the URL it prints on any machine, and paste
the code back. Then `/exit`. Verify:

```bash
docker compose run --rm --entrypoint claude loki --version
```

This uses your existing Pro/Max subscription exactly like a terminal login —
no API key, no metered billing.

## 3. Run

```bash
docker compose up -d
docker compose logs -f loki
```

You should see the read-only self-test pass, then `Connecting to Slack`. DM
your bot `hello` to confirm.

To serve Discord or Telegram instead, set `LOKI_PLATFORM` in `.env` or
uncomment the `command:` line in `docker-compose.yml`. One platform per
container — a second platform means a second container with its own `state/`,
since both run the scheduler.

Health and liveness work the same as on a host:

```bash
docker compose exec loki python -m loki status
docker compose exec loki python -m loki doctor
```

## Updating

```bash
git pull
docker compose build --pull      # picks up the latest claude CLI too
docker compose up -d
```

Your login (`claude-config`) and history (`state/`) both survive. To pin the
CLI instead of tracking latest, set `CLAUDE_VERSION` under `build.args`.

## Synology notes

- **Container Manager → Project → Create**, point it at this folder, and it
  will use `docker-compose.yml` as-is. Or run the commands above over SSH.
- The one-time `/login` step needs an interactive terminal — do it over SSH,
  not through the Container Manager UI.
- Bind-mount permissions are the usual stumbling block. The image runs as root
  so mounts just work; to run unprivileged instead, uncomment `user:` in
  `docker-compose.yml` and set it to the uid/gid that owns your share
  (`id yourname` over SSH), then make sure `state/` is writable by it.
- Give it a static path on a volume that doesn't sleep — a spun-down disk adds
  seconds to every request.

## What the container changes about the security model

Everything in [SECURITY.md](SECURITY.md) still applies, with one improvement
and one thing to keep in mind:

- **Better:** write mode (`CLAUDE_PERMISSION_MODE=bypassPermissions`) is far
  less alarming here. Claude can only touch what you mounted, so a bad
  instruction reaches `/work` and nothing else — not your home directory, not
  your other drives. If you were ever going to enable write mode, this is the
  place.
- **Unchanged:** whoever controls the owner's Slack still controls Loki at the
  owner's permission level, and a folder you mount is shared in full. Mount a
  project directory, not the whole NAS.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Missing required setting: …` at boot | `.env` isn't being read — check `env_file` resolves and the file isn't empty |
| `Could not find the claude executable` | Image built without the CLI; rebuild with `docker compose build --pull` |
| Schedules fire at the wrong hour | `TZ` unset — see step 1 |
| `Invalid auth` from Slack | Tokens are workspace-specific; regenerate and restart |
| Claude asks to log in on every request | The `claude-config` volume isn't mounted, or you logged in without it |
| Everything forgotten after a restart | `state/` isn't mounted |
