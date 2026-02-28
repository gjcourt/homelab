---
status: not-started
last_modified: 2026-02-28
---

# Network Migration Plan: 192.168.5.0/24 → 10.42.2.0/24

This document is the authoritative checklist for migrating the `melodic-muse` homelab
from its current `192.168.5.0/24` network to the new `10.42.2.0/24` network.
Follow the phases **in order**. Phase 1 (infra) and the teardown of old assignments
should happen at cut-over time; Phases 2–4 (manifests, docs, out-of-band) can be
prepared in advance on a feature branch.

## IP Address Mapping Reference

| Role | Old IP / CIDR | New IP / CIDR |
|---|---|---|
| Synology NAS (DSM / iSCSI / NFS) | `192.168.5.8` | `10.42.2.21` |
| Production Gateway LoadBalancer | `192.168.5.33` | `10.42.2.30` |
| Staging Gateway LoadBalancer | `192.168.5.30` | `10.42.2.31` |
| Cilium LB IP pool | `192.168.5.30 – 192.168.5.255` | `10.42.2.30 – 10.42.2.254` |
| Node / host trusted network | `192.168.5.0/24` | `10.42.2.0/24` |
| Talos nodes (static, out-of-band) | `192.168.5.1–N` | `10.42.2.10 – 10.42.2.15` |

> **Note:** The production gateway assignment deliberately uses `.30` (the lowest
> allocatable IP in the new pool) and staging uses `.31`. This is a swap relative
> to the old network where `.30` was staging. Ensure any documentation that referred
> to the old production gateway as `192.168.5.31` (some docs) or `192.168.5.33`
> (manifests — the authoritative value) is updated to `10.42.2.30`.

---

## Phase 1 — Infrastructure (Cilium / CSI)

These files directly control network and storage behaviour. They should be applied
**at cut-over** to avoid Cilium announcing obsolete IPs or the CSI driver failing to
reach the NAS.

### 1.1 Cilium LoadBalancer IP Pool

**File:** [`infra/configs/cilium/load-balancer-ip-pool.yaml`](../../infra/configs/cilium/load-balancer-ip-pool.yaml)

The file already contains the new range commented out. Replace the active block:

```yaml
# Before
spec:
  blocks:
    - start: 192.168.5.30
      stop: 192.168.5.255
  # - start: 10.42.2.30
  #   stop: 10.42.2.255

# After
spec:
  blocks:
    - start: 10.42.2.30
      stop: 10.42.2.254
```

> After Flux reconciles this, Cilium will stop announcing the old range via ARP
> and switch to `10.42.2.30–10.42.2.254`. Any existing `LoadBalancer` services
> will have their IPs re-allocated; update AdGuard DNS rewrites at the same time
> (see Phase 4).

### 1.2 Synology CSI Driver Values

**File:** [`infra/controllers/synology-csi/values.yaml`](../../infra/controllers/synology-csi/values.yaml)

Three `dsm:` fields reference the NAS. Change each:

```yaml
# Before (all three storageClasses)
      dsm: 192.168.5.8

# After
      dsm: 10.42.2.21
```

> The CSI controller and node plugins will reconnect to the NAS at the new IP.
> Existing PVs are not affected as long as the NAS has no active iSCSI sessions
> disrupted during the cut-over.

### 1.3 Synology CSI Secret (`client-info.yaml`)

**File:** [`infra/controllers/synology-csi/secret-client-info.yaml`](../../infra/controllers/synology-csi/secret-client-info.yaml)

The secret is SOPS-encrypted. Decrypt, verify whether the DSM URL inside
`client-info.yml` embeds a host IP (common in the Synology CSI YAML format),
and update it if needed. Re-encrypt and commit.

```bash
# Inspect (do not commit decrypted output)
sops -d infra/controllers/synology-csi/secret-client-info.yaml | grep -E 'host|dsm|ip'
```

If a bare IP appears, update it from `192.168.5.8` to `10.42.2.21`, then:

```bash
sops -e -i infra/controllers/synology-csi/secret-client-info.yaml
```

---

## Phase 2 — App Manifests

These are changes to Kubernetes manifests under `apps/`. They can be committed to
the feature branch before cut-over and will become effective when Flux reconciles
after the network has been switched.

### 2.1 Cilium L2 LB pool assignments — Gateway `hostAliases` patches

Apps use `hostAliases` so that pods can resolve the Authelia OIDC issuer URL
(which resolves through AdGuard, not CoreDNS) to the Gateway IP. Each overlay
must point at the **correct new gateway IP for that environment**.

| Overlay | Old IP | New IP |
|---|---|---|
| `apps/staging/` | `192.168.5.30` | `10.42.2.31` |
| `apps/production/` | `192.168.5.33` | `10.42.2.30` |

