#!/usr/bin/env bash
# Prepare the agent's home, then run the command (default: sleep, for the console).
set -euo pipefail
mkdir -p "$WORKSPACE"

# Remote Control refuses an untrusted workspace ("Workspace not trusted"), and
# the trust dialog can't be answered in a headless pod. Mark $WORKSPACE trusted
# in the user config — which lives on the PVC for the console, so this also
# preserves everything else Claude Code stored there.
node -e '
  const fs = require("fs"), p = process.env.HOME + "/.claude.json", w = process.env.WORKSPACE;
  let c = {}; try { c = JSON.parse(fs.readFileSync(p)); } catch (e) {}
  c.projects = c.projects || {};
  c.projects[w] = Object.assign(c.projects[w] || {}, { hasTrustDialogAccepted: true });
  fs.writeFileSync(p, JSON.stringify(c, null, 2), { mode: 0o600 });
'

exec "$@"
