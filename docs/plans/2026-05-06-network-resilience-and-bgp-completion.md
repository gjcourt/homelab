---
status: planned
last_modified: 2026-05-06
summary: "Unified network resilience + BGP completion plan, phases A-F with GO gates"
supersedes: docs/plans/2026-05-05-bgp-phase4-revision.md
---

# Network Resilience and BGP Completion Plan

> **Context:** The 2026-05-05 wired-device incident
> (`docs/operations/incidents/2026-05-05-bgp-l2-wired-device-regression.md`)
> exposed several latent issues in the LAN/cluster networking design. This plan
> consolidates the fixes — from quick wins requiring no topology change through
> the full LB-pool migration that finally enables pure-BGP — into a single
> phased rollout. It supersedes `docs/plans/2026-05-05-bgp-phase4-revision.md`
> (which becomes Phase D below).

## Problems being solved

From the 2026-05-06 architecture critique:

| # | Problem | Severity | Phase |
|---|---------|----------|-------|
| 1 | All L2 leases concentrated on a single CP node | Major | A |
| 2 | AdGuard primary/secondary HA undermined by co-located L2 leases | Major | A |
| 3 | L2 lease failover takes ~15s on default timing | Moderate | A |
| 4 | Helm-flag changes silently skip Cilium DaemonSet rollout | Moderate | A |
| 5 | L2 policy still named `*-staging` (cluster-wide scope) | Minor | A |
| 6 | No alerting on L2 lease churn | Minor | A |
| 7 | Test plans don't cover wired same-subnet devices | Major | B |
| 8 | DNS dependency loop — wired clients lose name resolution if cluster DNS fails | Moderate | C |
| 9 | LB pool shares `/24` with cluster nodes and wired clients | Major | D |
| 10 | IoT/AV devices share L2 broadcast domain with the kube-apiserver | Major | E |
| 11 | BGP `listen range 10.42.2.0/24` accepts AS 65010 from any LAN host | Moderate | F |
| 12 | All LB services blanket-default to `externalTrafficPolicy: Cluster` | Moderate | F |
| 13 | `k8sServiceHost: 10.42.2.20` is a SPOF | Moderate | out-of-scope |

## Constraints

1. **Reversible per phase** — each phase ships its own rollback procedure.
2. **No simultaneous changes** — phases serialize except where explicitly parallel.
3. **Wired-VLAN-2 test device required** — every L2/BGP test matrix includes a real ARP-direct host (Apple TV, kitchen-pi, or a HifiBerry).
4. **Pre-flight gate before destructive phases** — Phases D and E require explicit operator GO/NO-GO; Phases A–C are low-risk.

---

## Phase A — Same-day L2 hygiene (no topology change)

Risk: low. Recoverable by reverting a single PR. No client-visible disruption expected.

> **Atomicity requirement:** A.1 through A.5 must ship as a single PR. Splitting A.1 and A.2 across two merges creates a transient window where AdGuard services are matched by both the cluster-wide policy and the per-service policies, causing ambiguous lease ownership.

### A.0 Apply stable speaker-pool labels to workers

**Pre-step for A.1 and A.2.** Hostname-based pinning is brittle — Talos nodes get a new auto-generated hostname after a re-image. Apply a durable custom label to each worker, then select on that label everywhere downstream:

```bash
# Pool A — speakers for AdGuard primary and the cluster-wide default
kubectl label node talos-lmh-kyf homelab.io/l2-speaker-pool=a
kubectl label node talos-18u-ski homelab.io/l2-speaker-pool=a

# Pool B — speakers for AdGuard secondary (forced different from primary)
kubectl label node talos-kot-7x7 homelab.io/l2-speaker-pool=b
```

Document this step in `docs/operations/talos-node-bootstrap.md` so a future node-replacement run reapplies the label. If the runbook doesn't yet exist, create it with this section.

### A.1 Restrict L2 announcements to worker nodes (default policy)

**Problem #1.** Edit `infra/configs/cilium/l2-announcement-policy.yaml` to add a worker-only `nodeSelector` and exclude AdGuard services (deferred to A.2's per-service policies):

