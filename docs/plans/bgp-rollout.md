---
status: planned
last_modified: 2026-05-02
---

# BGP Rollout Plan — UniFi Cloud Gateway Fiber + Cilium

Replace Cilium L2 announcements (ARP-based) with BGP peering between the Kubernetes node and the UniFi Cloud Gateway Fiber (UCGF). The router gets real routing-table entries for LoadBalancer IPs instead of relying on gratuitous ARP — better reliability, observability, and multi-node readiness.

## Operator constraints

This plan is structured around four explicit constraints:

1. **Safe** — every commit is reversible; failures at any phase have a documented backout.
2. **Minimum disruption** — L2 and BGP coexist for the entire validation window. Disruption risk is concentrated in a single CRD deletion (Phase 4a) which is instantly reversible by re-applying the file.
3. **Per-phase rollback** — each phase has its own rollback procedure. There is no "big-bang revert" expectation.
4. **Operator awareness** — every phase ends at a GO/NO-GO checkpoint. The next phase does not start until the operator explicitly approves.

## Single-node reality

The cluster has one Talos node today. BGP does **not** improve high availability in this state — only L2 leader election goes away. The wins are:

- **Operational**: the gateway gets a routing table entry, not a learned ARP. `show ip route bgp` becomes a real diagnostic surface.
- **Multi-node-readiness**: when a second node joins, the UCGF can ECMP-load-balance across nodes for free (no leader election, no failover delay). L2 cannot do this without external orchestration.

Frame the migration as paying down operational debt and unlocking growth, not as an HA upgrade.

## Current State

| Component | Configuration |
|:----------|:-------------|
| Cilium version | 1.19.1 |
| IP advertisement | L2 announcements (`CiliumL2AnnouncementPolicy`) |
| LoadBalancer IP pool | `10.42.2.40` – `10.42.2.254` (`home-c-pool`) |
| K8s node | `talos-ykb-uir` at `10.42.2.20` (single node) |
| Router | UniFi Cloud Gateway Fiber at `10.42.2.1` |
| Cilium BGP CRDs | Installed (storage version `v2`, `v2alpha1` still served) |
| Cilium `bgpControlPlane` | `false` (default, not overridden) |
| Gateway API | Enabled, Cilium is the controller |

### Files involved

| File | Purpose |
|:-----|:--------|
| [infra/controllers/cilium/values.yaml](../../infra/controllers/cilium/values.yaml) | Helm values — `l2announcements.enabled: true` |
| [infra/configs/cilium/l2-announcement-policy.yaml](../../infra/configs/cilium/l2-announcement-policy.yaml) | `CiliumL2AnnouncementPolicy` resource |
| [infra/configs/cilium/load-balancer-ip-pool.yaml](../../infra/configs/cilium/load-balancer-ip-pool.yaml) | `CiliumLoadBalancerIPPool` (kept as-is) |
| [infra/configs/cilium/kustomization.yaml](../../infra/configs/cilium/kustomization.yaml) | Adds new BGP resources, removes L2 in 4a |
| [docs/infra/ucgf-bgp-frr.conf](../infra/ucgf-bgp-frr.conf) | Snapshot of UCGF FRR running config (filled in during Phase 1) |

---

## Target state

```
                    BGP Peering (eBGP)
                    AS 65100 ◄──────► AS 65010

┌──────────────────┐                   ┌──────────────────────┐
│  UniFi Cloud      │                   │  Kubernetes Node     │
│  Gateway Fiber    │                   │  (talos-ykb-uir)     │
│                  │  10.42.2.1:179     │  10.42.2.20:179      │
│  AS 65100        │◄─────────────────►│  AS 65010            │
│                  │                   │                      │
│  Learns routes:  │                   │  Advertises:         │
│  10.42.2.40/32   │                   │  LoadBalancer IPs    │
│  10.42.2.41/32   │                   │  as /32 routes       │
│  10.42.2.42/32   │                   │  next-hop: self      │
│     ...          │                   │                      │
└──────────────────┘                   └──────────────────────┘
```

- **Router (UCGF):** AS 65100 — receives routes, installs them in its routing table.
- **K8s node:** AS 65010 — advertises LoadBalancer IPs via Cilium BGP control plane.
- **IP pool:** unchanged (`10.42.2.40` – `10.42.2.254`).

---

## Phase 0 — Pre-flight gate (BLOCKING)

**No git changes happen until every checkbox below is satisfied.** Aborting at this phase costs nothing.

### 0.1 UCGF capability

```bash
ssh root@10.42.2.1
vtysh -c "show version"
```

