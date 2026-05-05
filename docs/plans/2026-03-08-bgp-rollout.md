---
status: planned
last_modified: 2026-05-03
---

# BGP Rollout Plan — UniFi Cloud Gateway Fiber + Cilium

Replace Cilium L2 announcements (ARP-based) with BGP peering between the Kubernetes node and the UniFi Cloud Gateway Fiber (UCGF). The router gets real routing-table entries for LoadBalancer IPs instead of relying on gratuitous ARP — better reliability, observability, and multi-node readiness.

## Operator constraints

This plan is structured around four explicit constraints:

1. **Safe** — every commit is reversible; failures at any phase have a documented backout.
2. **Minimum disruption** — L2 and BGP coexist for the entire validation window. Disruption risk is concentrated in a single CRD deletion (Phase 4a) which is instantly reversible by re-applying the file.
3. **Per-phase rollback** — each phase has its own rollback procedure. There is no "big-bang revert" expectation.
4. **Operator awareness** — every phase ends at a GO/NO-GO checkpoint. The next phase does not start until the operator explicitly approves.

## Topology

The cluster is 6 nodes, all on the `10.42.2.0/24` LAN segment:

| Node | Role | IP |
|:-----|:-----|:---|
| `talos-ykb-uir` | control-plane | `10.42.2.20` |
| `talos-2mz-rfj` | control-plane | `10.42.2.21` |
| `talos-v2l-hng` | control-plane | `10.42.2.22` |
| `talos-lmh-kyf` | worker | `10.42.2.23` |
| `talos-18u-ski` | worker | `10.42.2.24` |
| `talos-kot-7x7` | worker | `10.42.2.25` |

**Only the 3 worker nodes will peer with BGP.** Control-plane nodes carry the standard `node.kubernetes.io/exclude-from-external-load-balancers` label, so Cilium would skip them for service IP advertisement anyway. Establishing BGP sessions from idle CP nodes adds noise without benefit. The `nodeSelector` in `CiliumBGPClusterConfig` excludes them via the role label.

End state: **3 BGP peers on the UCGF, 3 ECMP next-hops per LB IP.** Any single-worker BGP failure leaves traffic served by the other two — this migration produces real HA, not just operational hygiene.

Future workers join automatically because the UCGF uses a `bgp listen range` rather than explicit per-node neighbors; no gateway change needed when the cluster grows.

## Current State

| Component | Configuration |
|:----------|:-------------|
| Cilium version | 1.19.1 |
| IP advertisement | L2 announcements (`CiliumL2AnnouncementPolicy`) |
| LoadBalancer IP pool | `10.42.2.40` – `10.42.2.254` (`home-c-pool`) |
| K8s nodes | 6 total: 3 CP (`.20`/`.21`/`.22`) + 3 worker (`.23`/`.24`/`.25`) |
| BGP peers (planned) | 3 — one per worker node |
| Router | UniFi Cloud Gateway Fiber at `10.42.2.1` |
| Cilium BGP CRDs | Installed (storage version `v2`, `v2alpha1` still served) |
| Cilium `bgpControlPlane` | `false` (default, not overridden) |
| Gateway API | Enabled, Cilium is the controller |

### Files involved

| File | Purpose |
|:-----|:--------|
| [infra/controllers/cilium/values.yaml](../../reference/controllers/cilium/values.yaml) | Helm values — `l2announcements.enabled: true` |
| [infra/configs/cilium/l2-announcement-policy.yaml](../../reference/configs/cilium/l2-announcement-policy.yaml) | `CiliumL2AnnouncementPolicy` resource |
| [infra/configs/cilium/load-balancer-ip-pool.yaml](../../reference/configs/cilium/load-balancer-ip-pool.yaml) | `CiliumLoadBalancerIPPool` (kept as-is) |
| [infra/configs/cilium/kustomization.yaml](../../reference/configs/cilium/kustomization.yaml) | Adds new BGP resources, removes L2 in 4a |
| [docs/reference/ucgf-bgp-frr.conf](../reference/ucgf-bgp-frr.conf) | Snapshot of UCGF FRR running config (filled in during Phase 1) |

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

### 0.3 Baseline snapshots

