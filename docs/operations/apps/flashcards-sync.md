# Flashcards Sync

## 1. Overview

`flashcards-sync` is the server-side companion to the [flashcards](flashcards.md) web app. It persists per-user FSRS card state so the same user can study from multiple devices and converge their review schedule. The web client posts to `/api/sync` on the same hostname; the path is forwarded by the gateway to this service.

Source: https://github.com/gjcourt/flashcards-sync . Plan: lab/04-009 Phase 7 (cross-device sync).

## 2. Architecture

Single-replica `Deployment` co-located in the `flashcards-prod` (and `flashcards-stage`) namespace with the web app. Listens on port 8080. Backed by a 3-instance CNPG Postgres cluster (`flashcards-db-production-cnpg-v1`) with iSCSI-backed PVCs and daily Barman Cloud backups to S3 (`s3://gjcourt-homelab-backup/production/flashcards-sync`).

- **Database**: CNPG `Cluster` per env — `flashcards-db-production-cnpg-v1` / `flashcards-db-staging-cnpg-v1`. Bootstrap user `app` owns DB `app`; service auths via `flashcards-db-credentials` Secret.
- **Storage**: 5Gi/instance (prod), 2Gi/instance (staging) on `truenas-iscsi-ssd`.
- **Backups**: daily at 02:00:05 UTC (prod) / 02:00:10 UTC (staging) via Barman Cloud plugin; 30d retention (prod) / 14d (staging).
- **Networking**: routed via the existing `flashcards-https` HTTPRoute on `flashcards.burntbytes.com` (and `flashcards.stage.burntbytes.com`) with a `PathPrefix /api` match. No separate hostname.
- **Egress**: DNS + the in-namespace CNPG instances on 5432.

## 3. URLs

- **Staging**: https://flashcards.stage.burntbytes.com/api/...
- **Production**: https://flashcards.burntbytes.com/api/...

Direct service-level access (cluster-internal): `flashcards-sync.flashcards-prod.svc.cluster.local:8080`.

## 4. Configuration

Env vars are split between a ConfigMap (`flashcards-sync-container-env`) and the credentials Secret (`flashcards-db-credentials`):

| Var | Source | Notes |
|---|---|---|
| `PORT` | ConfigMap | `8080` |
| `LOG_LEVEL` | ConfigMap | `info` |
| `NODE_ENV` | ConfigMap | `production` (both envs — Node convention, not k8s env) |
| `AUTH_MODE` | ConfigMap | `single-user` |
| `SINGLE_USER_ID` | ConfigMap | `george` |
| `DB_HOST` | ConfigMap | CNPG `-rw` service FQDN |
| `DB_PASSWORD` | Secret | from `flashcards-db-credentials.password` |
| `DATABASE_URL` | Composed | `postgres://app:$(DB_PASSWORD)@$(DB_HOST):5432/app?sslmode=disable` |

## 5. Usage instructions

The web app calls `/api/sync` automatically when running with sync enabled. There's no separate UI on this service — it's an HTTP-JSON backend for the SPA.

Smoke test:

```bash
kubectl -n flashcards-prod port-forward svc/flashcards-sync 8080:8080
curl http://localhost:8080/healthz
```

## 6. Monitoring & alerting

- **Database**: CNPG exposes a `PodMonitor` (`monitoring.enablePodMonitor: true`) — Prometheus scrapes per-instance metrics; standard CNPG dashboards apply.
- **Application logs**: `kubectl -n flashcards-prod logs deploy/flashcards-sync`.
- **Health probes**: `/healthz` (HTTP) for readiness; TCP-8080 for liveness — split per the [adding-an-app probe convention](../2026-05-02-adding-an-app.md#health-probes) so a single transient flap can't co-restart the pod.

## 7. Disaster recovery

- **Backup strategy**: daily `ScheduledBackup` via the Barman Cloud plugin to S3. WAL is also archived continuously.
- **Restore procedure**: follow the CNPG PITR docs — `externalClusters.flashcards-db-backup` is already wired in `database.yaml`, so a new cluster can bootstrap from the object store.
- **Secrets**: `flashcards-db-credentials` is the only stateful secret. Loss requires generating new credentials, encrypting via SOPS, and rotating in-place (or restoring from backup).

## 8. Troubleshooting

- **`502` on `/api/*`**: HTTPRoute `flashcards-https` should have a `PathPrefix /api` rule pointing at the `flashcards-sync` service. Check both backends exist and are Ready: `kubectl -n flashcards-prod get svc flashcards flashcards-sync`.
- **CNPG cluster stuck bootstrapping**: confirm the `flashcards-db-credentials` Secret exists in the target namespace. The CNPG operator logs the missing-secret condition under `kubectl describe cluster flashcards-db-production-cnpg-v1 -n flashcards-prod`.
- **`DB_PASSWORD` empty in pod env**: the Secret must be named `flashcards-db-credentials` and have a `password` key. The deployment uses `secretKeyRef`, so a typo silently empties the env var rather than failing the pod.
- **Image pull failure**: shares `ghcr-secret` with the flashcards web app — if the web pulls fine but sync doesn't, double-check `imagePullSecrets` in the sync Deployment manifest.
- **iSCSI volume not provisioning**: same path as every other CNPG cluster in the repo — see `truenas-iscsi-monitor` runbook for diagnosis.

## 9. Image bumps

CI (`.github/workflows/image.yml` in `gjcourt/flashcards-sync`) tags as `YYYY-MM-DD` (then `YYYY-MM-DD-N`) and `YYYY-MM-DD-<sha>` (SHA-pinned). Per `AGENTS.md`, the new tag must be strictly greater than the currently deployed one. The first deploy uses the SHA-pinned tag to force a clean pull; subsequent bumps use the daily tag.

To list published tags:

```bash
gh api /users/gjcourt/packages/container/flashcards-sync/versions --jq '.[0].metadata.container.tags[]'
```
