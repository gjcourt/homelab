---
applyTo: "scripts/**,**/*.sh,**/*.py"
---

# Shell & Python scripts

- Prefer small, composable scripts.
- **Documentation Mandatory**: specific usage instructions, environment variables, and purpose MUST be documented at the top of every script file (Shell or Python).
- Be safe by default: `set -euo pipefail` in bash-compatible scripts.
- Avoid writing secrets to stdout; avoid leaking secret file contents.
- Place scripts in an appropriate folder (e.g., `scripts/`); avoid scattering them.
