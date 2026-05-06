---
status: planned
last_modified: 2026-05-06
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

### A.1 Restrict L2 announcements to worker nodes

**Problem #1.** Edit `infra/configs/cilium/l2-announcement-policy.yaml` to add a worker-only `nodeSelector` mirroring `bgp-cluster-config.yaml:9-14`:

```yaml
spec:
  externalIPs: true
  loadBalancerIPs: true
  nodeSelector:
    matchExpressions:
      - key: node-role.kubernetes.io/control-plane
        operator: DoesNotExist
```

Effect: lease leader election restricts to `talos-lmh-kyf` / `-18u-ski` / `-kot-7x7`. CP nodes (`-2mz-rfj` / `-v2l-hng` / `-ykb-uir`) drop out.

### A.2 Split AdGuard primary/secondary onto different L2 speakers

**Problem #2.** Add per-service `CiliumL2AnnouncementPolicy` resources with `serviceSelector` to force lease distribution. Two policies, one per service, each pinning to a disjoint node subset:

```yaml
# infra/configs/cilium/l2-announcement-policy-adguard-primary.yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumL2AnnouncementPolicy
metadata:
  name: l2-adguard-primary
  namespace: kube-system
spec:
  loadBalancerIPs: true
  serviceSelector:
    matchLabels:
      app.kubernetes.io/name: adguard
      cilium.io/lb-pool: primary
  nodeSelector:
    matchExpressions:
      - key: kubernetes.io/hostname
        operator: In
        values: ["talos-lmh-kyf", "talos-18u-ski"]
```

```yaml
# infra/configs/cilium/l2-announcement-policy-adguard-secondary.yaml
# pins to talos-kot-7x7 only — different node from primary
```

Add a label `cilium.io/lb-pool: primary` / `secondary` to the matching services in `apps/base/adguard/service.yaml` and `apps/production/adguard/service-dns-secondary.yaml`.

Then narrow the cluster-wide policy from A.1 to **exclude** AdGuard services (add a `serviceSelector` matchExpression `NotIn: [adguard]`), or split it into a default-deny + per-service-allow model.

> **Trade-off:** more YAML, but per-service ownership means a node failure breaks at most one DNS IP.

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

**Problem #6.** Add a rule to `infra/configs/alerts/prometheus-rules.yaml` (group `cilium-bgp` or new `cilium-l2`):

```yaml
- alert: CiliumL2LeaseChurn
  expr: |
    rate(cilium_operator_lb_l2_acquired_leases_total[5m])
      + rate(cilium_operator_lb_l2_released_leases_total[5m]) > 0.1
  for: 10m
  labels: { severity: warning }
  annotations:
    summary: "Cilium L2 leases churning"
    description: "More than 0.1 lease handovers/sec over 10min — speaker is flapping."
```

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

`10.42.3.0/24` is currently unused on the LAN. Reserve it as the LB-only subnet — **no client host will live here.**

### D.2 Add new Cilium IP pool (additive)

Create `infra/configs/cilium/load-balancer-ip-pool-v2.yaml`:

```yaml
apiVersion: cilium.io/v2
kind: CiliumLoadBalancerIPPool
metadata:
  name: home-c-pool-v2
spec:
  blocks:
    - start: 10.42.3.40
      stop: 10.42.3.254
---
apiVersion: cilium.io/v2
kind: CiliumLoadBalancerIPPool
metadata:
  name: home-compute-pool-v2
spec:
  blocks:
    - start: 10.42.3.30
      stop: 10.42.3.37
```

Add to `infra/configs/cilium/kustomization.yaml`. Both old and new pools coexist; services keep their existing IPs until explicitly migrated.

### D.3 Update UCGF prefix-list

```bash
ssh root@10.42.2.1
vtysh
configure terminal
ip prefix-list K8S-LB-IPS seq 20 permit 10.42.3.0/24 ge 32 le 32
end
write memory
```

### D.4 Migrate gateway IP

The gateway services (`gateway-production`, `gateway-staging`) are referenced only by HTTPRoute hostnames + DNS, so the actual IP is largely transparent.

1. Allocate `10.42.3.40` to `gateway-production` via overlay annotation `lbipam.cilium.io/ip-pool: home-c-pool-v2`.
2. Update split-horizon DNS (AdGuard internal zone) to point `*.burntbytes.com` → `10.42.3.40`.
3. Remove the old `.40` IP allocation.

### D.5 Migrate AdGuard pinned IPs (the hard one)

AdGuard `.43` and `.45` are baked into wired-client DNS configs (DHCP option from Phase C, plus any manual configs).

Procedure:
1. Allocate new IPs (`10.42.3.43`, `10.42.3.45`) via per-service overlay.
2. Update UCGF DHCP DNS option from Phase C to advertise BOTH old and new: `[10.42.3.43, 10.42.3.45, 10.42.2.43, 10.42.2.45, 1.1.1.1]`.
3. Wait 24h for clients to pick up new leases (or force renew on critical devices).
4. Remove old `.43` / `.45` assignments. Update DHCP option to drop them.
5. Sweep manual DNS configs (HomeKit/Home Assistant config, anything hand-rolled) for hardcoded `10.42.2.43`.

### D.6 Migrate remaining LB services

Less-pinned services (snapcast, etc.) migrate via overlay annotation change. Soak each for 24h before moving on.

### D.7 Soak and verify pre-pure-BGP