```yaml
spec:
  externalIPs: true
  loadBalancerIPs: true
  serviceSelector:
    matchExpressions:
      - key: app.kubernetes.io/name
        operator: NotIn
        values: ["adguard"]
  nodeSelector:
    matchExpressions:
      - key: node-role.kubernetes.io/control-plane
        operator: DoesNotExist
```

Effect: cluster-wide policy covers everything **except** AdGuard, restricted to worker nodes. AdGuard is owned by per-service policies in A.2.

### A.2 Split AdGuard primary/secondary onto different L2 speakers

**Problem #2.** Add per-service `CiliumL2AnnouncementPolicy` resources with `serviceSelector` and the speaker-pool labels from A.0:

```yaml
# infra/configs/cilium/l2-announcement-policy-adguard-primary.yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumL2AnnouncementPolicy
metadata:
  name: l2-adguard-primary
spec:
  loadBalancerIPs: true
  serviceSelector:
    matchLabels:
      app.kubernetes.io/name: adguard
      homelab.io/dns-pool: primary
  nodeSelector:
    matchLabels:
      homelab.io/l2-speaker-pool: a
```

```yaml
# infra/configs/cilium/l2-announcement-policy-adguard-secondary.yaml
# Same shape, dns-pool=secondary, l2-speaker-pool=b — pinned to a different worker
```

Add labels to the matching services:
- `apps/base/adguard/service.yaml` — `homelab.io/dns-pool: primary`
- `apps/production/adguard/service-dns-secondary.yaml` — `homelab.io/dns-pool: secondary`

> **Note:** `homelab.io/*` is a custom label namespace local to this repo, not a Cilium API. Choosing a non-`cilium.io` prefix avoids implying upstream semantics.
>
> **Trade-off:** more YAML, but per-service ownership means a node failure breaks at most one DNS IP. The `homelab.io/l2-speaker-pool` labels survive node re-image as long as A.0's bootstrap step is followed.

### A.3 Tune L2 lease timing

**Problem #3.** Edit `infra/controllers/cilium/values.yaml` (after the existing `l2announcements:` block):

```yaml
l2announcements:
  enabled: true
  leaseDuration: "5s"
  leaseRenewDeadline: "3s"
  leaseRetryPeriod: "1s"
```

Cuts speaker-failover ARP outage from ~15s to ~5s.