Capture both the LB IP map and the cluster topology for the Phase 3 verification matrix:

```bash
# LoadBalancer IPs
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{"/"}{.metadata.name}{": "}{.status.loadBalancer.ingress[*].ip}{"\n"}{end}' | sort

# Node topology (which workers must peer)
kubectl get nodes -o wide

# Confirm CP nodes are excluded from LB advertisement
kubectl get nodes -l node.kubernetes.io/exclude-from-external-load-balancers -o name
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

  ! Dynamic peering — accept BGP from any LAN node asserting AS 65010.
  ! Workers initiate the session; new nodes join automatically.
  bgp listen range 10.42.2.0/24 peer-group K8S-NODES

  neighbor K8S-NODES peer-group
  neighbor K8S-NODES remote-as 65010
  neighbor K8S-NODES description melodic-muse-worker

  address-family ipv4 unicast
    neighbor K8S-NODES activate
    neighbor K8S-NODES route-map K8S-LB-IN in
    neighbor K8S-NODES route-map DENY-ALL out
    ! ECMP across worker peers
    maximum-paths 8
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
- **`bgp listen range` + peer-group** — accepts incoming BGP from any node on the LAN segment that asserts AS 65010. Cleaner than 3 explicit `neighbor` entries; survives cluster growth automatically.
- **`maximum-paths 8`** — installs all worker next-hops in the routing table for ECMP. Without this, FRR keeps only one path per prefix; you'd see BGP "best path" but no actual load balancing.
- **`K8S-LB-IN` prefix-list** — restricts inbound to the LB pool range. A misconfigured cluster cannot push arbitrary routes (e.g. a default route) to the gateway.
- **`DENY-ALL` outbound** — gateway must never advertise its own routes to the cluster; Cilium would install them.

### 1.2 Snapshot the running config

```bash
vtysh -c "show running-config" > /root/frr-bgp-baseline.conf
```

From a workstation:
```bash
scp root@10.42.2.1:/root/frr-bgp-baseline.conf ~/src/homelab/docs/reference/ucgf-bgp-frr.conf
```

This file is checked into the repo as the canonical reference. Diff it against the live config any time you suspect drift (e.g., after a firmware upgrade).

### 1.3 Verify (no peers yet)

```bash
vtysh -c "show bgp summary"
```

Expected: BGP instance is up but **no neighbors are listed yet** — the listen-range only learns peers as they connect. The cluster side is not configured, so nothing has connected. After Phase 2b, this output will list 3 dynamic peers (`*10.42.2.23`, `*10.42.2.24`, `*10.42.2.25` — leading `*` denotes a dynamically-learned peer).

**If a peer is already Established, somebody else on the LAN is asserting AS 65010 — investigate before proceeding.**

### 1.4 Persistence note

`write memory` persists across reboots. **Firmware upgrades may wipe `/etc/frr/frr.conf`.** After every UCGF firmware upgrade, re-apply this config from `docs/reference/ucgf-bgp-frr.conf`.

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

The phase is split into **2a (canary on one worker)** and **2b (expand to all workers)**. The canary catches a broken cluster-wide BGP config before it lands on all 3 workers. The shared YAML (`CiliumBGPPeerConfig`, `CiliumBGPAdvertisement`) is created in 2a and reused unchanged in 2b.

### 2a.1 Helm values

Edit [infra/controllers/cilium/values.yaml](../../reference/controllers/cilium/values.yaml) — add (do not remove `l2announcements`):

```yaml
bgpControlPlane:
  enabled: true
```

This is cluster-wide; it enables the BGP control plane on every Cilium agent. Whether a node *peers* is determined by `CiliumBGPClusterConfig` selector below.

### 2a.2 Pick a canary worker

Use `talos-lmh-kyf` (`10.42.2.23`) as the canary. Pick a node not currently scheduling the production gateway pods if possible; check with `kubectl get pods -A -o wide | grep gateway`.

### 2a.3 Label the canary

```bash
kubectl label node talos-lmh-kyf bgp-canary=true
```

> This label is transient. It does not need to survive a node reboot — Phase 2b promotes to a role-based selector that matches all workers automatically. The canary label becomes irrelevant after 2b and is removed in 2b.4.

### 2a.4 BGP peer configuration

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

### 2a.5 BGP advertisement

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

### 2a.6 BGP cluster configuration (canary selector)

Create `infra/configs/cilium/bgp-cluster-config.yaml` with the canary-only selector:

```yaml
apiVersion: cilium.io/v2
kind: CiliumBGPClusterConfig
metadata:
  name: homelab-bgp
