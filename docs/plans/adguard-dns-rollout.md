---
status: planned
last_modified: 2026-03-07
---

# AdGuard DNS Rollout Plan

Step-by-step guide to promote AdGuard Home as the primary DNS resolver for the entire LAN, using the UniFi gateway for DHCP and `1.1.1.2` (Cloudflare for Families) as the safety-net fallback.

## Prerequisites

| Item | Current State |
|:---|:---|
| AdGuard prod pod | `0/1` Running — stuck on first-launch setup wizard (port 3000) |
| AdGuard staging pod | `0/1` Running — same (port 3000) |
| Production DNS LB IP | `10.42.2.43` (port 53 exposed, but no endpoints yet) |
| Staging DNS LB IP | `10.42.2.42` (shared with staging gateway) |
| Production Gateway IP | `10.42.2.40` |
| Staging Gateway IP | `10.42.2.42` |
| UniFi DHCP DNS today | Likely router default (`10.42.2.1` or ISP DNS) |

## Network Topology (Target State)

```
┌─────────────────────────────────────────────────────────────────┐
│                        LAN (10.42.2.0/24)                       │
│                                                                 │
│  ┌──────────┐  DHCP: DNS1=10.42.2.43   ┌──────────────────┐    │
│  │  UniFi   │  DHCP: DNS2=1.1.1.2      │   K8s Cluster    │    │
│  │ Gateway  │◄─────────────────────────►│  (talos-ykb-uir) │    │
│  │10.42.2.1 │                           │   10.42.2.20     │    │
│  └──────────┘                           │                  │    │
│       │                                 │  ┌────────────┐  │    │
│       │ DNS queries                     │  │ AdGuard LB │  │    │
│       ▼                                 │  │ 10.42.2.43 │  │    │
│  ┌──────────┐                           │  │  port 53   │  │    │
│  │ Clients  │──── DNS ─────────────────►│  └─────┬──────┘  │    │
│  │ phones,  │                           │        │         │    │
│  │ laptops  │                           │  ┌─────▼──────┐  │    │
│  └──────────┘                           │  │ adguard-0  │  │    │
│                                         │  │ (primary)  │  │    │
│  If AdGuard is down:                    │  └────────────┘  │    │
│  ┌──────────┐                           │                  │    │
│  │ Clients  │──── DNS ────► 1.1.1.2     │  Upstream DNS:   │    │
│  │ (auto    │   (Cloudflare Families)   │  DoH to 1.1.1.2 │    │
│  │ failover)│                           │  + Quad9 backup  │    │
│  └──────────┘                           └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

## Phase 1: Complete AdGuard Initial Setup (Staging)

Do staging first so you can validate the entire flow before touching production.

### Step 1.1 — Access the setup wizard

```bash
# Port-forward the setup wizard (port 3000) to your laptop
kubectl port-forward -n adguard-stage pod/adguard-0 3000:3000
```

Open `http://localhost:3000` in your browser. You'll see the AdGuard Home setup wizard.

### Step 1.2 — Configure the setup wizard

| Setting | Value | Why |
|:---|:---|:---|
| Admin web interface | Listen on `0.0.0.0`, port `80` | Matches the readiness probe and service configuration |
| DNS server | Listen on `0.0.0.0`, port `53` | Required for DNS service |
| Admin username | Choose one (e.g., `admin`) | Write this down — it goes into the sync secret |
| Admin password | Choose a strong password | Same — needed for sync secret |

Click through the remaining wizard steps with defaults and finish setup.

### Step 1.3 — Verify the pod becomes Ready

```bash
# Watch the pod — it should transition to 1/1 Running within ~30 seconds
kubectl get pods -n adguard-stage -w
```

Once `adguard-0` is `1/1 Ready`, the LoadBalancer endpoints will populate and DNS will start responding.

### Step 1.4 — Configure upstream DNS

In the AdGuard web UI (`https://adguard.stage.burntbytes.com` or via port-forward to port 80):

1. Go to **Settings → DNS settings**
2. Set **Upstream DNS servers** to:
   ```
   https://security.cloudflare-dns.com/dns-query
   https://dns.quad9.net/dns-query
   ```
   This uses `1.1.1.2` (Cloudflare for Families — blocks malware) over DoH, with Quad9 as backup.
3. Set **Bootstrap DNS servers** to:
   ```
   1.1.1.2
   9.9.9.9
   ```
   These are used to resolve the DoH hostnames themselves (chicken-and-egg problem).
4. Enable **Parallel requests** to query all upstreams simultaneously for fastest response.
5. Click **Apply**.

