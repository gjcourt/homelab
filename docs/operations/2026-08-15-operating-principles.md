---
title: Operating principles — staging, registries, access, API versions
status: Stable
created: 2026-08-15
updated: 2026-08-15
updated_by: gjcourt
tags: [operations, practice, staging, gotchas]
---

# Operating principles — staging, registries, access, API versions

Standing decisions about how this cluster is operated, as distinct from how any one component behaves. Promoted 2026-08-15.

---

## staging fixed size testbed

**staging envs (esp. CNPG DBs) should be fixed-size stable mirrors of prod so they're a real rehearsal testbed**

the operator wants **staging environments to be fixed-size, stable mirrors of prod** so they function as a real rehearsal testbed — not drifted, half-broken clones.

**Why:** discovered 2026-07-03 while rehearsing the Immich v3 migration on staging: `immich-db-staging-cnpg-v1` had drifted to **20Gi** (prod is **10Gi** after the expansion) AND had a replica (`-6`) wedged on a WAL-segment gap and crashlooping for 9 days. A degraded, wrong-sized staging DB undermines its value as a pre-prod rehearsal for risky ops.

**How to apply:** when touching staging manifests, size staging DB/PVCs to mirror prod's shape (or a deliberate fraction), keep them healthy, and treat staging as a first-class rehearsal env. Follow-up work item: normalize `apps/staging/immich` (and other CNPG staging clusters) to a fixed size aligned with prod, and add health monitoring so staging degradation is caught early. Relates to the "do staging fully first" rule in the Immich v3 upgrade runbook.

---

## staging no shared physical device

**Never point a homelab staging/preview overlay at a single-connection physical device shared with prod — use the simulator/mock; prod owns the device.**

A staging/preview environment must **not** connect to a single-connection physical device that
production also uses. Point staging at a **simulator/mock** transport instead; **prod owns the
device exclusively**.

**Why:** Discovered on Vibrato 2026-07-18 (). Both staging + prod set
`LEVA_HOST=10.42.7.11` (the one physical ito espresso controller, limited TCP slots). Two failures:
1. **Contention** — after the connection self-heal (watchdog + re-handshake) deployed to both, staging
 actively re-grabbed the ito's single telemetry stream every ~20s and **starved prod** (prod Monitor
 went blank; scaling staging to 0 instantly restored prod's stream).
2. **Safety** — a staging PR-preview wired to the real machine can **send it commands** (trigger a
 shot / actuate hardware). Preview envs rebuild from every open PR, so untrusted/in-progress code
 would drive real hardware.

**How to apply:** In the staging overlay, set the transport to sim/mock (e.g. Vibrato:
`LEVA_TRANSPORT=sim` on `apps/staging/<app>/deployment-patch.yaml`). Staging still validates the full
app stack (UI, WS, chart) on canned frames. Leave the real-device egress netpol in place (harmless
when unused) so flipping one env to real-hardware for a specific test stays easy. This generalizes to
any shared single-owner resource a preview env shouldn't mutate.

---

## homelab image registry

