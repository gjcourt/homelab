# cloudflare-tunnel

`cloudflared` Deployment that establishes the production Cloudflare Tunnel
(`production`) and proxies external hostnames (`*.burntbytes.com`) to the
in-cluster gateway. Manifests live directly under `apps/production/` because
this app has no staging counterpart — see below.

## No staging overlay

This app intentionally has no `apps/staging/cloudflare-tunnel/` overlay (and
no `apps/base/cloudflare-tunnel/` base either; the manifests live directly
under `apps/production/`).

Reason: the tunnel is bound to a specific Cloudflare account, tunnel ID, and
DNS records (`auth.burntbytes.com`, `links.burntbytes.com`,
`memos.burntbytes.com`, etc.). Staging traffic in this homelab is not
publicly exposed — staging hostnames resolve internally via the cluster
gateway. Running a second `cloudflared` instance for staging would require
provisioning a separate tunnel ID and credentials in Cloudflare, plus DNS
records that don't exist (and aren't wanted) for the `-stage` namespace
suffixes. The cost/value tradeoff doesn't justify the duplication.

To validate changes safely, edit the configmap or deployment, run
`kustomize build apps/production/cloudflare-tunnel`, and merge to `master`
through a PR. Cloudflare exposes tunnel health under
`http://cloudflared:2000/ready`; both replicas must report ready before
external traffic recovers. If a config change might break ingress, gate it
behind a temporary hostname first and validate via `cloudflared tunnel info`
before flipping production hosts.

## The public surface

`config.yaml`'s `ingress:` list **is** the cluster's entire inbound surface from
the internet. There is no other public path — everything else resolves only on
the LAN via the `*.burntbytes.com` wildcard and the AdGuard rewrite. Adding a
hostname here publishes it; removing one makes it LAN-only again.

Currently published:

| Hostname | App | Why public | Auth |
|---|---|---|---|
| `auth.burntbytes.com` | Authelia | Required — every OIDC redirect below terminates here | `one_factor` |
| `links.burntbytes.com` | Linkding | "Local cloud" — bookmarks from anywhere | OIDC, `two_factor` |
| `memos.burntbytes.com` | Memos | "Local cloud" — notes from anywhere | OIDC, `two_factor` |
| `food.burntbytes.com` | Mealie | "Local cloud" — recipes from anywhere | OIDC, `two_factor` |
| `burntbytes.com` + `-origin` | Blog | Public by definition | none |
| `cadence.burntbytes.com` | — | **Orphan** — see the note in `config.yaml` | none |

### Bar for adding a hostname

Authelia's `default_policy` is `bypass`, and these hostnames are **not**
gateway-protected by a forward-auth rule — the apps above authenticate
*in-app* as OIDC clients. So an app that does not enforce its own login is
wide open the moment it is added here.

Before adding an entry:

1. Confirm the app is an Authelia OIDC client with
   `authorization_policy: two_factor` in
   `apps/production/authelia/configuration.yaml`, **and** that the hostname you
   are publishing appears in that client's `redirect_uris`.
2. Confirm a production HTTPRoute actually serves the hostname (otherwise you
   publish a hostname with no backend — see the cadence note).
3. Add the Cloudflare DNS `CNAME` → `<tunnel-uuid>.cfargotunnel.com`.
   **DNS is not managed in this repo** and there is no external-dns; the record
   is created by hand in the Cloudflare dashboard. Without it the tunnel entry
   is inert.

No manual restart is needed: the `configMapGenerator` content-hashes the
ConfigMap name, so any change to `config.yaml` renames it, rewrites the
Deployment's volume reference, and Flux rolls the pods. (cloudflared does not
hot-reload its ingress config, which is why the hash suffix exists.)

### Residual risk on the OIDC apps

Enabling OIDC does not necessarily *disable* local password login. Neither
Memos nor Mealie has an explicit "local login disabled" setting in this repo,
and Mealie sets `OIDC_AUTO_REDIRECT: "false"`, so its login page offers both
paths. The consequence of publishing them is that a password form is reachable
from the internet and is brute-forceable independently of Authelia's 2FA. If
that matters, disable local auth in the app or put the hostname behind an
Authelia forward-auth rule instead of relying on in-app OIDC alone.