- [ ] FRR is present and reports a version string.
- [ ] Dry-run BGP acceptance:
  ```text
  vtysh
  configure terminal
  router bgp 65100
  exit
  no router bgp 65100
  end
  ```
  No errors → vtysh can accept BGP commands. **Abort if it errors.**
- [ ] Record current UCGF firmware version in this PR's description (so post-firmware-upgrade FRR-loss is detectable).

### 0.2 Cluster capability

```bash
kubectl get crd | grep ciliumbgp
```

- [ ] All five CRDs present:
  - `ciliumbgpadvertisements.cilium.io`
  - `ciliumbgpclusterconfigs.cilium.io`
  - `ciliumbgpnodeconfigoverrides.cilium.io`
  - `ciliumbgpnodeconfigs.cilium.io`
  - `ciliumbgppeerconfigs.cilium.io`
- [ ] `kubectl get crd ciliumbgpclusterconfigs.cilium.io -o yaml | grep -A2 "name: v2"` shows `served: true` and `storage: true`.

### 0.3 Baseline LoadBalancer IPs

Capture today's LB IP map for the Phase 3 verification matrix:

```bash
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{"/"}{.metadata.name}{": "}{.status.loadBalancer.ingress[*].ip}{"\n"}{end}' | sort
```

Paste the output into the **Baseline appendix** at the end of this doc.

### 0.4 Maintenance window

- [ ] Pick a 1-hour window for Phases 4a/4b. Phases 1–3 are non-disruptive and can be done at any time.
- [ ] Notify household / users that LAN may have brief DNS/HTTPS hiccups during the window if BGP cutover is rough.

### Phase 0 GO criteria
All checkboxes above are ticked, baseline LB IPs are captured. Otherwise: **STOP** and resolve before proceeding.

### Phase 0 rollback
None — no changes have been made.

---

## Phase 1 — Configure BGP on the UCGF

The UCGF runs FRRouting (FRR). BGP is configured via SSH; the UniFi controller UI does not expose BGP.

### 1.1 SSH and apply config

```bash
ssh root@10.42.2.1
vtysh
```

```text
configure terminal

! BGP instance with the gateway's AS number
router bgp 65100
  bgp router-id 10.42.2.1
  no bgp ebgp-requires-policy

  ! Peer with the Kubernetes node
  neighbor 10.42.2.20 remote-as 65010
  neighbor 10.42.2.20 description talos-ykb-uir

  address-family ipv4 unicast
    neighbor 10.42.2.20 activate
    neighbor 10.42.2.20 route-map K8S-LB-IN in
    neighbor 10.42.2.20 route-map DENY-ALL out
  exit-address-family

! Inbound: only accept /32s from the LB pool range
ip prefix-list K8S-LB-IPS seq 10 permit 10.42.2.0/24 ge 32 le 32

route-map K8S-LB-IN permit 10
  match ip address prefix-list K8S-LB-IPS

! Outbound: never advertise gateway routes back to k8s
route-map DENY-ALL deny 10

end
write memory
```

**Why these choices:**
- **AS 65100 ↔ 65010** — both in private 2-byte range (64512–65534).
- **`no bgp ebgp-requires-policy`** — required on FRR 8+; without it, routes are silently dropped before route-map evaluation.
- **`K8S-LB-IN` prefix-list** — restricts inbound to the LB pool range. A misconfigured cluster cannot push arbitrary routes (e.g. a default route) to the gateway.
- **`DENY-ALL` outbound** — gateway must never advertise its own routes to the cluster; Cilium would install them.

### 1.2 Snapshot the running config

```bash
vtysh -c "show running-config" > /root/frr-bgp-baseline.conf
```

From a workstation:
```bash
scp root@10.42.2.1:/root/frr-bgp-baseline.conf ~/src/homelab/docs/infra/ucgf-bgp-frr.conf
```

This file is checked into the repo as the canonical reference. Diff it against the live config any time you suspect drift (e.g., after a firmware upgrade).

### 1.3 Verify (peer is `Active`, not `Established`)

```bash
vtysh -c "show bgp summary"
```

Expected: `10.42.2.20` appears as `Active` — the cluster side is not yet configured. **Established at this stage means somebody else is peering. Investigate.**

### 1.4 Persistence note

`write memory` persists across reboots. **Firmware upgrades may wipe `/etc/frr/frr.conf`.** After every UCGF firmware upgrade, re-apply this config from `docs/infra/ucgf-bgp-frr.conf`.

### Phase 1 GO criteria
- `show bgp summary` lists the peer as `Active`.
- `frr-bgp-baseline.conf` is committed.
- Operator confirms Phase 2 may begin.

### Phase 1 rollback

