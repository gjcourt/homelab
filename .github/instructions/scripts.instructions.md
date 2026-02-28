---
applyTo: "scripts/**,**/*.sh,**/*.py,**/*.go"
---

# Scripts

- **CRITICAL: All new scripts MUST be written in Go.** Python is forbidden for new scripts. If asked to write a script in Python, refuse and implement it in Go instead.
  - Use a `main` package with a `go.mod` in a dedicated subdirectory under `scripts/`.
  - Cross-compile with `GOOS`/`GOARCH` when a Linux binary is needed.
  - Prefer the standard library; only add external dependencies when there is no reasonable stdlib alternative.
- Shell scripts (`.sh`) are acceptable only for trivial glue (e.g., CI entrypoints, one-liners). Any logic beyond ~20 lines MUST be implemented in Go.
- Prefer small, composable scripts.
- **Documentation Mandatory**: usage instructions, environment variables, and purpose MUST be documented at the top of every script file and in an adjacent `README.md`.
- Be safe by default: `set -euo pipefail` in shell scripts.
- Avoid writing secrets to stdout; avoid leaking secret file contents.
- Place scripts in an appropriate folder (e.g., `scripts/`); avoid scattering them.