### Step 1.5 — Configure DNS rewrites (wildcard entries)

Go to **Filters → DNS Rewrites** and add:

| Domain | Answer |
|:---|:---|
| `*.stage.burntbytes.com` | `10.42.2.42` |
| `*.burntbytes.com` | `10.42.2.40` |

> **Order matters**: Put the `*.stage.burntbytes.com` entry first so it takes priority over the broader wildcard. AdGuard evaluates rewrites in order and the most specific match wins.

### Step 1.6 — Test staging DNS resolution

```bash
# Test DNS resolution against the staging AdGuard LB IP
dig @10.42.2.42 google.com +short
# Should return a public IP

dig @10.42.2.42 homepage.stage.burntbytes.com +short
# Should return 10.42.2.42 (staging gateway)

dig @10.42.2.42 homepage.burntbytes.com +short
# Should return 10.42.2.40 (production gateway)

# Test ad-blocking
dig @10.42.2.42 ads.google.com +short
# Should return 0.0.0.0 or NXDOMAIN (blocked)
```

---

## Phase 2: Complete AdGuard Initial Setup (Production)

Repeat the same steps for production.

### Step 2.1 — Access the setup wizard

```bash
kubectl port-forward -n adguard-prod pod/adguard-0 3000:3000
```

Open `http://localhost:3000`.

### Step 2.2 — Configure the setup wizard

Same settings as staging (listen `0.0.0.0:80` for web, `0.0.0.0:53` for DNS). Use the same or different admin credentials — your choice.

### Step 2.3 — Verify the pod becomes Ready

```bash
kubectl get pods -n adguard-prod -w
```

### Step 2.4 — Configure upstream DNS

Same as staging:
- Upstream: `https://security.cloudflare-dns.com/dns-query` + `https://dns.quad9.net/dns-query`
- Bootstrap: `1.1.1.2`, `9.9.9.9`
- Enable parallel requests

### Step 2.5 — Configure DNS rewrites

| Domain | Answer |
|:---|:---|
| `*.stage.burntbytes.com` | `10.42.2.42` |
| `*.burntbytes.com` | `10.42.2.40` |

### Step 2.6 — Test production DNS resolution

```bash
dig @10.42.2.43 google.com +short
dig @10.42.2.43 homepage.burntbytes.com +short  # expect 10.42.2.40
dig @10.42.2.43 homepage.stage.burntbytes.com +short  # expect 10.42.2.42
dig @10.42.2.43 ads.google.com +short  # expect blocked
```

---

## Phase 3: Update Sync Credentials

After both instances have admin accounts, update the SOPS-encrypted sync secrets so the CronJob can replicate config from `adguard-0` to replicas.

### Step 3.1 — Edit the sync secrets

```bash
# Production
sops apps/production/adguard/secret-sync.yaml
# Set: ORIGIN_USERNAME, ORIGIN_PASSWORD to the admin creds you chose
# Set: REPLICA1_USERNAME, REPLICA1_PASSWORD (same creds, or create a separate account)

# Staging
sops apps/staging/adguard/secret-sync.yaml
# Same structure
```

### Step 3.2 — Commit and push

```bash
git add apps/production/adguard/secret-sync.yaml apps/staging/adguard/secret-sync.yaml
git commit -m "Update adguard-sync credentials after initial setup"
git push
```

---

## Phase 4: Point One Device at AdGuard (Smoke Test)

Before changing DHCP for the whole network, test with a single device.

### Step 4.1 — Manual DNS override on your laptop

**macOS:**
```bash
# Set DNS to AdGuard prod only
sudo networksetup -setdnsservers Wi-Fi 10.42.2.43
```

**Or via System Settings:** Wi-Fi → Details → DNS → Add `10.42.2.43`, remove others.

### Step 4.2 — Validate everything works

```bash
# Normal resolution
nslookup google.com
nslookup github.com

# Internal resolution (split-horizon)
nslookup homepage.burntbytes.com
# Should return 10.42.2.40

# Ad blocking
nslookup ads.google.com
# Should be blocked
```

Browse the web normally for 10-15 minutes. Check the AdGuard query log at `https://adguard.burntbytes.com` to see your queries flowing through.

### Step 4.3 — Revert if needed

```bash
# Restore DHCP-assigned DNS
sudo networksetup -setdnsservers Wi-Fi Empty
```

---

## Phase 5: Configure UniFi Gateway (Network-Wide Rollout)

This is the "big switch" — after this, all DHCP clients will use AdGuard.

### Step 5.1 — Log in to UniFi Network

