# bench-cloud — Claude Code agent image (docs/plans/2026-09-25-bench-cloud-agent.md).
#
# One image for both the console pod and the task Jobs. Everything is pinned:
# an agent image that drifts on rebuild makes "it worked yesterday" unanswerable.
# Bump versions deliberately, in a PR.
FROM node:22-bookworm@sha256:363e1587494626837fa7f9a23bdb453d13b0ff3c67c705c2805cfc69c2d2fad7

ARG CLAUDE_CODE_VERSION=2.1.283
ARG GO_VERSION=1.27.1
ARG GO_SHA256=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
# Matches the cluster (v1.35.0); stay inside the +/-1 minor skew.
ARG KUBECTL_VERSION=v1.35.0
ARG KUBECTL_SHA256=a2e984a18a0c063279d692533031c1eff93a262afcc0afdc517375432d060989
ARG GH_VERSION=2.101.0
ARG GH_SHA256=9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8

ENV PATH=/usr/local/go/bin:/home/node/go/bin:$PATH \
    DISABLE_AUTOUPDATER=1 \
    WORKSPACE=/home/node/work

# Toolchains the agents' repos test with (Go, Node, Python, make/gcc), plus the
# plumbing: git, rsync/ssh for hestia, tmux for the console, jq/openssl for the
# GitHub App token helper.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git jq less make openssh-client \
      openssl procps python3 python3-pip python3-venv ripgrep rsync tmux \
 && rm -rf /var/lib/apt/lists/*

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

# GitHub App auth: git and gh both get short-lived installation tokens.
COPY github-app-token /usr/local/bin/github-app-token
COPY gh-wrapper /usr/local/bin/gh
COPY entrypoint.sh /usr/local/bin/bench-entrypoint
COPY bench-task /usr/local/bin/bench-task
# Managed (system-level) Claude Code policy: the house rules and the deny list.
# Users cannot override managed settings, including the agent itself.
COPY managed-settings.json /etc/claude-code/managed-settings.json
COPY CLAUDE.md /etc/claude-code/CLAUDE.md
# The trailing version checks fail the BUILD, not the first task, if a tool is missing.
RUN chmod 0755 /usr/local/bin/github-app-token /usr/local/bin/gh /usr/local/bin/bench-entrypoint /usr/local/bin/bench-task \
 && git config --system credential.https://github.com.helper '!/usr/local/bin/github-app-token --git-credential' \
 && git config --system init.defaultBranch main \
 && go version && kubectl version --client=true && /usr/local/lib/gh/gh --version \
 && claude --version && python3 --version && tmux -V && rsync --version | head -1

# node:22-bookworm ships uid/gid 1000 "node" — reuse it.
USER 1000:1000
WORKDIR /home/node
ENTRYPOINT ["/usr/local/bin/bench-entrypoint"]
CMD ["sleep", "infinity"]