**New gjcourt/* images use ghcr.io (auto-auth via GITHUB_TOKEN). The legacy gjcourt/snapcast and gjcourt/go-librespot on Docker Hub are vestigial forks; don't use them as the precedent for new images.**

When adding a new first-party container image to gjcourt/homelab:

- **Push to `ghcr.io/gjcourt/<name>`** — confirmed in `AGENTS.md` ("ghcr.io | Container image registry (`gjcourt/<app>`)") and matches every new image: vitals, golinks, overture, signal-bridge, pingo, mopidy.
- **Authenticate via `${{ secrets.GITHUB_TOKEN }}`** with `permissions: { packages: write }`. No operator-set Docker Hub creds required.

The two legacy images on Docker Hub — `gjcourt/snapcast`, `gjcourt/go-librespot` — are forks of upstream projects kept on Docker Hub for backwards compatibility. They are **not** the precedent for new images. The snapcast deployment references them as-is for compat, but new sidecars should use ghcr.io.

**Why:** Historically, an attempt to push the new mopidy image to docker.io because the snapcast deployment had docker.io siblings. The agent's PR worked but required setting up `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` repo secrets — friction the user shouldn't have to deal with. I had to push a fix commit (#436 retag → ghcr.io) before the workflow could run.

**How to apply:** When spawning an agent to add a new image + build workflow:

- Explicitly tell them ghcr.io is the registry, not docker.io.
- Reference the existing `.github/workflows/build-mopidy.yml` as the canonical pattern (GITHUB_TOKEN auth, date-tagged: `YYYY-MM-DD`, fallback `YYYY-MM-DD-N`, multi-arch `linux/amd64,linux/arm64` via QEMU + buildx).
- Don't let the agent infer the registry from `apps/base/snapcast/deployment.yaml` — those are the legacy forks.

**★ GHCR "repo:null" 403 GOTCHA (2026-07-25).** A GHA `GITHUB_TOKEN` push gets `403 Forbidden` (`unexpected status from HEAD request … 403`) when the target GHCR package **already exists but is NOT linked to a repo** — i.e. it was first created by a manual PAT push (`make image`). Check with `gh api user/packages/container/<name> --jq .repository.full_name` → `null` = unlinked. **Fix: delete the stale package so the next GHA push re-creates it auto-linked (+ public):** `gh api --method DELETE user/packages/container/<name>` (needs `delete:packages` scope → `gh auth refresh -h github.com -s delete:packages` first; and Some tooling blocks interactive DELETEs; run it directly if so.). Non-destructive alt = package Settings → *Manage Actions access* → add the repo with **Write**. After either, `gh run rerun <id>`. **2026-07-25: migrated golinks, changes, vitals, soundbyte from manual `make image` to GHA `image.yml`** (push to default branch + workflow_dispatch; tags `YYYY-MM-DD`, `YYYY-MM-DD-<sha7>`, `latest`; the immutable `date-sha` is the one to pin in homelab). golinks deployed `2026-07-26-57ffc01` (golinks-prod ns; host go.burntbytes.com; unknown shortcode now 302→`/admin?new=<code>`). Repos already on GHA (left alone): vibrato, pingo, flashcards, burntbytes, modemscope, netscope, thermalscope, ladder, tempo-interview. cadence skipped (killed venture).

---

## homelab lan access

**kubectl commands to the homelab cluster work from this Mac — Stale "no LAN access" note was wrong.**

**kubectl works on this Mac** against the homelab cluster (`https://10.42.2.20:6443`). The earlier "no LAN access" note was from a different session or network state.  when I offered to wait for them to run kubectl commands.

`ping` and `ssh` to `10.42.2.x` may still require VPN or not — don't assume either way. Use kubectl for cluster diagnostics; it has been confirmed working.

**How to apply:** Run `kubectl` commands directly. Do not ask the user to paste kubectl output just because of this old memory.

---

## verify api versions

**Before writing a manifest with an alpha/beta API version, confirm served=true on the cluster**

Before using any non-`v1` API version (e.g. `v1alpha3`, `v1beta1`) in a manifest, verify it's actually served:

```bash
kubectl get crd <name> -o jsonpath='{range .spec.versions[*]}{.name}{" served="}{.served}{"\n"}{end}'
```

A CRD can list a version in its spec while having `served=false` — Flux dry-run will fail with "no matches for kind X in version Y" and block ALL reconciliation for that kustomization until fixed.

**Why:** Used `gateway.networking.k8s.io/v1alpha3` for `BackendTLSPolicy` — it appeared in the CRD spec but `served=false`. Flux was completely blocked for ~45 min, preventing unrelated fixes (openwebui netpol) from applying.

**How to apply:** Whenever writing a CRD-backed resource with an alpha/experimental apiVersion, run the served check above first. Prefer the highest served stable version.

---
