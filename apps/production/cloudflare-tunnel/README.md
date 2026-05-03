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
`overture.burntbytes.com`, etc.). Staging traffic in this homelab is not
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