```text
vtysh
configure terminal
no router bgp 65100
no ip prefix-list K8S-LB-IPS
no route-map K8S-LB-IN
no route-map DENY-ALL
end
write memory
```

The gateway returns to pre-Phase-1 state. No effect on cluster traffic (nothing was peering yet).

---

## Phase 2 — Enable Cilium BGP control plane

Both protocols coexist after this phase. **Zero disruption expected** — L2 continues advertising while BGP comes up.

### 2.1 Helm values

Edit [infra/controllers/cilium/values.yaml](../../infra/controllers/cilium/values.yaml) — add (do not remove `l2announcements`):

```yaml
bgpControlPlane:
  enabled: true
```

### 2.2 BGP peer configuration

Create `infra/configs/cilium/bgp-peer-config.yaml`:

```yaml
apiVersion: cilium.io/v2
kind: CiliumBGPPeerConfig
metadata:
  name: ucgf-peer
spec:
  timers:
    holdTimeSeconds: 90
    keepAliveTimeSeconds: 30
    connectRetryTimeSeconds: 120
  transport:
    peerPort: 179
  families:
    - afi: ipv4
      safi: unicast
  gracefulRestart:
    enabled: true
    restartTimeSeconds: 120
```

### 2.3 BGP advertisement

Create `infra/configs/cilium/bgp-advertisement.yaml`:

```yaml
apiVersion: cilium.io/v2
kind: CiliumBGPAdvertisement
metadata:
  name: lb-ip-advertisement
  labels:
    advertise: lb-ips
spec:
  advertisements:
    - advertisementType: Service
      service:
        addresses:
          - LoadBalancerIP
      # Empty matchLabels = match all services with a LoadBalancer IP.
      selector:
        matchLabels: {}
```

### 2.4 BGP cluster configuration

Create `infra/configs/cilium/bgp-cluster-config.yaml`:

```yaml
apiVersion: cilium.io/v2
kind: CiliumBGPClusterConfig
metadata:
  name: homelab-bgp
spec:
  nodeSelector:
    matchLabels:
      kubernetes.io/os: linux
  bgpInstances:
    - name: homelab
      localASN: 65010
      peers:
        - name: ucgf
          peerASN: 65100
          peerAddress: 10.42.2.1
          peerConfigRef:
            name: ucgf-peer
            group: cilium.io
            kind: CiliumBGPPeerConfig
```

### 2.5 Wire into kustomization

Edit [infra/configs/cilium/kustomization.yaml](../../infra/configs/cilium/kustomization.yaml):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - bgp-advertisement.yaml
  - bgp-cluster-config.yaml
  - bgp-peer-config.yaml
  - l2-announcement-policy.yaml   # kept during transition
  - load-balancer-ip-pool.yaml
```

### 2.6 Commit, push, reconcile

```bash
git add infra/configs/cilium/ infra/controllers/cilium/values.yaml
git commit -m "feat(cilium): enable BGP control plane alongside L2 announcements"
git push
flux reconcile kustomization infra-controllers -n flux-system --with-source
flux reconcile kustomization infra-configs -n flux-system --with-source
```

Wait for `cilium` DaemonSet rollout to complete (`bgpControlPlane: true` requires an agent restart).

### 2.7 Verify on cluster

```bash
kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers
```

Expected: peer `10.42.2.1` in `Established` state, `Uptime` increasing.

### 2.8 Verify on UCGF

```bash
ssh root@10.42.2.1 'vtysh -c "show bgp ipv4 unicast"'
ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'
```

Expected: every allocated LoadBalancer IP (from baseline) appears as a `/32` route with next-hop `10.42.2.20`.

### Phase 2 GO criteria
- `cilium-dbg bgp peers` Established.
- All baseline /32s present in `show ip route bgp` on the gateway.
- `kubectl get ciliuml2announcementpolicy` still shows the L2 policy (we did not touch L2).
- LAN client smoke test (any HTTPS endpoint, any DNS query) still works.

### Phase 2 rollback

```bash
# 1. Delete the BGP CRD instances
kubectl delete -f infra/configs/cilium/bgp-cluster-config.yaml
kubectl delete -f infra/configs/cilium/bgp-advertisement.yaml
kubectl delete -f infra/configs/cilium/bgp-peer-config.yaml

# 2. Revert the git changes
git revert <commit-sha>
git push