spec:
  # Phase 2a: canary only. Phase 2b changes this to role-based exclusion.
  nodeSelector:
    matchLabels:
      bgp-canary: "true"
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

### 2a.7 Wire into kustomization

Edit [infra/configs/cilium/kustomization.yaml](../../reference/configs/cilium/kustomization.yaml):

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

### 2a.8 Commit, push, reconcile

```bash
git add infra/configs/cilium/ infra/controllers/cilium/values.yaml
git commit -m "feat(cilium): enable BGP control plane (canary on one worker)"
git push
flux reconcile kustomization infra-controllers -n flux-system --with-source
flux reconcile kustomization infra-configs -n flux-system --with-source
```

Wait for the `cilium` DaemonSet rollout to complete (`bgpControlPlane: true` triggers an agent restart on every node).

### 2a.9 Verify (one peer only)

From the cluster:

```bash
# Canary worker should report Established
kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium --field-selector spec.nodeName=talos-lmh-kyf -o jsonpath='{.items[0].metadata.name}')" \
  -- cilium-dbg bgp peers

# Other workers should report no BGP instance configured
for node in talos-18u-ski talos-kot-7x7; do
  echo "=== $node ==="
  kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium --field-selector spec.nodeName=$node -o jsonpath='{.items[0].metadata.name}')" \
    -- cilium-dbg bgp peers
done
```

From the UCGF:

```bash
ssh root@10.42.2.1 'vtysh -c "show bgp summary"'
# Expected: 1 dynamic peer (*10.42.2.23) Established

ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'
# Expected: every baseline /32 with single next-hop 10.42.2.23
```

### 2a.10 Soak — 1 hour minimum

Watch:
- `cilium-dbg bgp peers` on the canary stays Established with monotonically increasing Uptime.
- No correlated log errors on the canary's Cilium agent.
- LAN client smoke test (`curl https://home.burntbytes.com`, `dig @10.42.2.43 …`) succeeds.

### Phase 2a GO criteria
- Canary peer Established for ≥1 hour without flap.
- Other 5 nodes show no BGP activity.
- L2 still active (`kubectl get ciliuml2announcementpolicy` returns 1 resource).

### Phase 2a rollback

Fast option: remove the canary label.
```bash
kubectl label node talos-lmh-kyf bgp-canary-
```
The canary's session drops within seconds; L2 carries traffic.

Full option: revert the commit and push.
```bash
git revert HEAD
git push
flux reconcile kustomization infra-configs -n flux-system --with-source
flux reconcile kustomization infra-controllers -n flux-system --with-source
```

---

### 2b.1 Promote selector to all workers

Edit `infra/configs/cilium/bgp-cluster-config.yaml` — replace the canary selector with role-based exclusion:

```yaml
spec:
  # Phase 2b: every node EXCEPT control-plane nodes peers.
  # Workers establish BGP; CP nodes are excluded (they wouldn't advertise
  # anyway because of node.kubernetes.io/exclude-from-external-load-balancers).
  nodeSelector:
    matchExpressions:
      - key: node-role.kubernetes.io/control-plane
        operator: DoesNotExist
```

### 2b.2 Commit, push, reconcile

```bash
git add infra/configs/cilium/bgp-cluster-config.yaml
git commit -m "feat(cilium): promote BGP from canary to all workers"
git push
flux reconcile kustomization infra-configs -n flux-system --with-source
```

The `CiliumBGPClusterConfig` change is hot-applied; no Cilium agent restart needed. The non-canary workers will initiate BGP within ~30s.

### 2b.3 Verify (3 worker peers, ECMP)

From the cluster:

