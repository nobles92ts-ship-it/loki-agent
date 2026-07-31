# Loki — Slack ⇄ Claude Code, containerised so it can live on a NAS or home
# server instead of a desktop that gets closed at night.
#
# The image carries the official `claude` CLI (Loki's brain) and Python. It
# carries no credentials: the Claude login and the Slack tokens are mounted at
# run time, so the image itself is safe to rebuild and share.
#
# Node base rather than Python base — the claude CLI is an npm package, and
# Debian's python3 (3.11) already satisfies Loki's 3.10+ requirement.
FROM node:22-bookworm-slim

# git: Claude reaches for it constantly in a work dir. ripgrep: its file search.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates git ripgrep tzdata \
    && rm -rf /var/lib/apt/lists/*

# The CLI updates often; pin with --build-arg CLAUDE_VERSION=x.y.z for a
# reproducible image.
ARG CLAUDE_VERSION=latest
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && npm cache clean --force

# A bind-mounted work dir is usually owned by a different uid than the one
# running in here, and git refuses to touch such a repo ("dubious ownership")
# — which breaks the most common thing Claude does in a project folder. Safe
# to relax in a single-purpose container.
RUN git config --system --add safe.directory '*'

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY loki ./loki

# state/ holds sessions, schedules, checklists, budgets and the log — mount a
# volume here or a restart forgets everything. /claude-config holds the login;
# both exist in the image so an unmounted run still starts (and still warns).
RUN mkdir -p /app/state /claude-config
VOLUME ["/app/state"]

# Give the spawned claude its own config dir by default, so a mounted volume
# keeps the login across restarts (see docs/DOCKER.md for the one-time login).
ENV CLAUDE_CONFIG_DIR=/claude-config \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

CMD ["python", "-m", "loki"]
