---
status: planned
last_modified: 2026-10-10
summary: "ROI-ranked fixes from the 2026-10-10 review: S3 backup verification, restore drill, UPS, Pushover, egress lockdown"
---

# Plan: ROI-ranked homelab improvements (2026-10-10 review)

## Context

A whole-repo review on 2026-10-10 found the workload hygiene in good shape:
every production container has requests and a memory limit, all but one have
probes, every production database is a 3-instance CNPG cluster with nightly S3
backups, and 29 of 32 workload namespaces carry a NetworkPolicy. The remaining
risk is concentrated in four places: **backups nobody watches**, **power**,
**alert delivery**, and **one unfenced internet-facing workload**.

This plan ranks the fixes by return on effort. It does not replace the
existing plans it touches ([read-only PVC recovery](2026-09-01-readonly-pvc-recovery.md)
and its script design in PR #1390, [local deadman mesh](2026-08-09-local-deadman-mesh.md),
[network resilience](2026-05-06-network-resilience-and-bgp-completion.md));
it decides where they sit in the queue.

## Facts verified live on 2026-10-10

These came from the cluster and Prometheus, not from docs. Several contradict
`docs/STATUS.md`.

| Fact | Evidence | Consequence |
|---|---|---|
| `cnpg_collector_last_available_backup_timestamp` is **0 for every cluster** | Prometheus: age ≈ 497,691h (the Unix epoch) | With the barman-cloud **plugin**, CNPG no longer populates this. Any alert built on it would fire forever or be suppressed into uselessness. The alert has to be sourced elsewhere (§2). |
| Prod `ScheduledBackup`s are completing | Last 7 `Backup` CRs in each of the 5 prod namespaces: `completed` | Backups *run*. Whether they *restore* is unknown — see next row. |
| linkding-prod's last three nightly backups report an **identical WAL range** (`0000000A…71 → …73`) and take 4–7s | `kubectl get backups -n linkding-prod` | Taken on a replica (`-4`). The primary last archived `…72` on **2026-10-01** (`pg_stat_archiver`). If `…73` has never been archived, a restore from these backups cannot reach consistency. Probably benign (near-idle DB) but **unproven** — this is exactly what a restore drill answers. |
| `seconds_since_last_archival` is 236h on linkding/flashcards/memos prod | Prometheus | Normal for near-idle DBs. **Staleness of archival is not a usable alert signal here** — it would fire constantly. |
| `ContinuousArchiving=True` on all 10 clusters, prod and stage | Cluster `.status.conditions` | STATUS.md's "WAL archiving broken on several staging clusters" is **stale**. |
| Cilium uses KubePrism (`localhost:7445`); no HelmRelease is suspended | `infra/controllers/cilium/values.yaml:116`, no `suspend: true` in repo | STATUS.md's "do not resume the suspended Cilium HelmRelease" is **stale**. |
| Critical alerts route to `email-critical` | `kube-prometheus-stack/values.yaml:649` | STATUS.md's "3 critical Flux alerts route to the null receiver" is **stale**. |
| `AGENTS.md` hardcodes `flux suspend kustomization apps-production` for read-only-PVC recovery | Found by PR #1390 | Wrong for anything under `infra/` — including zigbee2mqtt, the workload in #1080. **The documented recovery silently fails on the incident that motivated it.** |
| Renovate PR #1462 is green and mergeable but has sat since 2026-09-22 | `gh pr view 1462` | Something in the merge path (automerge rule or renovate-review gate) is not doing its job. |

## Ranking

ROI = (likelihood × blast radius the item removes) ÷ effort. Effort: **XS** <1h ·
**S** ≈ half a day · **M** 1–2 days · **L** multi-day. "Operator" means it
needs hands on hardware or a vendor UI, not a PR.