**Files to update (staging — `ip: "192.168.5.30"` → `ip: "10.42.2.31"`):**

- [`apps/staging/audiobookshelf/deployment-patch.yaml`](../../apps/staging/audiobookshelf/deployment-patch.yaml)
- [`apps/staging/immich/deployment-patch.yaml`](../../apps/staging/immich/deployment-patch.yaml)
- [`apps/staging/linkding/deployment-patch.yaml`](../../apps/staging/linkding/deployment-patch.yaml)
- [`apps/staging/mealie/deployment-patch.yaml`](../../apps/staging/mealie/deployment-patch.yaml)
- [`apps/staging/memos/deployment-patch.yaml`](../../apps/staging/memos/deployment-patch.yaml)

**Files to update (production — `ip: "192.168.5.33"` → `ip: "10.42.2.30"`):**

- [`apps/production/audiobookshelf/deployment-patch.yaml`](../../apps/production/audiobookshelf/deployment-patch.yaml)
- [`apps/production/immich/deployment-patch.yaml`](../../apps/production/immich/deployment-patch.yaml)
- [`apps/production/linkding/deployment-patch.yaml`](../../apps/production/linkding/deployment-patch.yaml)
- [`apps/production/mealie/deployment-patch.yaml`](../../apps/production/mealie/deployment-patch.yaml)
- [`apps/production/memos/deployment-patch.yaml`](../../apps/production/memos/deployment-patch.yaml)

### 2.2 NFS PersistentVolume Server Addresses

All NFS `PersistentVolume` resources point `spec.nfs.server` at the NAS.
Change `192.168.5.8` → `10.42.2.21` in each:

| File | Notes |
|---|---|
| [`apps/base/jellyfin/media/nfs-media.yaml`](../../apps/base/jellyfin/media/nfs-media.yaml) | Three separate PV definitions |
| [`apps/production/navidrome/nfs-music.yaml`](../../apps/production/navidrome/nfs-music.yaml) | Production music share |
| [`apps/staging/navidrome/nfs-music.yaml`](../../apps/staging/navidrome/nfs-music.yaml) | Staging music share |
| [`apps/production/immich/nfs-photos.yaml`](../../apps/production/immich/nfs-photos.yaml) | Production photos share |
| [`apps/staging/immich/nfs-photos.yaml`](../../apps/staging/immich/nfs-photos.yaml) | Staging photos share |

> Changing the `server` field on an existing `PersistentVolume` requires
> **deleting and recreating the PV** if the volume is in `Released` or
> `Available` state; if `Bound`, you must first scale the consuming Deployment
> to zero, delete the PVC/PV pair, update the manifest, and re-apply to allow
> the workload to remount from the correct NAS IP.

### 2.3 Synology iSCSI Monitor

**File:** [`apps/base/synology-iscsi-monitor/deployment.yaml`](../../apps/base/synology-iscsi-monitor/deployment.yaml)

```yaml
# Before
value: "192.168.5.8"

# After
value: "10.42.2.21"
```

**File:** [`apps/base/synology-iscsi-monitor/script-cm.yaml`](../../apps/base/synology-iscsi-monitor/script-cm.yaml)

```python
# Before (default fallback)
SYNOLOGY_IP = os.environ.get("SYNOLOGY_IP", "192.168.5.8")

# After
SYNOLOGY_IP = os.environ.get("SYNOLOGY_IP", "10.42.2.21")
```

### 2.4 Home Assistant Trusted Proxies

**File:** [`apps/base/homeassistant/configmap.yaml`](../../apps/base/homeassistant/configmap.yaml)

Home Assistant trusts Envoy traffic arriving from the host network CIDR:

```yaml
# Before
- 192.168.5.0/24 # Trust Node Network (Envoy HostNetwork)

# After
- 10.42.2.0/24 # Trust Node Network (Envoy HostNetwork)
```

> Note: The Pod CIDR `10.244.0.0/16` does not change.

### 2.5 Homepage DSM Links

Both the staging and production Homepage `services.yaml` configmaps contain a
direct link to the Synology DSM web UI. Update the href and monitoring URL:

```yaml
# Before
href: https://192.168.5.8:5001
url: https://192.168.5.8:5001

# After
href: https://10.42.2.21:5001
url: https://10.42.2.21:5001
```

**Files:**

- [`apps/staging/homepage/services.yaml`](../../apps/staging/homepage/services.yaml)
- [`apps/production/homepage/services.yaml`](../../apps/production/homepage/services.yaml)

---

## Phase 3 — Documentation Updates

Update all documentation that references old IPs so that runbooks remain accurate.

