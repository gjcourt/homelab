# bench-cloud — Claude Code agent image (docs/plans/2026-09-25-bench-cloud-agent.md).
#
# One image for both the console pod and the task Jobs. Everything is pinned:
# an agent image that drifts on rebuild makes "it worked yesterday" unanswerable.
# Bump versions deliberately, in a PR.
FROM node:22-bookworm@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd

ARG CLAUDE_CODE_VERSION=2.1.283
ARG GO_VERSION=1.27.1
ARG GO_SHA256=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
# Matches the cluster (v1.35.0); stay inside the +/-1 minor skew.
ARG KUBECTL_VERSION=v1.35.0
ARG KUBECTL_SHA256=a2e984a18a0c063279d692533031c1eff93a262afcc0afdc517375432d060989
ARG GH_VERSION=2.101.0
ARG GH_SHA256=9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8
# Repos that lint and test with uv (agent-sandbox's CI does); without it agents
# fall back to a pip venv on a different Python (SANDBOX-8, 2026-10-24).
ARG UV_VERSION=0.12.24
ARG UV_SHA256=b4dfaef47d491a7296981f8374a4595f55dbf84e8937c8ecd2983574d8bb3da6

ENV PATH=/usr/local/go/bin:/home/node/go/bin:$PATH \
    BENCH_CLOUD_JOB_TEMPLATE=/usr/local/share/bench-cloud/job.yaml \
    DISABLE_AUTOUPDATER=1 \
    WORKSPACE=/home/node/work

# Toolchains the agents' repos test with (Go, Node, Python, make/gcc), plus the
# plumbing: git, rsync/ssh for hestia, tmux for the console, jq/openssl for the
# GitHub App token helper.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git jq less make openssh-client \
      openssl procps python3 python3-pip python3-venv python3-yaml ripgrep rsync tmux \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/uv.tgz "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
 && echo "${UV_SHA256}  /tmp/uv.tgz" | sha256sum -c - \
 && tar -C /tmp -xzf /tmp/uv.tgz \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/ \
 && rm -rf /tmp/uv* && uv --version

RUN curl -fsSL -o /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" \
 && echo "${GO_SHA256}  /tmp/go.tgz" | sha256sum -c - \
 && tar -C /usr/local -xzf /tmp/go.tgz && rm /tmp/go.tgz \
 && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl \
 && curl -fsSL -o /tmp/gh.tgz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
 && echo "${GH_SHA256}  /tmp/gh.tgz" | sha256sum -c - \
 && mkdir -p /usr/local/lib/gh && tar -C /tmp -xzf /tmp/gh.tgz \
 && mv "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/lib/gh/gh && rm -rf /tmp/gh*

# Installed as root into the global prefix, so the non-root agent cannot
# self-update it; the auto-updater is also disabled below. Version moves only
# through this ARG.
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" && npm cache clean --force

# The Plane MCP server for ticket Jobs (agent-desk plan, "Plane access inside a
# Job"), in its own venv, every package hash-pinned. 0.3.4 is the version tested
# against Plane CE on staging.
COPY images/bench-cloud/plane-mcp-requirements.txt /tmp/plane-mcp-requirements.txt
RUN python3 -m venv /opt/plane-mcp \
 && /opt/plane-mcp/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/plane-mcp-requirements.txt \
 && rm /tmp/plane-mcp-requirements.txt

# The bench gate's path checker (HOMELAB-85), copied from homelab at build
# time like job.yaml below, and the pre-push hook that runs it. bench-task
# points a clone's core.hooksPath at the hook only when the repo has a gate.
# python3-yaml (apt, above) is the checker's one dependency.
COPY scripts/bench-gate/check.py /usr/local/share/bench-cloud/bench-gate/check.py
COPY images/bench-cloud/git-hooks/pre-push /usr/local/share/bench-cloud/git-hooks/pre-push

# GitHub App auth: git and gh both get short-lived installation tokens.
COPY images/bench-cloud/github-app-token /usr/local/bin/github-app-token
COPY images/bench-cloud/gh-wrapper /usr/local/bin/gh
COPY images/bench-cloud/entrypoint.sh /usr/local/bin/bench-entrypoint
COPY images/bench-cloud/bench-task /usr/local/bin/bench-task
COPY images/bench-cloud/bench-agents-sync /usr/local/bin/bench-agents-sync
# Ticket mode: the comment hook and its settings (hooks and the Plane tool deny
# list), passed by bench-task with --settings only when TASK_TICKET is set.
COPY images/bench-cloud/bench-ticket /usr/local/bin/bench-ticket
COPY images/bench-cloud/ticket-settings.json /usr/local/share/bench-cloud/ticket-settings.json
COPY images/bench-cloud/agents-allowlist /usr/local/share/bench-cloud/agents-allowlist
# The task CLI + Job template, so the console can start task Jobs itself
# (bench-console ServiceAccount; task pods run as bench-agent and cannot).
# Build context is the repo root for exactly these two files.
COPY scripts/bench-cloud/bench-cloud /usr/local/bin/bench-cloud
COPY scripts/bench-cloud/job.yaml /usr/local/share/bench-cloud/job.yaml
# Managed (system-level) Claude Code policy: the house rules and the deny list.
# Users cannot override managed settings, including the agent itself.
COPY images/bench-cloud/managed-settings.json /etc/claude-code/managed-settings.json
COPY images/bench-cloud/CLAUDE.md /etc/claude-code/CLAUDE.md
# The trailing version checks fail the BUILD, not the first task, if a tool is missing.
RUN chmod 0755 /usr/local/bin/github-app-token /usr/local/bin/gh /usr/local/bin/bench-entrypoint /usr/local/bin/bench-task /usr/local/bin/bench-cloud /usr/local/bin/bench-agents-sync /usr/local/bin/bench-ticket \
      /usr/local/share/bench-cloud/git-hooks/pre-push \
 && git config --system credential.https://github.com.helper '!/usr/local/bin/github-app-token --git-credential' \
 && git config --system init.defaultBranch main \
 && go version && kubectl version --client=true && /usr/local/lib/gh/gh --version \
 && claude --version && python3 --version && tmux -V && rsync --version | head -1 \
 && /opt/plane-mcp/bin/python -c "import plane_mcp" \
 && /usr/bin/python3 /usr/local/share/bench-cloud/bench-gate/check.py --help >/dev/null

# node:22-bookworm ships uid/gid 1000 "node" — reuse it.
USER 1000:1000
WORKDIR /home/node
ENTRYPOINT ["/usr/local/bin/bench-entrypoint"]
CMD ["sleep", "infinity"]