| # | Item | Effort | Value | Why it ranks here |
|---|---|---|---|---|
| 1 | Fix stale/incorrect runbooks (STATUS.md + AGENTS.md suspend bug) | XS | Med-High | Wrong instructions during an incident cost more than no instructions. Pure doc change. |
| 2 | CNPG backup verification against S3 + archiving alert | S | High | Backups are the last line of defence and are currently unobserved. Checks the artifact, not the operator's claim; alerts through existing homelabscope. |
| 3 | CNPG restore drill (linkding-prod + immich-prod) | S | High | The only way to know backups work. The identical-WAL-range finding makes this concrete, not hypothetical. |
| 4 | Confirm ZFS snapshot coverage of the democratic-csi zvols | XS (operator) | High | ~30 non-DB PVCs (Home Assistant, Mealie, Audiobookshelf, Jellyfin config…) have no in-cluster backup. One look in the TrueNAS UI decides whether item 10 is urgent. |
| 5 | UPS for PDU, switch, gateway, hestia (+ NUT) | $ + S (operator) | High | Confirmed no UPS. Switch + PDU are documented accepted SPOFs; a power blip triggered the June outage. |
| 6 | Pushover for critical alerts + SMTP canary | S | Med-High | Every critical alert depends on one Gmail app password, and nothing proves that path works. |
| 7 | `cloudflare-tunnel` egress/ingress policy | S | Med | The only internet-facing workload is the only one with no policy. Canary connector makes a bad policy harmless. |
| 8 | Implement `recover-readonly-pvc.sh` (#1390) | M | High impact, low frequency | 8/13 cost ~14h of Immich DB downtime. Design is done; this plan only moves it up the queue behind the cheaper items. |
| 9 | Monitoring-coverage audit for charts shipping `serviceMonitor: false` | S | Med | Open follow-up from the 2026-08-05 cert postmortem. democratic-csi first — it is the storage SPOF. |
| 10 | App-data PVC backups (VolSync/restic → S3) | L | High *if* item 4 finds a gap | Gated on item 4. If hestia snapshots cover the zvols, this drops to "off-site copy" priority. |
| 11 | Hygiene batch | XS each | Low | Unblock Renovate, `patchesStrategicMerge`, stale PR triage, orphan extents, policies for 2 small namespaces. |

Items 1–4 are one afternoon of work combined and remove the largest unknown
(do the backups restore?). Items 5–7 are the next tier. 8–10 are the larger
investments.

---

## 1. Fix stale and incorrect runbooks — XS

**Changes**

- `AGENTS.md`: replace the hardcoded `flux suspend kustomization apps-production`
  with "find the owner first":
  ```bash
  kubectl -n <ns> get deploy <name> -o jsonpath='{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}'
  flux suspend kustomization <that-name> -n flux-system
  ```
  This is the live bug #1390 found; it is worth landing independently of the script.
- `docs/STATUS.md`:
  - Cilium entry → mark resolved (KubePrism live); drop "do not resume".
  - Monitoring-enhancement entry → critical alerts now reach `email-critical`, not `null`.
  - CNPG WAL archiving entry → resolved as of 2026-10-10 (all 10 clusters `ContinuousArchiving=True`); replace with the "backups unmonitored / restore unproven" finding until items 2–3 land.
  - Bump `Last updated`.

**Done when:** a reader following STATUS.md or AGENTS.md during an incident is not misled. PR is docs-only.

## 2. CNPG backup & archiving alerts — S

**Decision (2026-10-10 review): verify the artifact in S3, not the operator's
claim.** Two in-cluster sources were considered and rejected as the primary
signal:

- `cnpg_collector_last_available_backup_timestamp` — 0 under the barman-cloud
  plugin (verified). A metric that already broke silently once.
- kube-state-metrics `customResourceState` over `Backup` CRs — works, but a
  `completed` phase is CNPG's *claim*. linkding-prod's identical-WAL-range
  backups are `completed` too; that is the claim in doubt.

**Design**

a. **`cnpg-backup-verify-<cluster>` CronJobs** (one per prod cluster, a
   kustomize component so all five share one definition), every 6h:

   1. List `s3://gjcourt-homelab-backup/production/<app>/<serverName>/base/`,
      read the newest `backup.info`.
   2. **Fail** if its `end_time` is older than 26h (24h schedule + 2h slack).
   3. **Fail** if its `end_wal` segment is not present under `wals/` — the
      restorability check. A base backup whose end WAL was never archived
      cannot reach consistency, and nothing else in the stack tests this.
   4. Exit 0 otherwise, printing both ages.

   Alerting needs **no new pipeline**: homelabscope already projects
   `kube_cronjob_status_last_successful_time` onto
   `homelabscope_job_last_success_seconds` and alerts on staleness and
   absence. A failing check, a crashing checker, and a deleted checker all
   surface the same way, by name. Add each CronJob to the homelabscope
   recording rules with a `max_age` of ~13h (two missed runs).

   - **Image:** `amazon/aws-cli` pinned by digest (Renovate keeps it current) +
     a short shell script; no custom image to maintain.
   - **Credentials:** a new **read-only** IAM user scoped to `s3:ListBucket` +
     `s3:GetObject` on the bucket (operator step in AWS). Do **not** reuse the
     per-app write credentials — the checker should be unable to damage what
     it checks.
   - **Egress:** CiliumNetworkPolicy with `toFQDNs` for S3, copying
     `apps/base/vitals/networkpolicy.yaml`.
   - **Placement:** `infra/configs/cnpg-backup-verify/` (it is
     cluster-infrastructure monitoring, like `pvc-writeprobe`).

b. **WAL-archiving failure (fast path)** — the S3 check runs every 6h; this
   catches a broken archive within the hour from metrics that *do* work,
   restricted to the primary so replicas' stale archiver stats don't trip it:

   ```promql
   (cnpg_pg_stat_archiver_last_failed_time > cnpg_pg_stat_archiver_last_archived_time)
     and on (namespace, pod) (cnpg_pg_replication_in_recovery == 0)
   ```
   `for: 1h`, severity `warning`, staging muted by the existing `.+-stage`
   route. Not used: `seconds_since_last_archival` — days-old on idle DBs.

**Validation**

- Run the checker by hand (`kubectl create job --from=cronjob/...`) against
  all five clusters before enabling the schedule. **Its first run answers the
  linkding question** — if `…73` is missing it fails, which is the correct
  outcome and feeds item 3.
- Fault-inject: point one checker at a nonexistent prefix in a PR preview and
  confirm the homelabscope alert fires and routes.
- Replay (b) over the 10-day retention: it must be silent today. Replay
  cannot prove it fires — the known failures (~742h / ~582h ago) predate
  retention — so fault-inject it too, on a staging cluster with its
  ObjectStore pointed at a bad bucket.

**Done when:** a stale *or unrestorable* prod backup produces an email within ~13h, and a dead checker does too.

## 3. CNPG restore drill — S

**Scope (decided): two databases — linkding-prod and immich-prod.** linkding
for the identical-WAL-range question; immich because it is the largest and
has 190 historical archive failures. The other three get coverage from item
2's restorability check and can be drilled later.

**Order:** run item 2's checker first. If it reports linkding's end WAL
missing, the drill confirms the failure mode instead of discovering it.

**Procedure** (one-off, by hand, manifests written into the runbook rather
than committed to `apps/`):

1. Create a scratch namespace `restore-drill` (not Flux-managed; delete after).
2. Copy the linkding `ObjectStore` into it, using the **read-only** credentials
   from item 2 rather than the app's write credentials.
3. Create a 1-instance `Cluster` with `bootstrap.recovery` from the
   barman-cloud plugin source pointing at
   `s3://gjcourt-homelab-backup/production/linkding`.
   **Safety invariant: the drill Cluster must have no WAL-archiving or backup
   plugin configured against the prod `destinationPath`.** A recovered cluster
   archiving into the source path can clobber the prod WAL stream. Use a
   distinct `serverName` and no `plugins:` archiving block. The read-only
   credentials make this a hard guarantee, not just a convention.
4. Wait for `Ready`. Compare against prod: row counts of the main tables,
   `max(date_modified)` on bookmarks.
5. Record time-to-restore and the result in
   `docs/operations/2026-05-02-cnpg-backup-recovery.md` (the existing runbook),
   then delete the namespace.
6. Repeat for immich-prod.

**If linkding fails to reach consistency:** the fix is to make the backup's
end WAL get archived on idle clusters — e.g. take backups on the primary
(`target: primary` on the ScheduledBackup, so `pg_backup_stop` switches and
archives the segment), or set `archive_timeout`. Decide once the failure mode
is observed rather than guessing now.

**Follow-up (out of scope):** automate as a monthly CronJob that restores the
smallest DB and reports through homelabscope.

**Done when:** linkding-prod and immich-prod each have a recorded successful restore with a measured RTO.

## 4. Confirm ZFS snapshot coverage of the CSI zvols — XS (operator)

The democratic-csi parent dataset name lives in the SOPS-encrypted driver
config, and nothing in the repo records whether a TrueNAS periodic snapshot
task covers it. homelabscope already monitors ZFS snapshots on hestia, but
only for `main/family` and `main/homes` (`zfs-snapshot-main-*`, both fresh as
of 2026-10-10). `main/apps` has a task (id 10), but that is the Custom Apps
dataset, not the CSI zvols.

**Steps (TrueNAS UI → Data Protection → Periodic Snapshot Tasks):**

1. Find the CSI parent dataset (`sops -d infra/controllers/democratic-csi/secret-driver-config.yaml | grep -i parent`).
2. Is there a recursive snapshot task on it? Schedule and retention?
3. Is anything replicating it off hestia (alcatraz, cloud)?
4. Write the answer into `hosts/hestia/README.md`.
5. If a task exists (or is created), add a `zfs-snapshot-<dataset>` entry to
   the homelabscope heartbeat so it is monitored like family/homes.

Note: crash-consistent zvol snapshots are fine for SQLite/file-based apps in
practice but are not application-consistent; that matters for item 10.

**Decision this feeds:** if snapshots exist and replicate off-box → item 10
becomes low priority. If snapshots exist on hestia only → item 10 is about
off-box copies. If nothing → item 10 jumps to just behind item 3.

## 5. UPS — $ + S (operator)

**Confirmed 2026-10-10: there is no UPS.** All 4 Talos nodes share one Rack
Switch (USWED42) and one USP PDU Pro — documented accepted SPOFs — and a
blip on that shared path triggered the June outage. This also answers the
[local deadman mesh](2026-08-09-local-deadman-mesh.md) plan's `blocked_on`.

**What to protect** (in priority order — a UPS that keeps the nodes up while
the switch or gateway drops buys nothing):

1. USP PDU Pro (→ all 4 nodes) and the Rack Switch.
2. The UCGF gateway (BGP peer, DNS upstream, the route to the internet).
3. hestia — every PVC is iSCSI from it; if it drops, the cluster wedges
   read-only (the 8/13 failure mode) even with power to the nodes.
4. alcatraz and the modem — nice-to-have.

**Sizing:** sum the measured draw first — the USP PDU Pro reports per-outlet
power in UniFi, and hestia's IPMI exporter reports its PSU draw. Target is
riding through blips plus **≥10 minutes** at full load for a clean shutdown,
not running through outages. Requirements:

- **Pure sine wave output** (active-PFC server PSUs can misbehave on
  stepped-sine units).
- **USB HID** interface supported by NUT (most APC and CyberPower units are).
- Rack-mount if the rack has space; tower otherwise.

Candidate class: a 1500VA/1000W pure-sine line-interactive unit (e.g.
CyberPower CP1500PFCLCD, APC SMT1500/SMC1500). If items 1–3 together draw
more than ~500W, step up to 2200VA+ or split hestia onto its own unit — a
second UPS also removes hestia from the shared-power failure domain.

**Monitoring and shutdown (phase 2, after install):**

- Plug the UPS USB into **hestia** and enable the built-in TrueNAS **UPS
  service** (it is NUT). hestia becomes the NUT server.
- Scrape it with a NUT exporter (compose on hestia, same pattern as the IPMI
  exporter) and alert on `on_battery` (warning, immediate) and
  `battery.runtime < 5m` (critical) — through Pushover (item 6) so it reaches
  a phone.
- **Shutdown order matters:** the cluster must stop before hestia, or the
  last minutes on battery reproduce the read-only-remount incident. v1:
  alerting only, and hestia shuts down at low battery as a last resort.
  Orchestrated cluster shutdown (`talosctl shutdown` driven by NUT) is a
  separate follow-up once there is a real runtime number.

**Done when:** pulling the wall plug keeps the switch, nodes, gateway and hestia up, and fires an on-battery alert on the phone.

## 6. Push channel for criticals + SMTP canary — S

Today: criticals → Gmail SMTP only; the healthchecks.io deadman proves the
webhook leg but not SMTP (STATUS.md says so explicitly). An expired app
password silently drops every real alert while the deadman stays green, and
email has no acknowledgement — `repeat_interval: 4h` is the only nag.

**Decision: Pushover.** Options considered:

| | Pushover | ntfy.sh | Telegram |
|---|---|---|---|
| Cost | $5 one-time per platform | Free; reserved private topics are paid | Free |
| Alertmanager | **Native** `pushover_configs` | Webhook → needs a template/bridge | Native `telegram_configs` |
| Ack-until-acknowledged | **Yes** — emergency priority repeats until acked | No | No |
| Outside failure domain | Yes | Yes (hosted) / No (self-hosted) | Yes |
| Secrecy | App token + user key | Topic name *is* the secret on free tier | Bot token |
| healthchecks.io integration | Yes | Yes | Yes |

Pushover wins on the two things that matter for paging: native support (no
bridge to maintain inside the failure domain) and emergency priority, which
closes the "email has no ack" gap.

**Steps**

1. Operator: buy Pushover (iOS/Android), create an application, note the
   app token and user key.
2. SOPS secret `alertmanager-pushover` in `monitoring`, mounted the same way
   as `alertmanager-deadman`; reference via `token_file` / `user_key_file`
   (never inline in the plaintext values ConfigMap).
3. `email-critical` receiver gains a `pushover_configs` entry with
   `priority: 2` (emergency: retry 5m, expire 2h). Warnings stay email-only.
4. Point the healthchecks.io deadman check's notifications at Pushover too,
   so "alerting itself is dead" also reaches the phone.
5. **SMTP canary:** a daily always-firing `SmtpCanary` alert routed by email
   to an address that forwards to a healthchecks.io **email** check
   (healthchecks.io accepts inbound-email pings). Period 1d, grace 6h. If the
   Gmail leg breaks, that check goes red and notifies via Pushover.

**Done when:** a test critical alert pages the phone and repeats until acknowledged, and revoking the Gmail app password turns the SMTP canary check red within ~30h.

## 7. `cloudflare-tunnel` network policy — S

The single public entry point (`apps/production/cloudflare-tunnel/`) has no
policy; a compromised cloudflared could reach every Service in the cluster.
It has **no staging overlay**, so a wrong policy applied to both replicas
takes down every public hostname.

**Decision: canary connector, not a second tunnel.** Cloudflare spreads
requests across every connector registered to a tunnel, so a third
cloudflared pod running the same config joins the existing tunnel and takes a
share of real traffic. The policy is applied to that pod only:

| If the policy wrongly blocks… | Effect | Blast radius |
|---|---|---|
| Egress to Cloudflare's edge | Canary never registers; `/ready` false | **None** — it takes no traffic |
| Egress to the Gateway | Canary registers, its share (~⅓) of requests 502 | Partial, visible within minutes |
| DNS | Canary can't resolve the edge → as row 1 | None |

Rollback for any of these is scaling the canary to 0 — no policy edit, no
Flux suspend. Rejected: Cilium per-endpoint audit mode (set live via
`cilium-dbg`, not GitOps, reset on pod restart) and a second tunnel (new
credentials and DNS records for a temporary test).

**Steps**

1. **Observe (1 week, no change):** record cloudflared's real flows with Hubble
   (relay is enabled):
   `hubble observe -n cloudflare-tunnel --verdict FORWARDED -o compact --since 1h`
   at a few points across the week, including after a pod restart (to catch
   edge-discovery DNS) and while browsing every published hostname.
2. **PR 1 — canary:** add `cloudflared-canary` (1 replica, identical spec,
   extra label `netpol: enforced`) and a CiliumNetworkPolicy selecting
   `netpol: enforced`, following `apps/base/vitals/networkpolicy.yaml`:
   - Egress: kube-dns with the DNS-proxy rule FQDN policy needs; Cloudflare
     edge **7844 TCP+UDP** (`*.argotunnel.com`, `*.v2.argotunnel.com`); the
     production Gateway on 443 (`toEntities: ingress` or the gateway
     endpoints — whichever step 1 shows); nothing else. Anything else step 1
     surfaces gets an explicit rule or a written reason to drop it.
   - Ingress: port 2000 (metrics + probes) from the monitoring namespace and
     `host` only.
3. **Verify (48h soak):** `hubble observe -n cloudflare-tunnel --verdict DROPPED`
   is empty; the canary is `Ready`; an external curl of every hostname in the
   README table succeeds repeatedly (hitting all three connectors); a
   `curl` from the canary to another app's Service is **denied**.
4. **PR 2 — enforce:** add `netpol: enforced` to the main Deployment's pod
   template and delete the canary.

**Follow-on (hygiene batch):** `thermalscope` and `truenas-iscsi-monitor` —
same pattern, lower stakes, no canary needed.

**Done when:** both production replicas run under the policy with zero drops over 48h and a denied lateral `curl`.

## 8. Implement `recover-readonly-pvc.sh` — M

Design is complete in PR #1390 (`2026-09-01-recover-readonly-pvc-script.md`,
the *how* for [2026-09-01-readonly-pvc-recovery.md](2026-09-01-readonly-pvc-recovery.md)),
with four open questions. This plan's only contribution is ordering: land
item 1's AGENTS.md fix now (the dangerous part), then merge #1390 after
answering its open questions, then implement. No new design here.