```bash
# All 3 workers Established
for node in talos-lmh-kyf talos-18u-ski talos-kot-7x7; do
  echo "=== $node ==="
  kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium --field-selector spec.nodeName=$node -o jsonpath='{.items[0].metadata.name}')" \
    -- cilium-dbg bgp peers
done

# CP nodes still report no BGP
for node in talos-ykb-uir talos-2mz-rfj talos-v2l-hng; do
  echo "=== $node ==="
  kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium --field-selector spec.nodeName=$node -o jsonpath='{.items[0].metadata.name}')" \
    -- cilium-dbg bgp peers
done
```

From the UCGF:

```bash
ssh root@10.42.2.1 'vtysh -c "show bgp summary"'
# Expected: 3 dynamic peers Established (*10.42.2.23, .24, .25)

ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'
# Expected: each baseline /32 shows 3 next-hops (one per worker), e.g.:
#   B>* 10.42.2.40/32 [20/0] via 10.42.2.23, eth0, weight 1
#                       via 10.42.2.24, eth0, weight 1
#                       via 10.42.2.25, eth0, weight 1
```

### 2b.4 Remove canary label (cleanup)

```bash
kubectl label node talos-lmh-kyf bgp-canary-
```

The canary worker keeps peering — it now matches the role-based selector instead.

### Phase 2b GO criteria
- 3 BGP peers Established on UCGF.
- ECMP visible: `show ip route bgp` shows 3 next-hops per LB IP.
- All baseline LB IPs reachable from a LAN client.
- L2 still active (we did not touch L2).

### Phase 2b rollback

Three choices, in order of escalation:

1. **Roll back to canary** — `git revert HEAD` (the 2b.1 commit). The selector returns to `bgp-canary: "true"`; only the canary peer remains. Re-add the canary label if it was removed in 2b.4.
2. **Roll back to no-BGP** — Phase 2a rollback applied on top.
3. L2 was never touched throughout. Traffic is uninterrupted in all rollback paths.

---

## Phase 3 — Soak

**Minimum 4 hours. Recommended 24 hours.** Both L2 and BGP advertise the same /32s during this window. The goal is to catch flaps, leaks, or other instability before relying on BGP exclusively.

Soak runs against the **full multi-node fleet** — all 3 worker peers must remain Established. Single-peer flaps are tolerable (other 2 workers still serve traffic) but unexpected; investigate before proceeding.

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

- All 3 worker BGP sessions `Established` continuously for the soak window (no flap events).
- `show ip route bgp` on the UCGF shows **3 next-hops per LB IP** for the entire window (ECMP holds).
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

# All 3 worker peers still Established with unchanged Uptime
for node in talos-lmh-kyf talos-18u-ski talos-kot-7x7; do
  echo "=== $node ==="
  kubectl exec -n kube-system "$(kubectl get pod -n kube-system -l k8s-app=cilium --field-selector spec.nodeName=$node -o jsonpath='{.items[0].metadata.name}')" \
    -- cilium-dbg bgp peers
done

ssh root@10.42.2.1 'vtysh -c "show ip route bgp"'
# Expected: still 3 next-hops per LB IP. ECMP holds — L2 removal did not perturb BGP routes.
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

Edit [infra/controllers/cilium/values.yaml](../../reference/controllers/cilium/values.yaml):

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

Edit [docs/reference/cilium.md](../reference/cilium.md) — replace any "L2 announcements" wording with "BGP peering with the UCGF (AS 65010 ↔ 65100)". Reference `docs/reference/ucgf-bgp-frr.conf`.

### 5.3 Mark this plan completed

Update the frontmatter `status: planned` → `status: completed` and add a closing date. Move detailed phase content to an appendix or leave intact for future reference / multi-node expansion.

---

## Test plan matrix

Run before any phase, then again after each cutover.

