---
status: in-progress
last_modified: 2026-11-02
summary: "Self-hosted Plane CE (the tracker for lab 03-042's agent desk) on staging: Flux HelmRelease of plane-ce with post-render fixes, CNPG Postgres, LAN/VPN only, in-cluster webhooks to the dispatcher, restore drill before production"
---

# Plane on the homelab (staging first)

## Goal

Run [Plane](https://plane.so) Community Edition on the cluster as the tracker for the agent desk in
lab project 03-042 (gjcourt/lab#106). George chose self-hosted Plane on 2026-10-18 because he wants
Linear's UI without a paid SaaS plan. This plan covers **staging only**, up to a rehearsed restore.
Production is a follow-up PR once staging has run for a while.

Out of scope here: the dispatcher, the ticket-to-PR link and the agent playbooks. They live outside
this repo (see 03-042). When this was written, the homelab repo was on the agent deny list until
bench-watch existed, so this deploy was ordinary homelab work.

*Updated 2026-11-02:* homelab is off the deny list. Bench Jobs may open homelab PRs that change only
`apps/**` and `docs/**`. The required check `bench-paths` fails a bench PR outside those paths, and
`bench-approval` holds each bench PR until George approves its head. Per the HOMELAB-85 plan's
decision 6, the approval gate replaces the bench-watch condition. See the
[HOMELAB-85 plan](2026-11-01-bench-homelab-paths-plan.md).

## What the research found (2026-10-18)

From Plane's docs and repos, then re-checked against the source: Plane v1.4.2, chart `plane-ce`
1.8.4 and `plane-mcp-server` v0.3.4. Three first-pass claims were wrong (webhooks, the rate limit,
api probes) and are corrected below.

- **Release:** Plane CE **v1.4.2** (23 Aug). Chart `plane-ce` from `https://helm.plane.so/`. The
  newest chart, 1.8.5 (13 Oct), is under two weeks old, so this plan pins **1.8.4** (29 Sep, also
  appVersion 1.4.2).
- **Services:** web, space, admin and live (port 3000), api (port 8000), worker and beatworker.
  Routes: `/` → web, `/spaces/` → space, `/god-mode/` → admin, `/live/` → live, `/api/` and
  `/auth/` → api.
- **Backing services:** Postgres, Valkey, RabbitMQ and MinIO. Each has `<svc>.local_setup`, which
  can be set to `false` for an external instance. Credentials can come from existing Secrets
  (`pgdb_existingSecret`, `app_env_existingSecret`, `live_env_existingSecret`,
  `doc_store_existingSecret`, `rabbitmq_existingSecret`).
- **No GitHub integration in CE.** It is a paid feature run by a separate `silo` service. CE keeps
  old GitHub tables but has no routes for them. The REST API, webhooks, work item links and
  personal API keys (`X-API-Key`) are free in CE.
- **Webhooks to private addresses are allowed by setting.** Plane's SSRF check blocks private and
  loopback addresses unless the host is in `WEBHOOK_ALLOWED_HOSTS` (or the address is in
  `WEBHOOK_ALLOWED_IPS`). It checks both when the webhook is saved and at delivery, and connects to
  the address it validated. Deliveries are HMAC-signed (`X-Plane-Signature`) and retried up to 5
  times with backoff. Delivery runs in the worker pod.
- **API rate limit is a setting:** `API_KEY_RATE_LIMIT`, default `60/minute`, chart
  `env.api_key_rate_limit`.
- **MCP:** the official `makeplane/plane-mcp-server` (v0.3.4, MIT) works with CE in stdio mode,
  run with `PLANE_BASE_URL`, `PLANE_API_KEY` and `PLANE_WORKSPACE_SLUG`. That's how bench-cloud
  Jobs would reach it. CE rejects PQL queries (filter client-side), and the customer, initiative,
  release, work-log and work-item-type tools target paid editions.
- **Known chart gaps:**
  - no probes on web/space/admin/live, which gives 502s during rollouts (helm-charts#318). The api
    already has a readinessProbe (`GET /` on 8000);
  - no securityContext, and the images run as root (#339). The backend Dockerfile has no `USER`
    but makes `/code` world-writable, so non-root likely works there; the frontend images are
    unchecked;
  - `ingress.appHost` drives both `WEB_URL` and CORS (#275).
- **Not found:** a documented supported Postgres range (the chart bundles 15.7), Kubernetes sizing
  guidance (Compose says 2 CPU / 4 GB minimum), and websocket requirements for live.

## Design

### Install: Flux HelmRelease plus post-render patches

Apps here are plain manifests, and Helm is used only under `infra/`. Plane is the exception
because it has seven deployments that change together with each release, and hand-maintaining
their rendered output would drift. So:

- a `HelmRepository` for `https://helm.plane.so/` and a `HelmRelease` for `plane-ce` 1.8.4, under
  `apps/base/plane/` and `apps/staging/plane/`, in the shape of `infra/controllers/cnpg/release.yaml`
  (drift detection on, values from a ConfigMap, secrets from existing Secrets);
- **`postRenderers` with Kustomize patches** close the chart gaps:
  - a `readinessProbe` on web, space, admin and live (HTTP on 3000); the api's existing probe
    stays. Following the repo's probe rule
    ([adding an app](../operations/2026-05-02-adding-an-app.md#health-probes)), a `livenessProbe`
    goes in only where it checks something readiness doesn't;
  - securityContext: `runAsNonRoot`, dropped capabilities, `seccompProfile: RuntimeDefault`;
  - `WEBHOOK_ALLOWED_HOSTS` on the api and worker, set to the dispatcher's in-cluster service name
    only. It's not secret, so it lives in the patch where it's reviewable, not in SOPS. Never a
    CIDR: that would let any workspace admin point webhooks at anything on the LAN.
- Renovate tracks the chart version like every other HelmRelease.

**Pod security:** namespaces here are labelled either `restricted` or `privileged`. The target is
`restricted`. If the Plane images can't run as non-root (#339 says they run as root today), the
fallback is `baseline` on `plane-stage` only, recorded here with the reason. Step 3 finds out
which.

*Recorded 2026-10-18 (manifests PR):* the namespace enforces `baseline` and audits and warns at
`restricted`. From the Dockerfiles: the web and admin images are nginx built to start as root,
and the chart-local Valkey, RabbitMQ and MinIO (plus its bucket Job) chown their data, so those
six can't meet `restricted` without image changes. The backend (api, workers, migrator), space and
live run as uid 1000 with all capabilities dropped, and a server-side dry run shows they pass
`restricted`. Step 3 confirms they actually start that way.

### Backing services

| Service             | Choice                                     | Why                                                                                                      |
| ------------------- | ------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Postgres            | **CNPG cluster** `plane-db-staging-cnpg-v1` | Same as every app: S3 WAL archive to `gjcourt-homelab-backup/staging/plane/`, iSCSI storage, PodMonitor |
| Valkey              | chart-local                                | Cache and queue state only; nothing to back up                                                           |
| RabbitMQ            | chart-local                                | Job queue; losing it loses in-flight background jobs only                                                |
| Uploads (S3)        | chart-local MinIO on a PVC, **staging only** | Simplest; decide production storage after step 6                                                         |

- **Postgres version: 15**, matching what the chart bundles and tests. 17 is what other apps run,
  but Plane documents no range; moving up is its own later change after an upgrade test.
- **The MinIO image is `docker.io/pgsty/minio`**, Pigsty's fork rather than upstream MinIO. Before
  production, check its provenance and maintenance, or move uploads to another S3 target. Staging
  uploads are throwaway.

### Routing and access

- **Hostname `plane.stage.burntbytes.com`** on `app-gateway-staging`, with the usual http → https
  redirect route and an https route.
- **No Cloudflare tunnel.** LAN and VPN only, like 03-042 says.
- **Chart ingress off.** Our HTTPRoute carries the path rules listed above. `ssl.externalTermination:
  true` makes `WEB_URL` https behind the gateway; confirm that fixes the CORS origin from #275.
- **Upload size:** the chart's limit settings are ingress-specific and don't apply to an HTTPRoute.
  Set `doc_upload_size_limit` (start at 5 MB, the chart default) and check that the gateway passes a
  file just under it.
- **Websockets:** live uses them for collaborative editing. Verify in step 4 that an upgrade passes
  the Cilium gateway.
- **NetworkPolicy:** default-deny, with gateway ingress via `fromEntities` (host, remote-node,
  ingress), app-to-app inside the namespace, and egress to the CNPG pods and DNS. If any Plane
  service calls its own public hostname, the hairpin needs the gateway VIP on 443 **and** the
  backend pod ports. Allow ingress to the api from the `bench-cloud` namespace now, so 03-042's
  Jobs can reach it later, and egress from the worker to the dispatcher's namespace and port for
  webhook delivery.

### Auth

- **Email and password.** George creates the instance admin on first visit ("Let's secure your
  instance"), then a workspace.
- **SMTP: none on staging.** Password login works without it. Codes, invites and resets need it;
  add it in God Mode for production.
- **No SSO/OIDC.** It's a Commercial feature, and Authelia can't front it without it.

### Secrets (SOPS, operator-only)

The PR ships `.yaml.example` files only. George encrypts the real ones:

- `secret-app-env.yaml`: `SECRET_KEY`, `DATABASE_URL` (CNPG app user), `REDIS_URL`, `AMQP_URL`;
- `secret-live-env.yaml`: `LIVE_SERVER_SECRET_KEY`;
- `secret-doc-store.yaml`: MinIO root credentials and the bucket name;
- the CNPG backup credentials, the same as the other staging clusters.

## Steps

1. **This plan PR.** Review and approve.
2. **Manifests PR.** While it's open, CI's preview build deploys it to staging (master plus open
   PRs), so staging testing happens before merge. It contains the namespace, HelmRepository, HelmRelease and values, post-render patches, CNPG
   Cluster and ObjectStore, HTTPRoutes, NetworkPolicy, `.yaml.example` secrets. Run `helm template`
   locally against 1.8.4 and attach the diff of rendered resources.
3. **First reconcile on staging.** Check that pods come up under `restricted`, or record the
   fallback. Check the migrator job finished and the api answers `/api/`.
4. **Smoke test:**
   - create the admin and a workspace and project;
   - create a ticket, comment on it, attach a file;
   - edit a page from two browsers (websocket check);
   - make a personal API key and read the ticket over the REST API from a pod in `bench-cloud`;
   - register a webhook to a throwaway in-cluster receiver on the allowlisted name, change the
     ticket, and confirm a signed delivery arrives. Confirm a non-allowlisted private URL is still
     refused.
5. **MCP check:** run `plane-mcp-server` in stdio mode from a bench-cloud pod against staging. List
   and update the test ticket, post a comment, change its state and attach a link.
6. **Restore drill:** restore the CNPG cluster from its S3 backup into a new cluster, point Plane at
   it, and confirm the test ticket and comment survive. Record the timings.
   *Done 2026-10-19, unplanned: two staging-rebuild deletions forced two real restores. Steps,
   timings and the `serverName` rule are in the [Plane runbook](../operations/apps/plane.md).*
7. **Production PR**, separate: production storage decision, SMTP, hostname, resources from
   staging's observed usage. *Opened 2026-10-20 (`apps/production/plane`); activation and the
   first-install checks are in [runbook section 7](../operations/apps/plane.md#7-production-first-install).*
8. **Access check, staging and production:** confirm the hostname loads on LAN and over the VPN,
   and does not resolve or connect from outside (phone on cellular with the VPN off, or an
   external DNS lookup). The hostname must not appear in
   `apps/production/cloudflare-tunnel/config.yaml`.

## Exit criteria (staging)

- [x] Plane CE 1.4.2 reconciles from Flux at chart 1.8.4, with readiness probes and securityContext
      patched in. *(2026-10-20: HelmRelease Ready at chart 1.8.4 / app 1.4.2 after #1595 merged to
      master.)*
- [ ] Reachable at `https://plane.stage.burntbytes.com` on LAN and VPN, and not from the internet.
      *(LAN confirmed 2026-10-19. VPN and the not-from-internet check not yet run.)*
- [x] A ticket, a comment, an upload and a two-browser page edit all work. *(Ticket, comment,
      upload: 2026-10-19. Two-browser edit, synced both ways in about 110 ms: 2026-10-20.)*
- [x] A bench-cloud pod can read and update a ticket over the REST API and over MCP stdio.
      *(2026-10-19, after the egress rule in #1596.)*
- [x] A webhook delivers to the allowlisted in-cluster host, and other private URLs are refused.
      *(2026-10-19: 4 signed deliveries; the in-cluster api, the gateway IP and localhost were
      refused.)*
- [x] A restore from S3 brings back the test data. *(2026-10-19, twice; about 6 minutes from
      cluster create to Ready. Timings and steps:
      [runbook section 5](../operations/apps/plane.md#5-restore-drill-plan-step-6--2026-10-19).)*
- [x] Pod security level recorded: `restricted`, or `baseline` with the reason. *(`baseline`,
      recorded 2026-10-18 under Pod security above.)*

## Risks

- **Migrations on upgrade.** Plane has open migrator issues (#8176, #7310, #8772). Every chart bump
  takes a CNPG backup first, and step 6's drill proves the way back.
- **Root images.** If patching to non-root breaks them, staging runs `baseline` until upstream fixes
  #339.
- **Fork MinIO image.** It's staging-only until checked (see Backing services).
- **Missed webhooks.** Retries stop after 5 attempts, and a dispatcher outage longer than that
  loses events. 03-042's dispatcher keeps a slow backstop poll; raise `API_KEY_RATE_LIMIT` if that
  poll and the agents share a key.
