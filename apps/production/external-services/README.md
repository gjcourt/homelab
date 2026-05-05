# external-services

Kubernetes `Service` + `Endpoints` objects that expose LAN appliances
(Synology NAS, TrueNAS, router, go-librespot speakers) as in-cluster
Services, plus `HTTPRoute` resources that route `*.burntbytes.com` hostnames
to those endpoints via the production gateway.

Manifests live directly under `apps/production/` because this app has no
staging counterpart — see below.

## No staging overlay

This app intentionally has no `apps/staging/external-services/` overlay (and
no `apps/base/external-services/` base; the manifests live directly under
`apps/production/`).

Reason: external-services reverse-proxies LAN appliances (Synology NAS,
TrueNAS, router) that exist only on the production network and have no
staging equivalent. The `Endpoints` objects carry hard-coded production IP
addresses (`10.42.x.x`). There are no staging versions of these physical
devices, and creating dummy staging endpoints would produce non-functional
routes with no validation value.

To validate changes safely, run
`kustomize build apps/production/external-services` locally before pushing,
then verify the target appliance is reachable at its IP from within the
cluster after merge.
