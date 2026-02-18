# Secrets & SOPS

These rules apply globally — secrets touch YAML manifests, scripts, and docs.

- Never create plaintext secrets.
- Follow existing patterns for encrypted secret files and related documentation.
- When adding new required secret values, document how to generate/rotate them.
- Validate SOPS-encrypted files with `sops filestatus <file>` before committing.
- For placeholder values in docs, use clearly fake values (e.g., `your-secret-value-here`).
- Include TODO notes in docs for any manual secret setup steps needed.