> **Operator gotcha (Problem #4):** Helm-flag changes don't roll the Cilium DaemonSet automatically. After this phase merges and Flux reconciles, run:
> ```bash
> kubectl rollout restart ds/cilium -n kube-system
> kubectl rollout status ds/cilium -n kube-system
> ```

### A.4 Rename and document

**Problems #5 and #4.**

- Rename `l2-announcement-policy-staging` → `l2-announcement-policy-default` in `infra/configs/cilium/l2-announcement-policy.yaml:4`. (Cluster-wide scope; "staging" is misleading.)
- Add a header comment to `infra/controllers/cilium/values.yaml` listing values that require an explicit DaemonSet rollout: `l2announcements.*`, `bgpControlPlane.enabled`, `kubeProxyReplacement`, `routingMode`, anything mapping to `enable-*` configmap keys.

### A.5 Add L2 churn alert

**Problem #6.** Add a rule to `infra/configs/alerts/prometheus-rules.yaml` (group `cilium-bgp` or new `cilium-l2`).

> **Pre-step — verify metric names against the running operator.** Cilium 1.19's L2 metric names are not stable in the docs; enumerate what's actually emitted before drafting the alert:
> ```bash
> kubectl -n kube-system port-forward deploy/cilium-operator 9963:9963 &
> curl -s localhost:9963/metrics | grep -i 'l2\|lease' | grep -v '^#'
> kill %1
> ```
> Use the actual metric name(s) you see in the alert below. The names below are illustrative placeholders.

```yaml
- alert: CiliumL2LeaseChurn
  expr: |
    # Replace with verified metric names from the pre-step above.
    rate(<l2_acquired_metric>[5m])
      + rate(<l2_released_metric>[5m]) > 0.1
  for: 10m
  labels: { severity: warning }
  annotations:
    summary: "Cilium L2 leases churning"
    description: "More than 0.1 lease handovers/sec over 10min — speaker is flapping."
```

Validate the rule loads: `kubectl -n monitoring exec prometheus-kube-prometheus-stack-prometheus-0 -- promtool check rules /etc/prometheus/rules/...`. If the metric doesn't exist yet, the alert silently no-ops; the validation step would catch a typo but not a missing metric — only the runtime emission check above does.

### A.6 Verify

```bash
# Lease distribution should span at least 2 worker nodes, not 1 CP node
kubectl -n kube-system get leases | grep cilium-l2announce | awk '{print $2}' | sort | uniq -c

# Both AdGuard IPs reachable from a wired VLAN-2 device
sudo arp -d 10.42.2.43; sudo arp -d 10.42.2.45
dig @10.42.2.43 example.com +short
dig @10.42.2.45 example.com +short
```

### Phase A GO criteria
- All `cilium-l2announce-*` leases held by worker nodes only.
- AdGuard `.43` and `.45` leased to **different** workers.
- Wired VLAN-2 client (Apple TV ping, or kitchen-pi `dig`) reaches both AdGuard IPs.
- L2 churn alert is loaded in Prometheus (`promtool` or UI verifies).

### Phase A rollback
```bash
git revert <commit-sha>
git push
flux reconcile kustomization infra-configs -n flux-system --with-source
flux reconcile kustomization infra-controllers -n flux-system --with-source
kubectl rollout restart ds/cilium -n kube-system
```

---

## Phase B — Test plan hardening (procedural)

**Problem #7.** Before any subsequent network change, the test matrix must include a wired same-subnet device. This is procedural, not code, but documented as a phase to make it gate-blocking.

### B.1 Designate test devices

Add to `docs/operations/network-test-devices.md` (new):

| Device | IP | VLAN | Why |
|---|---|---|---|
| Apple TV | 10.42.2.19 | 2 | Same-subnet wired ARP test |
| `kitchen-pi` | 10.42.2.143 | 2 | SSH-able ARP test |
| `living-room` HifiBerry | 10.42.2.39 | 2 | SSH-able ARP test |
| Mac (george) | 10.42.4.x | 4 | Cross-subnet routing test |

### B.2 Update existing test plans

Edit `docs/plans/2026-03-08-bgp-rollout.md` test matrix and add a "wired VLAN-2 ARP" row:

| Test | Pre | Post-2a | Post-2b | Post-4a | Post-4b |
|---|---|---|---|---|---|
| `ssh kitchen-pi 'arp -d 10.42.2.43; dig @10.42.2.43 …'` | ✓ | ✓ | ✓ | ✓ | ✓ |

This test must pass for every L2/BGP-impacting change going forward.

### Phase B GO criteria
- `docs/operations/network-test-devices.md` exists and documents the test devices.
- Procedural sign-off: every L2/BGP PR template includes a checklist item "wired VLAN-2 ARP test executed and passed".

### Phase B rollback
N/A (procedural).

---

## Phase C — DNS resilience (parallel with B)

**Problem #8.** Wired devices on VLAN 2 currently use `[10.42.2.43, 10.42.2.45]` (cluster AdGuard) as their resolver. Cluster outage → no DNS → no troubleshooting.

### C.1 Update UCGF DHCP DNS option

Add a third resolver as fallback. Choose either:

- **Option 1 (preserves filtering during cluster health):** `[10.42.2.43, 10.42.2.45, 1.1.1.1]`. Clients use AdGuard normally; fall through to Cloudflare on dual-DNS failure. Trade-off: bypasses ad/tracker filtering during cluster outages.
- **Option 2 (preserves filtering always):** Add a UCGF-local `dnsmasq` or `unbound` instance bound to `10.42.2.1` as third resolver, configured to forward to public resolvers but **not** AdGuard. Trade-off: more moving parts on the UCGF.

Recommended: Option 1. The window is rare and short-lived.

### C.2 Apply

```bash
ssh root@10.42.2.1
# UniFi Network UI: Settings → Networks → LAN → Advanced → DHCP Service Management → DNS Server
# Set: 10.42.2.43, 10.42.2.45, 1.1.1.1
# Or via CLI if exposed by the UCGF firmware version.
```

### C.3 Verify

```bash
# After client renews lease (or `sudo dhclient -r eth0; sudo dhclient eth0`):
ssh kitchen-pi 'cat /etc/resolv.conf'
# nameserver 10.42.2.43
# nameserver 10.42.2.45
# nameserver 1.1.1.1
```

### Phase C GO criteria
- Test wired client receives 3-entry DNS list on lease renewal.
- Simulating cluster DNS failure (`kubectl scale sts/adguard --replicas=0` in a maintenance window) — wired clients fall through to public resolver within ~5s.

### Phase C rollback
Revert UCGF DHCP option to `[10.42.2.43, 10.42.2.45]`. Clients pick up on next lease renewal.

---

## Phase D — LB pool migration to dedicated `/24`

**Problem #9.** This is the core topology change that unblocks Problem #10 below (Phase E) and finally enables pure BGP. Adapted from the prior `2026-05-05-bgp-phase4-revision.md`.

> **Pre-flight gate:** Phase A must be complete and stable for ≥48h. Phase B's procedural test must be in effect.

### D.1 Allocate dedicated subnet

> **2026-05-06 correction:** an earlier draft of this plan named `10.42.3.0/24`
> as the target, but on UCGF inspection that subnet is already in use as the
> **Security VLAN** (`br3`, `10.42.3.0/24`). `10.42.5.0/24` is currently
> unallocated — UCGF has interfaces for `br0/2/3/4/6/7` only, and `br5` is
> free. Reserve `10.42.5.0/24` as the LB-only subnet — **no client host will
> live here.**

Verify before reserving (firmware drift could change available bridges):

```bash
ssh root@10.42.2.1 'ip -br addr show | grep "br[0-9]"'
# Confirm no br5 / 10.42.5.x interface; if present, pick the next free /24.
```

### D.2 Add new Cilium IP pool (additive)

Create `infra/configs/cilium/load-balancer-ip-pool-v2.yaml`:

```yaml
apiVersion: cilium.io/v2
kind: CiliumLoadBalancerIPPool
metadata:
  name: home-c-pool-v2
spec:
  blocks:
    - start: 10.42.5.40
      stop: 10.42.5.254
---
apiVersion: cilium.io/v2
kind: CiliumLoadBalancerIPPool
metadata:
  name: home-compute-pool-v2
spec:
  blocks:
    - start: 10.42.5.30
      stop: 10.42.5.37
```

Add to `infra/configs/cilium/kustomization.yaml`. Both old and new pools coexist; services keep their existing IPs until explicitly migrated.

### D.3 Update UCGF prefix-list

```bash
ssh root@10.42.2.1
vtysh
configure terminal
ip prefix-list K8S-LB-IPS seq 20 permit 10.42.5.0/24 ge 32 le 32
end
write memory
```

### D.4 Migrate gateway IP

The gateway services (`gateway-production`, `gateway-staging`) are referenced only by HTTPRoute hostnames + DNS, so the actual IP is largely transparent. Before changing the IP, inventory every place it appears.

#### D.4.0 Inventory checklist (gateway IP `10.42.2.40`)

Run before D.4.1. Update each location in lockstep with the IP change:

```bash
# Check the repo for hardcoded references
grep -rn "10\.42\.2\.40" --include="*.yaml" --include="*.md" --include="*.conf" .
```

Specific places to verify (not exhaustive):

| Location | What to check |
|---|---|
| AdGuard rewrites (UI export) | `*.burntbytes.com` and any explicit per-host A records pointing at `.40` |
| Cloudflare zone (public DNS) | Any A record pointing at `.40` for externally exposed services |
| `apps/base/cloudflare-tunnel/` | `cloudflared` config — does it route to a gateway IP or to a Service name? |
| Tailscale subnet routes (if used) | Advertised routes covering `10.42.2.40` |
| Manual `/etc/hosts` entries | Any host with hand-rolled overrides |
| Per-app HTTPRoute resources | None should hardcode the IP, but verify no overlay does |

#### D.4.1 Apply

1. Allocate `10.42.5.40` to `gateway-production` via overlay annotation `lbipam.cilium.io/ip-pool: home-c-pool-v2`.
2. Update every location from D.4.0's inventory atomically (a single PR for repo changes; a coordinated UI/AdGuard/Cloudflare change for non-repo).
3. Remove the old `.40` IP allocation.

### D.5 Migrate AdGuard pinned IPs (the hard one)

AdGuard `.43` and `.45` are baked into wired-client DNS configs (DHCP option from Phase C, plus any manual configs).

#### D.5.0 Static-DNS device inventory (BLOCKING pre-step)

DHCP-managed clients pick up new resolvers automatically; **statically configured devices won't.** Walk through each non-DHCP device and enumerate where DNS is set:

| Device | DNS config location | How to update |
|---|---|---|
| Apple TV (`.19`) | Settings → Network → configure DNS manually | UI — must be done by hand |
| HifiBerry kitchen (`.38`) | `/etc/resolv.conf` or `/etc/systemd/resolved.conf` | SSH + edit + restart |
| HifiBerry living-room (`.39`) | same | SSH + edit + restart |
| `kitchen-pi` (`.143`) | NetworkManager — `nmcli con mod … ipv4.dns …` | SSH + nmcli |
| hestia (`.10`) | TrueNAS Network → Global Configuration | Web UI |
| Synology (`.11`) | Control Panel → Network → DSM Settings | Web UI |
| Talos nodes (`.20`–`.25`) | Talos machine config (`spec.resolvers`) | `talosctl edit machineconfig` |
| Anything else with `cat /etc/resolv.conf` showing `10.42.2.43` | host-specific | host-specific |

Generate the list:

```bash
# DHCP leases — these update automatically
ssh root@10.42.2.1 'cat /run/dnsmasq.leases 2>/dev/null || ip neighbor show | grep -v FAIL'

# Cross-reference with static-IP devices outside DHCP
# (manual walk through UCGF "Clients" → filter by static)
```

**Do not proceed to D.5.1 until every statically configured device has an owner and an update procedure documented.**

#### D.5.1 Apply

Procedure:
1. Allocate new IPs (`10.42.5.43`, `10.42.5.45`) via per-service overlay.
2. Update UCGF DHCP DNS option from Phase C to advertise BOTH old and new: `[10.42.5.43, 10.42.5.45, 10.42.2.43, 10.42.2.45, 1.1.1.1]`.
3. **Force-update every static device from D.5.0** in parallel with the DHCP change.
4. Wait through the UCGF DHCP lease half-life (verify lease duration in the UCGF UI; default is often 24h, so renewal kicks in at ~12h). Force renew on critical devices: `sudo dhclient -r && sudo dhclient` (Linux) or toggle Wi-Fi (macOS/iOS).
5. Verify each test device's `/etc/resolv.conf` (or equivalent) shows the new IPs.
6. Remove old `.43` / `.45` assignments. Update DHCP option to drop them.
7. Sweep the repo for `10.42.2.43`/`10.42.2.45` hardcoding: `grep -rn "10\.42\.2\.4[35]" .`

### D.6 Migrate remaining LB services

Less-pinned services (snapcast, etc.) migrate via overlay annotation change. Soak each for 24h before moving on.

### D.7 Soak and verify pre-pure-BGP

Routing-layer checks (necessary but not sufficient):

```bash
# All LB IPs now in 10.42.5.x range
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}' | sort -u
# Expected: only 10.42.5.x, no 10.42.2.x

# UCGF has BGP routes for new range
ssh root@10.42.2.1 'vtysh -c "show ip route 10.42.5.0/24"'

# Wired VLAN-2 device reaches new IPs via routing (NOT ARP — they're cross-subnet now)
ssh kitchen-pi 'arp -n 10.42.5.43'   # Expected: no entry (gateway routes it)
```

**Application-layer checks (required — catches HTTPRoute / cert-binding regressions):**

```bash
# DNS resolution
ssh kitchen-pi 'dig @10.42.5.43 example.com +short'

# Full HTTPS stack against the gateway (SNI + cert + HTTPRoute match)
ssh kitchen-pi 'curl -sk --max-time 5 -o /dev/null -w "%{http_code}\n" https://home.burntbytes.com'
ssh kitchen-pi 'curl -sk --max-time 5 -o /dev/null -w "%{http_code}\n" https://grafana.burntbytes.com'
# Expected: 200 (or expected redirect / auth code; NOT a connection failure or 502)

# Repeat from a cross-subnet client (Mac on VLAN 4) to verify both paths
curl -sk --max-time 5 -o /dev/null -w "%{http_code}\n" https://home.burntbytes.com
```

A 200/302/401 confirms TCP connection + TLS handshake + HTTPRoute match + backend reachability — the full stack the 2026-05-05 hestia gateway incident broke. A `dig` alone wouldn't have caught that class of bug.

### D.8 Remove old pools

After ≥48h of clean operation:

```bash
# Delete old pool resources from infra/configs/cilium/load-balancer-ip-pool.yaml
# (keep file but empty the blocks, or delete file and remove from kustomization.yaml)
```

### Phase D GO criteria
- Zero LB services on `10.42.2.x`.
- All wired clients have updated DNS configs (DHCP-distributed first; manual configs verified).
- BGP routes for `10.42.5.0/24` installed on UCGF.
- Wired VLAN-2 test device reaches every service via routed delivery (not ARP).

### Phase D rollback per sub-step
- D.2: delete the new pool resources. Existing services keep their `10.42.2.x` IPs.
- D.4: revert HTTPRoute / DNS to point at old IP; revert overlay; service falls back to old pool.
- D.5: re-add old IPs to the pinned services; update DHCP DNS option to put old IPs back first; let clients re-learn.
- D.6: per-service revert.
- D.8: re-add the pool blocks; existing services aren't affected because they already have the IPs in the new range.

---

## Phase E — Pure BGP (executes the original Phase 4)

**Problem #10 (partial).** Once Phase D completes, the LB pool is on a subnet with no client hosts. L2 announcements become genuinely unnecessary.

> **Pre-flight gate:** Phase D complete for ≥48h. No host on `10.42.5.0/24` (verify via UCGF ARP table and DHCP leases).

### E.1 Delete L2 policy resources

Remove every `CiliumL2AnnouncementPolicy` resource and matching kustomization entry. Filenames depend on what Phase A.2 created — list and delete by glob, not by hardcoded names:

```bash
# Enumerate every L2 policy file currently tracked
ls infra/configs/cilium/l2-announcement-policy*.yaml

# Delete them
rm infra/configs/cilium/l2-announcement-policy*.yaml

# Drop the corresponding entries from infra/configs/cilium/kustomization.yaml
# (every line matching `l2-announcement-policy*.yaml`)
```

Confirm zero policies exist in the rendered output before committing:

```bash
kustomize build infra/configs/cilium/ | grep -c CiliumL2AnnouncementPolicy
# Expected: 0
```

### E.2 Remove L2 from Helm values + roll DaemonSet

Edit `infra/controllers/cilium/values.yaml` — **remove the entire `l2announcements:` block**, including the timing values added in Phase A.3. Leaving `enabled: false` with dead `leaseDuration` keys works but is confusing for future readers.

Before:
```yaml
l2announcements:
  enabled: true
  leaseDuration: "5s"
  leaseRenewDeadline: "3s"
  leaseRetryPeriod: "1s"
```

After: the block is deleted entirely (Cilium defaults to `enabled: false`).

After Flux reconcile:

```bash
# Required — does NOT auto-roll (Problem #4)
kubectl rollout restart ds/cilium -n kube-system
kubectl rollout status ds/cilium -n kube-system
```

### E.3 Verify

```bash
# No L2 policies, no L2 leases
kubectl get ciliuml2announcementpolicy
# No resources found

kubectl -n kube-system get leases | grep -c cilium-l2announce
# 0

# All BGP peers still established with ≥48h uptime
ssh root@10.42.2.1 'vtysh -c "show bgp summary"'

# Wired VLAN-2 device still reaches services (via routing now)
ssh kitchen-pi 'dig @10.42.5.43 example.com +short'
```

### Phase E GO criteria
- Zero L2 policies and zero L2 leases.
- All 3 BGP peers `Established`.
- Wired VLAN-2 + cross-subnet test devices both reach every LB service.
- 24h soak with no flap.

### Phase E rollback
Revert the deletion commits and run a DaemonSet rollout. ARP resumes within ~5s of L2 policies re-applying.

---

## Phase F — Defense-in-depth (parallelizable, post-E)

These can run in any order after Phase E. Each is independent.

### F.1 IoT VLAN segmentation (Problem #10 cont.)

> **2026-05-06 correction:** an earlier draft proposed creating a new VLAN 20.
> UCGF inspection shows an **IoT VLAN already exists** at `10.42.7.0/24`
> (`br7`, configured as "IoT" in UniFi). Migrate to the existing VLAN rather
> than provisioning a parallel one.

Move pure client devices off the Lab VLAN (`br2`, `10.42.2.0/24`) onto the
existing IoT VLAN (`br7`, `10.42.7.0/24`):

| Device | Current | Target |
|---|---|---|
| Apple TV | 10.42.2.19 | 10.42.7.x |
| HifiBerry kitchen | 10.42.2.38 | 10.42.7.x |
| HifiBerry living-room | 10.42.2.39 | 10.42.7.x |
| `kitchen-pi` | 10.42.2.143 | 10.42.7.x |

Keep on VLAN 2 (mgmt/storage):
- Cluster nodes (`.20-.25`)
- hestia / TrueNAS GPU server (`.10`) — iSCSI peer to cluster
- Synology (`.11`) — iSCSI peer to cluster
- UCGF (`.1`)

#### F.1.0 mDNS reflector pre-flight (BLOCKING)

AirPlay (Apple TV ↔ Mac/iPhone), Snapcast/Bonjour, HomeKit pairing, and Spotify Connect all rely on mDNS discovery. Without cross-VLAN mDNS reflection, splitting Apple TV onto the IoT VLAN breaks AirPlay from VLAN 4 (Family) wireless devices.

**Verify before any device moves:**

```bash
# 1. Confirm UCGF firmware exposes mDNS reflector
# UniFi Network UI: Settings → Networks → <network> → Advanced → "Multicast DNS"
# (Path varies by firmware version; "mDNS Repeater" or "Multicast DNS" both apply.)

# 2. If supported, enable mDNS reflector on the Lab VLAN (br2), IoT VLAN (br7),
#    and Family/wireless VLAN (br4) — every VLAN that needs to discover or
#    advertise services should be in the reflector group.

# 3. Test cross-VLAN discovery BEFORE moving the Apple TV.
#    From a Mac on the wireless VLAN: should see Apple TV and HifiBerry advertisements.
dns-sd -B _airplay._tcp local.
dns-sd -B _raop._tcp local.        # AirPlay receiver
dns-sd -B _snapcast._tcp local.    # if Snapcast advertises
```

**Known limitations:** UCG-Fiber's mDNS reflector has historically struggled with `_homekit._tcp` on some firmware versions. If HomeKit pairing breaks after F.1, fall back to keeping HomeKit-paired devices on the same VLAN as their controller, or use a dedicated mDNS bridge (e.g. Avahi on a Pi).

**If the pre-flight fails** (mDNS reflector unavailable or unreliable on current UCGF firmware): do not execute F.1. Either upgrade UCGF firmware to a version with stable mDNS reflection, or accept that IoT VLAN segmentation requires a dedicated mDNS bridge appliance — file as a follow-up plan.

#### F.1.1 Per-device migration

Procedure (per device): change UCGF port-VLAN assignment for the wired port, force DHCP renew, verify routed connectivity, verify cross-VLAN mDNS discovery still works after each move.

### F.2 Narrow BGP listen-range (Problem #11)

After F.1, only cluster nodes remain on VLAN 2. Narrow the FRR `listen range`.

Cluster nodes occupy `10.42.2.20–10.42.2.25` (6 addresses). The smallest CIDR block that covers all 6 is `10.42.2.16/28` (`.16–.31`, 16 addresses). A `/29` (8 addresses) does **not** suffice — `10.42.2.20/29` normalizes to `10.42.2.16/29` (`.16–.23`), excluding workers `.24` and `.25`.

```bash
# Verify before applying
ipcalc 10.42.2.16/28
# Network: 10.42.2.16/28
# HostMin: 10.42.2.17
# HostMax: 10.42.2.30
# (covers all 6 cluster nodes plus a few spares)
```

```bash
ssh root@10.42.2.1
vtysh
configure terminal
router bgp 65100
  no bgp listen range 10.42.2.0/24
  bgp listen range 10.42.2.16/28 peer-group K8S-NODES
end
write memory
```

After applying, confirm all 3 worker peers re-establish:

```bash
ssh root@10.42.2.1 'vtysh -c "show bgp summary"'
# Expected: 3 dynamic peers (*10.42.2.23/.24/.25) Established
```

### F.3 Per-service externalTrafficPolicy review (Problem #12)

For each LoadBalancer service in `apps/`, classify:

- **`Cluster` (current default):** keep when source IP is irrelevant (gateway, snapcast).
- **`Local`:** switch when source IP matters (auth logs, blackbox-probed services). Trade-off: ECMP path count drops to "nodes hosting the backing pod."

Document the decision per-service in a comment next to the service definition.

### F.4 BGP MD5 authentication (defense-in-depth, conditional)

Add BGP password auth on both UCGF and Cilium peer-config to prevent any host on the BGP listen range from asserting AS 65010.

**Trigger condition (must execute F.4 within 7 days if any of the following becomes true):**

1. Any non-cluster device appears in the BGP listen range (e.g. a new node IP outside `10.42.2.20–.25` on the management VLAN).
2. UCGF firmware upgrade re-broadens the listen range (e.g. by reverting `frr.conf`).
3. F.1 is rolled back, putting non-cluster devices back on VLAN 2.

Otherwise: low-priority hardening, not blocking.

**Implementation sketch** (when triggered):

- UCGF: `neighbor K8S-NODES password <secret>` in the BGP config.
- Cilium: add `authSecretRef:` to `CiliumBGPPeerConfig` pointing at a SOPS-encrypted secret in `kube-system`.
- Roll the cilium DaemonSet (per the operator gotcha in A.4).

### Phase F GO criteria
Per sub-phase. F.1: all listed devices on new VLAN, all services still reachable. F.2: BGP peers re-establish on the narrower range. F.3: per-service migration verified.

---

## Out of scope (separate plans)

- **`k8sServiceHost` SPOF (Problem #13).** `infra/controllers/cilium/values.yaml:108` hardcodes `10.42.2.20`. Fix is independent: switch to `auto` or set up a CP VIP via Talos's built-in VIP feature. File as separate plan.

---

## Sequencing summary

Phases serialize, but soak windows dominate the calendar. Realistic durations:

| Phase | Active work | Soak / wait | Total |
|---|---|---|---|
| A — L2 hygiene | ~half day | 48h post-merge | ~3 days |
| B — Test plan hardening | ~1 hour | none (procedural) | same day as A |
| C — DNS fallback | ~1 hour | 24h DHCP propagation | ~1–2 days |
| D — LB pool migration | ~1 day spread across sub-steps | 24h per migrated service + 48h final soak | **2–3 weeks** |
| E — Pure BGP | ~1 hour | 24h soak | ~2 days |
| F — Defense in depth | ~1 day per sub-phase | 24h post each | ~1 week per sub-phase |

Total wall-clock from kickoff to Phase E completion: **~3 weeks of soak windows + ~3 days of active work**, assuming no rollbacks. Phase F sub-phases run in any order after E and don't gate each other.

Each gate is explicit. No phase auto-fires from the previous one.

## Backout to current state

If at any point the rollout proves more disruptive than expected: revert the latest phase, return to the post-Phase-A steady state (L2 distributed across workers + BGP both running), and re-evaluate. The current state is a defensible long-term posture; only the security finding (Problem #10) genuinely requires phase E+F to resolve.