## 9. Monitoring-coverage audit — S

From the 2026-08-05 cert postmortem's open follow-up. Charts with no
ServiceMonitor/PodMonitor reference found in the review: **democratic-csi**,
cnpg (operator — the *clusters* are covered by `enablePodMonitor`),
barman-cloud, vector, snapshot-controller.

For each: does the chart expose metrics? Is there a value to turn on a
ServiceMonitor? Enable it, confirm `up == 1`, and decide whether any alert is
warranted. democratic-csi first: it is the storage path every stateful app
depends on, and its 550-restart crashloop (#1367) went unalerted.

## 10. App-data PVC backups — L (gated on item 4)

If item 4 finds no off-box copy of the CSI zvols, adopt **VolSync** with the
restic mover to the existing `gjcourt-homelab-backup` bucket, using the
existing `truenas-iscsi-snapshot` VolumeSnapshotClass for copy-on-snapshot
consistency. Start with the irreplaceable-and-small set: Home Assistant,
Mealie, Audiobookshelf metadata, zigbee2mqtt (its `coordinator_backup.json` and
network key are painful to lose). Skip caches and anything rebuildable.

Full design deferred until item 4's answer is in — written as its own plan.

## 11. Hygiene batch — XS each

- **Renovate stuck:** find why green #1462 hasn't merged (automerge rule for
  digests vs. the renovate-review gate from #1554), fix the rule, then merge
  the backlog (#1462, #1553, #1560, #1561). Note the overlap with
  [2026-09-28-renovate-fixer-bench-cloud.md](2026-09-28-renovate-fixer-bench-cloud.md):
  that plan fixes PRs that *can't* merge; #1462 *can* and doesn't, so this is a
  merge-path bug it won't cover.
- **Jellyfin overlays:** `patchesStrategicMerge` → `patches` (`kustomize edit fix`)
  in `apps/{staging,production}/jellyfin/`.
- **Stale PRs:** decide #1262 (cadence, open since 2026-08-02 — the README
  calls `cadence.burntbytes.com` an orphan public hostname, so either land it or
  remove the hostname) and #1390 (see item 8).
- **6 orphan iSCSI extents:** audit against PVs and remove (operator, destructive).
- **`make test`/`make lint` need Docker:** document it in the README's
  Development section, or fall back to native binaries when Docker is absent.

## Out of scope

- Self-built images pinned by date-sha tag rather than digest (14) — tags are
  immutable in practice; low value.
- Network resilience / LB subnet move — has its own plan.
- Storage SPOF architecture (#1080) beyond items 8 and 10.

## Decisions (review of 2026-10-10)

1. **Item 2 source:** S3-artifact verification via per-cluster CronJobs
   feeding homelabscope, plus the in-cluster WAL-failure alert as a fast path.
   KSM `customResourceState` rejected — it reports CNPG's claim, not the
   artifact.
2. **Item 3 scope:** two databases — linkding-prod and immich-prod.
3. **Item 5:** no UPS exists → buy, sized from measured draw; NUT on hestia.
4. **Item 6:** Pushover, emergency priority for criticals; deadman also
   routed to it.
5. **Item 7:** canary connector on the existing tunnel, after a week of
   Hubble observation.

## Remaining open questions

- Item 5: power draw of PDU + switch + UCGF + hestia (determines one UPS or two).
- Item 2: barman's `serverName` per cluster (S3 prefix layout) — confirm by
  listing the bucket before writing the checker.

## Sequencing

| PR / task | Items | Type |
|---|---|---|
| A | 1 | docs |
| — | read-only IAM user, Pushover account, UPS purchase | operator (start now; long-lead) |
| B | 2 | checker CronJobs + homelabscope rules + WAL alert |
| — | 3, 4 | operator sessions, after B's first run (no PR until findings are written up) |
| C | 6 | alertmanager values + secret |
| D1, D2 | 7 | canary policy (after 1 week of Hubble observation), then enforce |
| E | 5 phase 2 | NUT exporter + alerts, after the UPS is installed |
| E… | 8, 9, 10, 11 | one PR each |