| File | What to update |
|---|---|
| [`docs/architecture/dns-strategy.md`](../architecture/dns-strategy.md) | All `192.168.5.30/31` gateway IP references; LB pool range; table of AdGuard rewrites |
| [`docs/infra/cilium.md`](../infra/cilium.md) | LB pool range `192.168.5.30 – 192.168.5.255` |
| [`docs/infra/storage.md`](../infra/storage.md) | NAS IP `192.168.5.8`, DSM URL |
| [`docs/infra/kernel-log-shipping.md`](../infra/kernel-log-shipping.md) | Node IP `192.168.5.1` (three occurrences); use `10.42.2.10` as an example node |
| [`docs/apps/linkding.md`](../apps/linkding.md) | Gateway IP in SSO integration note (`192.168.5.33`) |
| [`docs/apps/mealie.md`](../apps/mealie.md) | Gateway IP in SSO integration note (`192.168.5.33`) |
| [`docs/apps/memos.md`](../apps/memos.md) | Gateway IP in SSO integration note (`192.168.5.33`) |
| [`docs/guides/synology-iscsi-operations.md`](../guides/synology-iscsi-operations.md) | NAS IP `192.168.5.8` (diagram + export line) |
| [`docs/plans/authelia-sso-rollout.md`](authelia-sso-rollout.md) | Staging `192.168.5.30` and prod `192.168.5.33` references |
| [`scripts/synology/README.md`](../../scripts/synology/README.md) | `SYNOLOGY_HOST` example value |
| [`.github/instructions/synology.instructions.md`](../../.github/instructions/synology.instructions.md) | NAS IP (`192.168.5.8`) and DSM URL in both locations |

> `docs/incidents/` files are **historical records and should NOT be edited**;
> they accurately describe the environment at the time of the incident.

### 3.1 vector nodeport comment

**File:** [`infra/controllers/vector/nodeport.yaml`](../../infra/controllers/vector/nodeport.yaml)

Update the header comment to reflect the new node IP range:

```yaml
# Before
# Talos is configured to send json_lines logs to tcp://192.168.5.1:30600.

# After
# Talos is configured to send json_lines logs to tcp://<node-ip>:30600
# where <node-ip> is in the range 10.42.2.10–10.42.2.15.
```

---

## Phase 4 — Out-of-Band (Not in Git)

These changes are applied directly to infrastructure devices and are **not tracked in
this repository**. They must be completed at (or before) cut-over.

### 4.1 Synology NAS — Static IP Change

In DSM → Control Panel → Network → Network Interface:
- Change the NAS IP address from `192.168.5.8` to **`10.42.2.21`**.
- Update the gateway and DNS entries to match the new network.
- Verify iSCSI target accessibility: `nc -zv 10.42.2.21 3260`
- Verify DSM API reachability: `curl -k https://10.42.2.21:5001/webapi/auth.cgi`

### 4.2 Talos Machine Configuration — Kernel Log Shipping

Each Talos node's machine config must be patched to update the log destination:

```bash
# Per-node (replace 10.42.2.10 with the actual node IP for each node)
talosctl -n <node-new-ip> patch machineconfig \
  '[{"op":"replace","path":"/machine/logging/destinations/0/endpoint","value":"tcp://10.42.2.10:30600"}]'
```

> The NodePort `30600` is unchanged. The destination host must be a node IP
> in `10.42.2.10–10.42.2.15` (each node should point at **its own IP** so that
> `externalTrafficPolicy: Local` routes correctly). See
> [`docs/infra/kernel-log-shipping.md`](../infra/kernel-log-shipping.md) for full context.

### 4.3 AdGuard Home — DNS Rewrites

In the AdGuard DNS rewrites configuration, update the two wildcard rules:

| Domain | Old Target | New Target |
|---|---|---|
| `*.burntbytes.com` | `192.168.5.31` or `192.168.5.33` | `10.42.2.30` |
| `*.stage.burntbytes.com` | `192.168.5.30` | `10.42.2.31` |

> These must be set **after** Cilium has assigned the new IPs to the gateway
> services, or clients will briefly resolve to unreachable addresses.

### 4.4 Router / DHCP

- Update any static DHCP leases for cluster nodes.
- Ensure the default gateway and DNS server for the `10.42.2.0/24` subnet are
  configured correctly.
- If the AdGuard instance(s) have static IPs, update those reservations too.

### 4.5 Cloudflare Tunnel (if applicable)

If the Cloudflare Tunnel connector is configured with a private-network route
advertisement to `192.168.5.0/24`, update it to advertise `10.42.2.0/24` in the
Cloudflare Zero Trust dashboard under Networks → Tunnels → Private Networks.

---

## Cut-Over Sequence

To minimise downtime, follow this order:

1. **Pre-stage**: Commit all Phase 1–3 changes on a feature branch. Do **not**
   merge to `master` until ready.
2. **NAS IP change** (Phase 4.1): The iSCSI and NFS sessions will drop briefly.
   Scale down any PVC-dependent workloads if you want a clean unmount first.