| Test | Pre-cutover | Post-Phase 2a | Post-Phase 2b | Post-Phase 4a | Post-Phase 4b |
|:-----|:-----------:|:-------------:|:-------------:|:-------------:|:-------------:|
| `dig @10.42.2.43 example.com +short` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `curl -sk https://home.burntbytes.com` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `curl -sk https://grafana.burntbytes.com` | ✓ | ✓ | ✓ | ✓ | ✓ |
| LAN client `arp -d <ip>; curl …` | ✓ (re-ARPs) | ✓ | ✓ | ✓ (BGP route) | ✓ (BGP route) |
| `vtysh -c "show bgp summary"` peer count | 0 | 1 | 3 | 3 | 3 |
| `vtysh -c "show ip route bgp"` next-hops per LB IP | n/a | 1 | **3 (ECMP)** | 3 | 3 |
| `cilium-dbg bgp peers` Established (per worker) | n/a | canary only | all 3 workers | all 3 workers | all 3 workers |
| `cilium-dbg bgp peers` on CP nodes | n/a | none | none | none | none |
| `kubectl get ciliuml2announcementpolicy` | 1 | 1 | 1 | 0 | 0 |
| Helm value `l2announcements.enabled` | true | true | true | true | false |

**Resilience tests** (run after Phase 4a):

| Test | Procedure | Expected |
|:-----|:----------|:---------|
| Single-worker drain | `kubectl drain talos-lmh-kyf --ignore-daemonsets --delete-emptydir-data` | UCGF removes that next-hop in ≤90s; LB IPs continue serving via the other 2 workers; `curl` smoke test still 200. Uncordon to restore. |
| Cilium agent rolling restart | `kubectl rollout restart ds/cilium -n kube-system` | Per-pod BGP sessions reset one-by-one; graceful-restart keeps routes installed for 120s; LAN-side smoke test never fails. |
| Single-worker BGP flap simulation | On worker, `iptables -I INPUT -p tcp --dport 179 -j DROP` for 30s, then revert | UCGF marks peer Idle within hold-time, removes that next-hop; ECMP drops to 2; traffic uninterrupted. After revert, peer re-Established. |

Any "no" where "yes" is expected → STOP and roll back the most recent phase.

---

## ASN reference

| Entity | ASN | Notes |
|:-------|:----|:------|
| UniFi Cloud Gateway Fiber | 65100 | Private ASN, chosen arbitrarily |
| Kubernetes cluster | 65010 | Private ASN, chosen arbitrarily |

Private 2-byte AS range: 64512–65534. 4-byte: 4200000000–4294967294.

---

## Future cluster growth

The `bgp listen range 10.42.2.0/24` on the UCGF and the `node-role.kubernetes.io/control-plane: DoesNotExist` selector in `CiliumBGPClusterConfig` together make new workers self-service:

- A new worker node joins the cluster on `10.42.2.x` → matches the role-based selector → Cilium initiates BGP → UCGF accepts via the listen range → ECMP path-count grows by one. No homelab repo change needed.
- A new control-plane node joins → matched by the role exclusion → no BGP session, as desired.

If a future requirement demands BGP from CP nodes (e.g., advertising pod CIDRs from CP), revisit the selector and reason about whether the `exclude-from-external-load-balancers` label still does the right thing for service IPs.

## Out of scope

- **`k8sServiceHost: "10.42.2.20"` SPOF.** [`infra/controllers/cilium/values.yaml`](../../reference/controllers/cilium/values.yaml) hardcodes Cilium's API-server endpoint to one CP node IP. With 3 CP nodes, this is a SPOF for Cilium → kube-apiserver. **Not part of this migration.** File a follow-up issue; consider switching to `k8sServiceHost: "auto"` or a CP VIP.
- **`externalTrafficPolicy: Local`.** All LB services use `Cluster`. Switching to `Local` preserves source IP and avoids the second hop, but reduces ECMP path count to "nodes hosting the backing pod." Separate decision; not part of BGP migration.
- **BGP authentication (MD5 / TCP-AO).** Skipped — the cluster and gateway share a private LAN segment with no untrusted peers. Add later if multiple BGP speakers are introduced.
- **Pod CIDR / Egress Gateway via BGP.** Out of scope; this migration only changes how LB IPs are advertised.

---

## Baseline appendix

> Filled in during Phase 0.3 — captured 2026-05-04.

### Cluster topology

```
NAME            STATUS   ROLES           INTERNAL-IP
talos-ykb-uir   Ready    control-plane   10.42.2.20
talos-2mz-rfj   Ready    control-plane   10.42.2.21
talos-v2l-hng   Ready    control-plane   10.42.2.22
talos-lmh-kyf   Ready    <none>          10.42.2.23   # canary worker (Phase 2a)
talos-18u-ski   Ready    <none>          10.42.2.24
talos-kot-7x7   Ready    <none>          10.42.2.25
```

