---
status: in-progress
last_modified: 2026-06-17
summary: "Route critical Alertmanager alerts to email via Gmail SMTP (replaces dead Signal channel)"
---

# Plan: Alertmanager critical-alert email delivery via Gmail SMTP

## Context

The homelab's critical alerts — the three Flux reconciliation alerts (#937/#940),
plus `NodeFilesystemReadOnly` and `LokiPushErrors` — currently route to
Alertmanager's **`null` receiver** and are pushed nowhere. They evaluate
correctly and are visible in Alertmanager/Grafana, but no human is notified.
This gap opened when signal-cli + hermes were decommissioned (#936), which
obsoleted Phase 2 of [2026-05-09-monitoring-enhancement.md](2026-05-09-monitoring-enhancement.md)
(Signal-based routing). That plan explicitly left "critical-alert delivery needs
a real channel (email/ntfy), TBD."

This restores delivery by reusing the **same Gmail SMTP relay Authelia already
uses** (`apps/production/authelia/configuration.yaml` — `smtp.gmail.com`, auth
`gjcourt@gmail.com`, a Gmail app password). Alertmanager has a native
`email_configs` receiver, so this is not wiring *through* Authelia — both point
at the same upstream relay. No new service to run (unlike ntfy). Credential
pattern follows the precedent in
[2026-02-17-authelia-smtp-notifier.md](2026-02-17-authelia-smtp-notifier.md).

**Decisions:**
- Deliver to **`gjcourt+alerts@gmail.com`** (Gmail plus-addressing → same inbox, one filter sorts it into a label).
- **Critical severity only** for now; widen to warnings later if useful.

## Changes

### 1. SMTP credential secret (operator-encrypted)
Secrets are namespace-scoped, so the Gmail app password must exist as a Secret
in the **`monitoring`** namespace (Authelia's copy in `authelia-prod` can't be
read cross-namespace). Same value as `AUTHELIA_NOTIFIER_SMTP_PASSWORD` in
`apps/production/authelia/secret-authelia.yaml`.

- Template `infra/controllers/kube-prometheus-stack/secret-alertmanager-smtp.yaml.example`
  (the `.example` suffix is outside the `.sops.yaml` `.*secret.*\.yaml$` rules)
  ships in-tree; the real `secret-alertmanager-smtp.yaml` is wired into
  `kustomization.yaml` `resources:`.
- **Operator step:** copy the template, fill the password, `sops -e -i` it, commit.
  The `.sops.yaml` rule `infra/.*secret.*\.yaml$` encrypts it; the
  `infra-controllers` Flux Kustomization already has SOPS decryption enabled.
- Until the encrypted secret exists, `kustomize build infra/controllers` / CI
  **fails on the missing resource** — the intended gate.

### 2. Mount the secret
`infra/controllers/kube-prometheus-stack/values.yaml` —
`alertmanager.alertmanagerSpec.secrets: [alertmanager-smtp]`. Mounted read-only
at `/etc/alertmanager/secrets/alertmanager-smtp/password`, keeping the password
out of the (plaintext) values ConfigMap.

### 3. Routing + receiver
Same `values.yaml`, `alertmanager.config` block — leaving the `null` default,
Watchdog→null route, and inhibit_rules intact:
- `config.global`: `smtp_smarthost: smtp.gmail.com:587`, `smtp_from`,
  `smtp_auth_username`, `smtp_auth_password_file` (the mounted file),
  `smtp_require_tls: true`.
- New `email-critical` receiver → `to: gjcourt+alerts@gmail.com`, `send_resolved: true`.
- New route `severity = "critical"` → `email-critical`, `repeat_interval: 4h`.

### 4. Docs
This plan doc; `docs/reference/monitoring.md` §7 (note the receiver/route/secret);
regen `docs/plans/README.md` via `make plans-index`.

## Files

| File | Change |
|---|---|
| `infra/controllers/kube-prometheus-stack/secret-alertmanager-smtp.yaml.example` | new — template |
| `infra/controllers/kube-prometheus-stack/secret-alertmanager-smtp.yaml` | new — **operator**, SOPS-encrypted |
| `infra/controllers/kube-prometheus-stack/kustomization.yaml` | wire secret into `resources:` |
| `infra/controllers/kube-prometheus-stack/values.yaml` | secrets mount + global SMTP + receiver + route |
| `docs/reference/monitoring.md` | document the email receiver/route/secret |

## Verification

- `kubectl get secret alertmanager-smtp -n monitoring` exists; Alertmanager pod
  rolls **clean** (a missing/mis-mounted secret blocks startup);
  `kubectl exec -n monitoring alertmanager-... -- ls /etc/alertmanager/secrets/alertmanager-smtp/`.
- Config loaded: `amtool config show` shows the `email-critical` receiver + route.
- **Live test:** a real `severity=critical` delivers to `gjcourt+alerts@gmail.com`
  within ~5 min, with a `[RESOLVED]` when cleared. (Watchdog routes to `null` —
  not a valid test.)
- No regressions: `flux get kustomizations -A` green.

## Gotchas

- **Port 587, not 465.** Authelia uses 465 (implicit TLS); Alertmanager's
  supported path is 587 + STARTTLS. Same app password works on both.
- **Gmail rewrites From** to the authenticated account → `smtp_from: gjcourt@gmail.com`.
- **Password duplicated** across two SOPS secrets (a reflector for one value isn't worth it).
- **Sequencing:** secret + values land in one merge so Flux applies the secret
  before the Alertmanager StatefulSet rolls.

## Out of scope
- ntfy / push-to-phone (new service; revisit only if email proves insufficient).
- Warnings to email (start critical-only).
- Per-alert routing/grouping beyond the single critical route; Alertmanager HA.
