---
status: Stable
last_modified: 2026-05-03
---

# AdGuard DNS — failover validation drill

Confirm the homelab DNS keeps resolving when one AdGuard pod, one node, or one LoadBalancer IP is unavailable. Run quarterly or after material AdGuard / Cilium / Talos changes.

Architecture context: [`docs/plans/2026-02-15-adguard-ha.md`](../plans/2026-02-15-adguard-ha.md).

## Pre-flight (skip if you just made changes)

```bash
# Both replicas Ready and on different nodes
kubectl -n adguard-prod get pods -l app.kubernetes.io/name=adguard -o wide

# LBs have IPs
kubectl -n adguard-prod get svc adguard adguard-dns-secondary

# DNS works through both
for ip in 10.42.2.43 10.42.2.45; do
  echo "--- $ip ---"
  dig @"$ip" +short +tries=1 +time=2 example.com || echo FAIL
done
```

If any of the above fails, fix that first — don't run the failover drill on a degraded baseline.

## Drill 1 — pod-level failure (no node loss)

Kill `adguard-0` (the primary). Service should keep responding via `adguard-1`. Logs should land on `adguard-1` until `adguard-0` recovers.

```bash
# Note baseline
PRIMARY_NODE=$(kubectl -n adguard-prod get pod adguard-0 -o jsonpath='{.spec.nodeName}')
echo "primary on $PRIMARY_NODE"

# Delete the primary pod
kubectl -n adguard-prod delete pod adguard-0

# Watch DNS keep responding (separate terminal recommended)
while true; do
  dig @10.42.2.43 +short +tries=1 +time=1 example.com >/dev/null && echo "$(date -u +%H:%M:%S) ok" || echo "$(date -u +%H:%M:%S) FAIL"
  sleep 1
done

# Confirm pod recovers
kubectl -n adguard-prod wait --for=condition=Ready pod/adguard-0 --timeout=2m
```

**Pass:** zero or transient (≤2s) FAIL lines during the kill→re-Ready window.
**Fail:** sustained DNS failures = the LoadBalancer is still routing to the dead pod, or readiness probes don't gate traffic correctly. Investigate `kubectl -n adguard-prod describe svc adguard` and the Cilium BPF service map.

## Drill 2 — node drain (one of two AdGuard nodes goes away)

Drain the node hosting one of the AdGuard pods. The pod should reschedule on a different node (preferred anti-affinity, hard topology spread). DNS should keep responding.

```bash
# Pick the node hosting adguard-0
TARGET=$(kubectl -n adguard-prod get pod adguard-0 -o jsonpath='{.spec.nodeName}')
echo "draining $TARGET"

kubectl drain "$TARGET" --ignore-daemonsets --delete-emptydir-data --force --timeout=2m

# Watch DNS (continue the loop from Drill 1 in a separate terminal)
# Confirm adguard-0 ends up on a different node
kubectl -n adguard-prod wait --for=condition=Ready pod/adguard-0 --timeout=3m
kubectl -n adguard-prod get pod adguard-0 -o wide

# Restore
kubectl uncordon "$TARGET"
```

**Pass:** DNS stays up; both pods end up on different nodes after the drain.
**Fail:**
- Pod can't schedule → not enough nodes free, or PVC is bound to a zone that no longer has nodes. Check `kubectl describe pod adguard-0` for scheduling events.
- DNS goes down for >5s → Cilium endpoint update lag, or the LB is advertising the IP via L2 from the drained node only. Check `cilium-cli endpoint list` and confirm `service/adguard` has endpoints from a healthy node.

## Drill 3 — primary DNS IP failure (force secondary client failover)

Simulate the primary LB IP being unreachable from a client. Confirm clients fall back to the secondary IP.

```bash
# From a workstation that resolves via the homelab DNS, query the secondary directly
dig @10.42.2.45 +short example.com

# Then test client-side fallback by listing what your OS picked up via DHCP
# macOS:
scutil --dns | grep "nameserver\["
# Linux:
resolvectl status | grep "DNS Servers"
# Windows:
# ipconfig /all | findstr /R /C:"DNS Servers"
```

**Pass:** the OS shows BOTH `10.42.2.43` and `10.42.2.45` in its DNS server list. If only one shows, **UniFi DHCP scope options aren't handing out both** — fix in UniFi → Networks → LAN → DHCP → Network options → DNS Server.

If you want to actively prove failover (rather than just confirm both are configured), block `10.42.2.43:53` on the workstation's firewall briefly and confirm OS-level DNS still resolves:

```bash
# macOS — block primary, observe secondary takes over
sudo pfctl -e 2>/dev/null
echo "block drop quick from any to 10.42.2.43" | sudo pfctl -f -
dig +tries=1 +time=2 example.com  # should still work
sudo pfctl -d  # restore
```

## Drill 4 — sync job validation

Confirm the most recent `adguard-sync` Job succeeded and replica config matches origin.

```bash
# Most recent Job + status
kubectl -n adguard-prod get jobs --sort-by=.metadata.creationTimestamp | tail -3
LATEST=$(kubectl -n adguard-prod get jobs --sort-by=.metadata.creationTimestamp -o name | tail -1 | cut -d/ -f2)
kubectl -n adguard-prod logs -l job-name="$LATEST" --tail=30
```

**Pass:** logs end with `INFO sync sync/sync.go:300 Sync done {...}` and no `ERROR` lines.
**Fail:** common modes:
- `401 Unauthorized` → `adguard-sync-credentials` Secret keys don't match the live AdGuard admin user (the gotcha from the original HA rollout — username is `george`, not `admin`).
- `connect: connection refused` → `adguard-1` is down or the headless Service is misconfigured.

## When everything passes

Note the date in [`docs/plans/2026-02-15-adguard-ha.md`](../plans/2026-02-15-adguard-ha.md) `last_modified` so the next person knows it was recently validated.

## Out of scope

- **Multi-region / multi-cluster failover** — single cluster only.
- **DNSSEC validation drill** — separate concern, not redundancy.
- **Upstream resolver failure** (Cloudflare/Quad9 unreachable) — AdGuard handles this internally with multiple upstreams; not exercised here.
