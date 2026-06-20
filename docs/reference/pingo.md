# Pingo

Pingo is a background job that runs as a CronJob to update DNS records for `vpn.burntbytes.com`.

## Architecture

- **Deployment Type**: CronJob (runs every 5 minutes)
- **Namespace**: `pingo`
- **Image**: `ghcr.io/gjcourt/pingo`

## Configuration

Pingo requires a secret to authenticate with the DNS provider. The secret is managed via SOPS and should contain the necessary API tokens and domain configuration.

### Secret Structure

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: pingo-secret
  namespace: pingo
type: Opaque
stringData:
  CLOUDFLARE_API_TOKEN: "your-api-token"
  DOMAINS: "vpn.burntbytes.com"
  # Optional — AdGuard Home rewrite sync (see below). Omit to disable.
  ADGUARD_URLS: "http://adguard-admin.adguard-prod.svc.cluster.local:8080"
  ADGUARD_USERNAME: "admin"
  ADGUARD_PASSWORD: "your-adguard-password"
```

## AdGuard rewrite sync

Pingo also keeps a **DNS rewrite** for `vpn.burntbytes.com` on AdGuard, in sync
with the public IP, so the WireGuard endpoint resolves correctly **from inside
the LAN** too.

**Why this is needed:** AdGuard serves a split-horizon wildcard
`*.burntbytes.com → <k8s ingress>` that correctly routes every HTTP service. But
`vpn.burntbytes.com` is a WireGuard UDP endpoint, not an HTTP service behind the
ingress — on-LAN it must resolve to the **live public IP** (reached via the
gateway's NAT hairpin), not the ingress. A more-specific AdGuard rewrite for
`vpn.burntbytes.com` overrides the wildcard; Pingo updates that rewrite on every
run alongside the Cloudflare record. Off-LAN, public DNS already returns the
correct IP, so nothing changes there.

- Pingo writes to the **primary** AdGuard admin Service (`adguard-admin`, port
  8080). The `adguardhome-sync` CronJob propagates the rewrite to the replicas,
  so a single `ADGUARD_URLS` entry suffices. (List multiple comma-separated URLs
  only if instances stop replicating between themselves.)
- Cross-namespace access is allowed by a dedicated ingress rule in
  `apps/base/adguard/networkpolicy.yaml` (pingo namespace → admin port 80 after
  DNAT), mirroring the homepage-widget rule.
- The endpoint must stay **DNS-only** in Cloudflare (`PROXIED` unset/false): a
  WireGuard handshake needs the real IP, and AdGuard rewrites have no proxy
  concept.

## Operations

### Viewing Logs

To view the logs for the latest Pingo job:

```bash
kubectl logs -n pingo -l app.kubernetes.io/name=pingo
```

### Triggering a Manual Run

To trigger a manual run of the Pingo CronJob:

```bash
kubectl create job --from=cronjob/pingo pingo-manual-run -n pingo
```