Open the UniFi Network Controller (usually `https://unifi.local` or your gateway IP).

### Step 5.2 — Set DHCP DNS servers

Navigate to: **Settings → Networks → (your LAN network) → DHCP → DNS Server**

Configure:

| Field | Value | Purpose |
|:---|:---|:---|
| **DNS Server 1 (Primary)** | `10.42.2.43` | AdGuard Home production |
| **DNS Server 2 (Secondary)** | `1.1.1.2` | Cloudflare for Families (fallback) |

> **Why `1.1.1.2` and not `1.1.1.1`?** `1.1.1.2` is Cloudflare's "for Families" resolver which blocks known malware domains. It's a safe fallback — if AdGuard goes down, clients still get malware protection without ads slipping through a fully open resolver.

### Step 5.3 — Set the gateway's own DNS

UniFi gateways use their own DNS for NTP, firmware checks, etc. Set this too:

Navigate to: **Settings → Internet → (your WAN) → Advanced → DNS Server**

| Field | Value |
|:---|:---|
| **Primary DNS** | `10.42.2.43` |
| **Secondary DNS** | `1.1.1.2` |

> **Note**: Some UniFi firmware versions put this under **Settings → Networks → WAN → DNS**. The exact path varies by version.

### Step 5.4 — Force DHCP lease renewal

Clients will pick up the new DNS on their next DHCP renewal. To speed this up:

```bash
# On your Mac
sudo ipconfig set en0 DHCP  # re-acquire DHCP lease

# Or restart networking on individual devices
# Most devices renew within 5-15 minutes on their own
```

### Step 5.5 — Verify clients are using AdGuard

Check the AdGuard query log at `https://adguard.burntbytes.com` — you should see queries from various client IPs appearing.

```bash
# Verify your current DNS server
scutil --dns | grep "nameserver\[0\]"
# Should show 10.42.2.43
```

---

## Phase 6: Post-Rollout Hardening

### Step 6.1 — Enable ad-blocking filters

In the AdGuard UI (**Filters → DNS Blocklists**), enable or add:

- **AdGuard DNS filter** (default, pre-enabled)
- **AdAway Default Blocklist**
- **Steven Black's Unified Hosts** (optional, aggressive)

Start conservative — you can always add more blocklists later. Check the query log for false positives.

### Step 6.2 — Configure client-specific settings (optional)

If certain devices need unfiltered DNS (e.g., a smart TV that breaks with ad-blocking):

1. Go to **Settings → Client settings**
2. Add the device by IP or MAC
3. Disable filtering for that specific client

### Step 6.3 — Monitor for issues

Watch for the first 24-48 hours:
- Check the **Query Log** for blocked domains that shouldn't be
- Watch for family members complaining about broken sites
- Add exceptions to **Filters → Custom filtering rules** as needed:
  ```
  @@||example.com^  # Whitelist a domain
  ```

---

## Rollback Plan

If something goes wrong after Phase 5, you have two levels of rollback:

### Quick rollback (< 1 minute)
Change UniFi DHCP DNS back to:
- **DNS 1**: `1.1.1.2`
- **DNS 2**: `9.9.9.9`

All clients fail over to Cloudflare + Quad9 immediately (within their DNS cache TTL, typically seconds).

### Emergency (AdGuard LB unreachable)
Clients automatically fail over to DNS 2 (`1.1.1.2`) — this is the whole point of the dual-DNS setup. No action needed; resolution continues with slightly less ad-blocking.

---

## IP Reference Table

| Resource | IP | Port(s) |
|:---|:---|:---|
| AdGuard prod (DNS) | `10.42.2.43` | 53 (UDP/TCP) |
| AdGuard prod (Admin UI) | via `adguard.burntbytes.com` | 443 (HTTPS) |
| AdGuard staging (DNS) | `10.42.2.42` | 53 (UDP/TCP) |
| Production Gateway | `10.42.2.40` | 80, 443 |
| Staging Gateway | `10.42.2.42` | 80, 443 |
| Fallback DNS | `1.1.1.2` | 53 |
| Talos node | `10.42.2.20` | — |
| Synology NAS | `10.42.2.11` | — |

---

## Future: HA DNS (Two LB IPs)

Once you have more nodes, consider the plan in [adguard-ha.md](adguard-ha.md):
1. Scale StatefulSet to 2 replicas
2. Allocate a second LB IP for the second pod
3. Configure UniFi DHCP with both IPs as DNS 1 and DNS 2
4. Remove `1.1.1.2` as fallback (AdGuard handles its own HA)