# 3. Disable BGP in helm (sets bgpControlPlane.enabled: false)
#    Cilium agent restarts; L2 was never disabled, so traffic continues.
```

L2 was never touched. Traffic is uninterrupted throughout this rollback.

---

## Phase 3 — Soak

**Minimum 4 hours. Recommended 24 hours.** Both L2 and BGP advertise the same /32s during this window. The goal is to catch flaps, leaks, or other instability before relying on BGP exclusively.

### 3.1 What to watch

- `kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers` — peer must remain `Established`. Note the `Uptime` at start of soak.
- Cilium agent logs:
  ```bash
  kubectl logs -n kube-system -l k8s-app=cilium --tail=200 | grep -i 'bgp\|error'
  ```
- Prometheus (if scraping Cilium):
  - `cilium_bgp_session_state{}` — must equal 6 (Established) for the duration.
  - `cilium_bgp_peers` — must equal 1.
- Synthetic probe — a curl loop against `https://home.burntbytes.com` from a LAN client every 30 seconds, or rely on existing blackbox-exporter alerts.

### 3.2 GO criteria for Phase 4a

- BGP `Established` continuously for the soak window (no flap events).
- Zero increase in 5xx or DNS-resolution-failure rate compared to baseline.
- All baseline LB IPs still routable via BGP (verify by re-running the kubectl jsonpath query — IPs should match).

### 3.3 NO-GO conditions (abort and rollback to Phase 2)

- Any BGP session flap (uptime resets).
- Cilium agent CPU or memory spikes correlated with BGP activity.
- Gateway shows missing prefixes.
- LAN client probe failures attributable to routing.

### Phase 3 rollback
Same as Phase 2 rollback. No state change happened in Phase 3.

---

## Phase 4a — Remove the L2 announcement policy

This is the cutover. **Single CRD deletion. Instantly reversible.**

After this phase, L2 announcements stop. UCGF ARP cache for LB IPs ages out (~5 min default on UniFi). LAN clients re-learn via BGP-installed routes.

### 4a.1 Delete the policy file

```bash
rm infra/configs/cilium/l2-announcement-policy.yaml
```

Edit `infra/configs/cilium/kustomization.yaml` — remove the `l2-announcement-policy.yaml` line:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - bgp-advertisement.yaml
  - bgp-cluster-config.yaml
  - bgp-peer-config.yaml
  - load-balancer-ip-pool.yaml
```

> Leave `l2announcements.enabled: true` in Helm values. We disable it in 4b after a 24h watch window.

### 4a.2 Commit, push, reconcile

```bash
git add -A infra/configs/cilium/
git commit -m "feat(cilium): remove L2 announcement policy (BGP-only advertisement)"
git push
flux reconcile kustomization infra-configs -n flux-system --with-source
```

### 4a.3 Verify

```bash
kubectl get ciliuml2announcementpolicy
# Expected: No resources found

kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers
# Expected: Established, unchanged Uptime (Cilium agent does not restart)
```

### 4a.4 Force ARP refresh and test from LAN

From a LAN client (NOT the k8s node, NOT the UCGF):

```bash
# Flush ARP for the production gateway IP, then re-test
sudo arp -d 10.42.2.40 2>/dev/null || true
sudo arp -d 10.42.2.43 2>/dev/null || true   # AdGuard primary

curl -sk https://home.burntbytes.com
dig @10.42.2.43 example.com +short
```

Run the **Test plan** matrix below.

### Phase 4a GO criteria
All test-plan rows in the "Post-Phase 4a" column pass.

### Phase 4a rollback

```bash
# Restore the file from the previous commit
git revert HEAD
git push
flux reconcile kustomization infra-configs -n flux-system --with-source
```

Within 1–2 minutes, the L2 policy is re-applied and ARP announcements resume. **This is the safety net.** Use it without hesitation if anything looks wrong — the cost is one extra commit to revert later.

---

## Phase 4b — Disable L2 in Helm values (cosmetic)

Run after **24 hours** of clean operation post-4a. No traffic effect — `l2announcements: enabled: true` does nothing without a `CiliumL2AnnouncementPolicy` resource. This step is for hygiene: future Cilium upgrades shouldn't carry L2 logic that nothing uses.

### 4b.1 Update Helm values

Edit [infra/controllers/cilium/values.yaml](../../infra/controllers/cilium/values.yaml):

```yaml
l2announcements:
  enabled: false   # was true