```bash
# All LB IPs now in 10.42.3.x range
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}' | sort -u
# Expected: only 10.42.3.x, no 10.42.2.x

# UCGF has BGP routes for new range
ssh root@10.42.2.1 'vtysh -c "show ip route 10.42.3.0/24"'

# Wired VLAN-2 device reaches new IPs via routing (NOT ARP — they're cross-subnet now)
ssh kitchen-pi 'arp -n 10.42.3.43'   # Expected: no entry (gateway routes it)
ssh kitchen-pi 'dig @10.42.3.43 example.com +short'   # Works via UCGF
```

### D.8 Remove old pools

After ≥48h of clean operation:

```bash
# Delete old pool resources from infra/configs/cilium/load-balancer-ip-pool.yaml
# (keep file but empty the blocks, or delete file and remove from kustomization.yaml)
```

### Phase D GO criteria
- Zero LB services on `10.42.2.x`.
- All wired clients have updated DNS configs (DHCP-distributed first; manual configs verified).
- BGP routes for `10.42.3.0/24` installed on UCGF.
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

> **Pre-flight gate:** Phase D complete for ≥48h. No host on `10.42.3.0/24` (verify via UCGF ARP table and DHCP leases).

### E.1 Delete L2 policy resources

```bash
# Remove every CiliumL2AnnouncementPolicy created in Phase A
rm infra/configs/cilium/l2-announcement-policy.yaml
rm infra/configs/cilium/l2-announcement-policy-adguard-primary.yaml
rm infra/configs/cilium/l2-announcement-policy-adguard-secondary.yaml
# Update kustomization.yaml accordingly
```

### E.2 Disable L2 in Helm + roll DaemonSet

Edit `infra/controllers/cilium/values.yaml`:

```yaml
l2announcements:
  enabled: false
```

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
ssh kitchen-pi 'dig @10.42.3.43 example.com +short'
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

Move pure client devices off VLAN 2 onto a new VLAN 20 (`10.42.20.0/24`):

| Device | Current | Target |
|---|---|---|
| Apple TV | 10.42.2.19 | 10.42.20.x |
| HifiBerry kitchen | 10.42.2.38 | 10.42.20.x |
| HifiBerry living-room | 10.42.2.39 | 10.42.20.x |
| `kitchen-pi` | 10.42.2.143 | 10.42.20.x |

Keep on VLAN 2 (mgmt/storage):
- Cluster nodes (`.20-.25`)
- hestia / TrueNAS GPU server (`.10`) — iSCSI peer to cluster
- Synology (`.11`) — iSCSI peer to cluster
- UCGF (`.1`)

Procedure (per device): change UCGF port-VLAN assignment for the wired port, force DHCP renew, verify routed connectivity. Snapcast multicast (Bonjour) needs UCGF mDNS reflector enabled across VLAN 2 ↔ 20 to keep auto-discovery working.

### F.2 Narrow BGP listen-range (Problem #11)

After F.1, only cluster nodes remain on VLAN 2. Narrow the FRR `listen range`:

```bash
ssh root@10.42.2.1
vtysh
configure terminal
router bgp 65100
  no bgp listen range 10.42.2.0/24
  bgp listen range 10.42.2.20/29 peer-group K8S-NODES
end
write memory
```

`/29` covers `.16-.23` (3 CP + 3 worker + spare) — much smaller attack surface.

### F.3 Per-service externalTrafficPolicy review (Problem #12)

For each LoadBalancer service in `apps/`, classify:

- **`Cluster` (current default):** keep when source IP is irrelevant (gateway, snapcast).
- **`Local`:** switch when source IP matters (auth logs, blackbox-probed services). Trade-off: ECMP path count drops to "nodes hosting the backing pod."

Document the decision per-service in a comment next to the service definition.

### F.4 Optional: BGP MD5 authentication

If F.1/F.2 are deemed insufficient, add BGP password auth on both UCGF and Cilium peer-config. Out of scope unless an actual untrusted-LAN risk emerges.

### Phase F GO criteria
Per sub-phase. F.1: all listed devices on new VLAN, all services still reachable. F.2: BGP peers re-establish on the narrower range. F.3: per-service migration verified.

---

## Out of scope (separate plans)

- **`k8sServiceHost` SPOF (Problem #13).** `infra/controllers/cilium/values.yaml:108` hardcodes `10.42.2.20`. Fix is independent: switch to `auto` or set up a CP VIP via Talos's built-in VIP feature. File as separate plan.

---

## Sequencing summary

```
Week 1  ─ Phase A (L2 hygiene + tuning)
        └ Phase B (test plan hardening, parallel)
        └ Phase C (DNS fallback, parallel)

Week 2  ─ Phase D start (allocate new pool, migrate gateway)
Week 3  ─ Phase D continue (migrate AdGuard, then long-tail services)
Week 4  ─ Phase D soak (48h+) → Phase E (pure BGP)

Week 5+ ─ Phase F sub-phases (IoT VLAN, listen-range, eTP review)
```

Each gate explicit. No phase auto-fires from the previous one.

## Backout to current state

If at any point the rollout proves more disruptive than expected: revert the latest phase, return to the post-Phase-A steady state (L2 distributed across workers + BGP both running), and re-evaluate. The current state is a defensible long-term posture; only the security finding (Problem #10) genuinely requires phase E+F to resolve.