All 3 control-plane nodes carry `node.kubernetes.io/exclude-from-external-load-balancers` (verified at Phase 0.3).

### LoadBalancer service IP map

```
adguard-prod/adguard:                            10.42.2.43   # pinned (client DNS configs)
adguard-prod/adguard-dns-secondary:              10.42.2.45   # pinned (client DNS configs)
adguard-stage/adguard:                           10.42.2.42   # → standalone IP after this PR
default/cilium-gateway-app-gateway-production:   10.42.2.40
default/cilium-gateway-app-gateway-staging:      10.42.2.42   # retains .42 alone after split
snapcast-prod/snapcast:                          10.42.2.37
snapcast-stage/snapcast:                         10.42.2.41
```

> **Shared-IP cleanup (this PR):** Pre-PR, `gateway-staging` and `adguard-stage` co-tenanted `10.42.2.42` via `lbipam.cilium.io/sharing-key: homelab`, a single-node-era IP-compaction holdover. Multi-node cluster removes that scarcity. This PR strips the sharing annotations from `adguard-stage`'s overlay so IPAM allocates it a standalone IP from `home-c-pool`. `gateway-staging` retains `.42`. Production is unchanged: `adguard-prod` keeps `.43`/`.45` (pinned by client DNS configs) and `gateway-production` keeps `.40`.

### LB IP pools

Two pools are present (unchanged for BGP — the prefix-list `10.42.2.0/24 ge 32 le 32` covers both):

| Pool | Range | Notes |
|:-----|:------|:------|
| `home-c-pool` | `10.42.2.40` – `10.42.2.254` | Legacy pool; covers all gateway and adguard IPs |
| `home-compute-pool` | `10.42.2.30` – `10.42.2.37` | Added 2026-05-05 for snapcast/compute-tier services |

### L2 announcement policy

A single `CiliumL2AnnouncementPolicy` named `l2-announcement-policy-staging` is in effect. Despite the name, it has **no service selector** (`spec: {externalIPs: true, loadBalancerIPs: true}`) so it covers all LB IPs cluster-wide. Phase 4a removes this single resource.

### UCGF state at Phase 0.1

| | |
|:--|:--|
| Firmware | 5.0.16 |
| Kernel | 5.4.213-ui-ipq9574 |
| FRR package | `frr 10.1.2-1+ubnt-35995+g695732fae09e` (arm64) |
| FRR service before pre-flight | `disabled / inactive (dead)` |
| Action taken | `bgpd=yes` in `/etc/frr/daemons` (backup at `/etc/frr/daemons.bak-pre-bgp`); `systemctl enable --now frr` |
| Dry-run `router bgp 65100` / `no router bgp 65100` | accepted, no errors |
| LAN segment | `br2 = 10.42.2.0/24`, gateway `10.42.2.1` |

**Operator note:** UniFi firmware upgrades may revert `/etc/frr/daemons`. Re-apply `bgpd=yes` and re-enable FRR after any upgrade — the existing Phase 1.4 persistence note already covers `frr.conf`; extend it to `daemons` as well.

---

## Survey 2026-05-03

**Current state:** Not started. `infra/controllers/cilium/default-values.yaml` still has `bgpControlPlane.enabled: false` and `l2announcements.enabled: false` (L2 is the active mode via `l2announcements.enabled: true` override in `values.yaml`, presumably). No `CiliumBGP*` CRs exist anywhere in `infra/configs/`. Phase 0 (pre-flight) has not been entered.

**Outstanding next steps:**

1. Phase 0: pre-flight (UCGF FRR capability check, Cilium BGP CRD presence, baseline LB IP snapshot, schedule a maintenance window).
2. Phase 1: configure BGP on the UCGF (vtysh, snapshot FRR config before/after).
3. Phase 2a: enable the BGP control plane in Cilium, canary on a single worker.
4. Phase 2b: promote to all workers once the canary soaks ≥1h cleanly.
5. Phase 3: full multi-worker soak (≥4h, ideally 24h).
6. Phase 4: cutover — delete the L2 announcement policy first, disable L2 in Helm values 24h later.