```

Or remove the block entirely.

### 4b.2 Commit, push, reconcile

```bash
git add infra/controllers/cilium/values.yaml
git commit -m "chore(cilium): disable l2announcements helm flag (already unused)"
git push
flux reconcile kustomization infra-controllers -n flux-system --with-source
```

Cilium DaemonSet rolls. ~30s gap during pod replacement. Expect zero traffic impact (BGP keeps advertising via graceful-restart).

### 4b.3 Verify

```bash
kubectl rollout status ds/cilium -n kube-system
kubectl exec -n kube-system ds/cilium -- cilium-dbg bgp peers
# Established, Uptime resets to seconds (after rollout)
```

Run the **Test plan** matrix one more time, "Post-Phase 4b" column.

### Phase 4b GO criteria
All test-plan rows in the "Post-Phase 4b" column pass.

### Phase 4b rollback

```bash
git revert HEAD
git push
flux reconcile kustomization infra-controllers -n flux-system --with-source
```

Re-enables the Helm flag. Without the policy file (still removed) it has no effect, but the option is back if needed.

---

## Phase 5 — Hardening and docs

### 5.1 Monitoring

Cilium exposes BGP metrics on its existing `/metrics` endpoint. Add Prometheus rules:

```yaml
# infra/configs/alerts/bgp.yaml (illustrative)
groups:
  - name: cilium-bgp
    rules:
      - alert: CiliumBGPSessionDown
        expr: cilium_bgp_session_state != 6
        for: 2m
        labels: { severity: warning }
        annotations:
          summary: "Cilium BGP peer not Established"
          description: "Peer {{ $labels.peer }} state {{ $value }}; LB IPs may be unreachable from outside the node."
      - alert: CiliumBGPNoPeers
        expr: absent(cilium_bgp_peers) or cilium_bgp_peers < 1
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: "Cilium has no BGP peers"
```

### 5.2 Update infra docs

Edit [docs/infra/cilium.md](../infra/cilium.md) — replace any "L2 announcements" wording with "BGP peering with the UCGF (AS 65010 ↔ 65100)". Reference `docs/infra/ucgf-bgp-frr.conf`.

### 5.3 Mark this plan completed

Update the frontmatter `status: planned` → `status: completed` and add a closing date. Move detailed phase content to an appendix or leave intact for future reference / multi-node expansion.

---

## Test plan matrix

Run before any phase, then again after each cutover.

| Test | Pre-cutover | Post-Phase 2 | Post-Phase 4a | Post-Phase 4b |
|:-----|:-----------:|:------------:|:-------------:|:-------------:|
| `dig @10.42.2.43 example.com +short` | ✓ | ✓ | ✓ | ✓ |
| `curl -sk https://home.burntbytes.com` | ✓ | ✓ | ✓ | ✓ |
| `curl -sk https://grafana.burntbytes.com` | ✓ | ✓ | ✓ | ✓ |
| LAN client `arp -d <ip>; curl …` | ✓ (re-ARPs) | ✓ | ✓ (BGP route) | ✓ (BGP route) |
| `vtysh -c "show ip route bgp"` shows /32s | empty | populated | populated | populated |
| `kubectl get ciliuml2announcementpolicy` | 1 | 1 | 0 | 0 |
| `cilium-dbg bgp peers` Established | n/a | yes | yes | yes |
| Helm value `l2announcements.enabled` | true | true | true | false |

Any "no" in a column where "yes" is expected → STOP and rollback the most recent phase.

---

## ASN reference

| Entity | ASN | Notes |
|:-------|:----|:------|
| UniFi Cloud Gateway Fiber | 65100 | Private ASN, chosen arbitrarily |
| Kubernetes cluster | 65010 | Private ASN, chosen arbitrarily |

Private 2-byte AS range: 64512–65534. 4-byte: 4200000000–4294967294.

---

## Multi-node considerations (future)

When a second Talos node joins:

- The `nodeSelector` (`kubernetes.io/os: linux`) on `CiliumBGPClusterConfig` matches all nodes — each gets a BGP session with the UCGF automatically.
- The UCGF learns multiple next-hops for each LB IP and ECMP-load-balances across nodes. No gateway config changes.
- L2 leader-election failures stop being a relevant failure mode (they already don't apply post-this-migration, but conceptually noted).

If you want explicit control over which nodes peer, add a `bgp-policy: active` node label and switch `nodeSelector` to `matchLabels: {bgp-policy: active}`. Apply the label via Talos machine config so it survives node restarts:

```yaml
machine:
  nodeLabels:
    bgp-policy: active
```

---

## Baseline appendix

> Filled in during Phase 0.3.

```
# kubectl get svc -A -o jsonpath='...' (paste output here)
default/cilium-gateway-app-gateway-production: 10.42.2.40
default/cilium-gateway-app-gateway-staging: 10.42.2.42
adguard-prod/adguard: 10.42.2.43
adguard-stage/adguard: 10.42.2.42
snapcast-prod/snapcast: 10.42.2.44
snapcast-stage/snapcast: 10.42.2.41
```

Replace with the live output captured at Phase 0.3 time. Anyone reading this plan a year from now needs to know what was advertised at cutover.