3. **Flux reconcile Phase 1** (apply the feature branch to the cluster via Flux,
   or manually `kubectl apply` the infra configs):
   - Cilium LB pool → announces new range.
   - CSI driver → reconnects to `10.42.2.21`.
4. **Talos node reconfiguration** (Phase 4.2): Update log destinations per node.
5. **AdGuard DNS rewrites** (Phase 4.3): Point wildcards at new gateway IPs.
6. **Merge PR to `master`**: All remaining app manifest and doc changes applied.
7. **Verify** (see Validation section below).

---

## Validation Checklist

After cut-over, confirm the following:

```bash
# NAS reachable at new IP
curl -k -s https://10.42.2.21:5001/webapi/auth.cgi | grep -q error_details && echo "NAS OK"

# CSI driver healthy (no ErrSync / connection errors)
kubectl -n synology-csi logs deploy/synology-csi-controller | tail -30

# Cilium LB pool updated and no old IPs assigned
kubectl get svc -A | grep LoadBalancer
# Should return 10.42.2.x IPs only

# NFS mounts working (spot-check Jellyfin)
kubectl -n jellyfin exec deploy/jellyfin -- df -h /media | grep -q nfs

# Gateway health
kubectl get gateway -A
kubectl get httproute -A

# iSCSI monitor reports no errors
kubectl -n synology-iscsi-monitor logs deploy/synology-iscsi-monitor | tail -30

# Staging app accessible
curl -I https://jellyfin.stage.burntbytes.com

# Production app accessible
curl -I https://jellyfin.burntbytes.com

# Kernel logs arriving in Loki/Grafana (check vector receiver)
kubectl -n monitoring logs ds/vector | grep "talos-logs" | tail -10
```

---

## Summary of Files Changed in Git

| File | Change |
|---|---|
| `infra/configs/cilium/load-balancer-ip-pool.yaml` | Pool range → `10.42.2.30–10.42.2.254` |
| `infra/controllers/synology-csi/values.yaml` | `dsm:` × 3 → `10.42.2.21` |
| `infra/controllers/synology-csi/secret-client-info.yaml` | Re-encrypt with updated DSM host if needed |
| `infra/controllers/vector/nodeport.yaml` | Comment updated to node range |
| `apps/base/jellyfin/media/nfs-media.yaml` | `server:` × 3 → `10.42.2.21` |
| `apps/base/synology-iscsi-monitor/deployment.yaml` | `SYNOLOGY_IP` env → `10.42.2.21` |
| `apps/base/synology-iscsi-monitor/script-cm.yaml` | Default fallback IP → `10.42.2.21` |
| `apps/base/homeassistant/configmap.yaml` | Trusted proxy CIDR → `10.42.2.0/24` |
| `apps/production/navidrome/nfs-music.yaml` | `server:` → `10.42.2.21` |
| `apps/production/immich/nfs-photos.yaml` | `server:` → `10.42.2.21` |
| `apps/production/audiobookshelf/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.30` |
| `apps/production/immich/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.30` |
| `apps/production/linkding/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.30` |
| `apps/production/mealie/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.30` |
| `apps/production/memos/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.30` |
| `apps/production/homepage/services.yaml` | DSM href/url → `10.42.2.21:5001` |
| `apps/staging/navidrome/nfs-music.yaml` | `server:` → `10.42.2.21` |
| `apps/staging/immich/nfs-photos.yaml` | `server:` → `10.42.2.21` |
| `apps/staging/audiobookshelf/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.31` |
| `apps/staging/immich/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.31` |
| `apps/staging/linkding/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.31` |
| `apps/staging/mealie/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.31` |
| `apps/staging/memos/deployment-patch.yaml` | `hostAliases ip:` → `10.42.2.31` |
| `apps/staging/homepage/services.yaml` | DSM href/url → `10.42.2.21:5001` |
| `docs/architecture/dns-strategy.md` | All gateway and pool IP references |
| `docs/infra/cilium.md` | LB pool range |
| `docs/infra/storage.md` | NAS IP and DSM URL |
| `docs/infra/kernel-log-shipping.md` | Node IP example (`192.168.5.1` × 3) |
| `docs/apps/linkding.md` | Gateway IP in SSO note |
| `docs/apps/mealie.md` | Gateway IP in SSO note |
| `docs/apps/memos.md` | Gateway IP in SSO note |
| `docs/guides/synology-iscsi-operations.md` | NAS IP in diagram and export |
| `docs/plans/authelia-sso-rollout.md` | Staging/prod gateway IPs |
| `scripts/synology/README.md` | `SYNOLOGY_HOST` example |
| `.github/instructions/synology.instructions.md` | NAS IP and DSM URL |
