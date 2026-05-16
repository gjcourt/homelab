# truenas-iscsi-monitor

Prometheus exporter (Python + websockets + kubernetes client) that connects
to the TrueNAS Scale instance at hestia (`10.42.2.10`) over the WebSocket
JSON-RPC API to scrape iSCSI target/extent state, then joins each extent
back to a Kubernetes PV's claimRef to expose per-PVC labeled metrics.

Plus a Grafana dashboard (auto-discovered via `grafana_dashboard: "1"`
ConfigMap label) and a PrometheusRule for SAN-side alerts.

## Two-source design (different from the Synology exporter)

The Synology CSI driver writes `<namespace>/<pvc>` into each LUN's
description field, so the Synology exporter just parses INI config files.

democratic-csi (the TrueNAS CSI driver) leaves the iSCSI extent comment
field empty; the only PV identifier on the TrueNAS side is the extent
name pattern `csi-pvc-<UUID>-k8s`. To label by namespace/pvc/app, this
exporter performs a **two-source join**:

1. WebSocket JSON-RPC to TrueNAS:
   - `iscsi.target.query([])` for the target count
   - `iscsi.extent.query([])` for extent name + path
   - `pool.dataset.query([[type=VOLUME]])` for zvol provisioned + used bytes
2. In-cluster Kubernetes API (read-only ClusterRole on PVs, see `rbac.yaml`):
   - `list_persistent_volume()` for `pv.spec.claimRef`

The join: regex `^csi-(pvc-[0-9a-f-]+)-k8s$` → PV name → `claimRef`.

If democratic-csi's naming template ever changes, the regex stops matching
and `truenas_iscsi_unmatched_extents_total` increments. There's a
PrometheusRule alert for that case (`TrueNASISCSIUnmatchedExtents`).

## No staging overlay

By design — same reasoning as `synology-iscsi-monitor`:

- the exporter is a singleton scraper aimed at one piece of shared homelab
  infrastructure (one TrueNAS at one fixed IP, one API key);
- running a second instance in staging would double the scrape load and
  duplicate every alert (which describes real SAN conditions, not
  per-environment app behavior);
- requires either reusing the production API key (defeating the purpose
  of an isolated stage) or provisioning a second API key just to satisfy
  parity.

To validate changes safely:
- Run `kustomize build apps/production/truenas-iscsi-monitor` locally
  before pushing.
- For exporter script changes (`script-cm.yaml`), test the Python locally
  against TrueNAS with a port-forwarded kubeconfig and inspect the
  `/metrics` output before merging.
- For dashboard / PrometheusRule edits, render via
  `kubectl apply --dry-run=server` against the production namespace.

## Operator setup checklist

Before this app can come up healthy:

1. **Create a dedicated TrueNAS API key** in the TrueNAS UI:
   `Settings → API Keys → Add`. Bind it to a least-privileged user if
   possible — TrueNAS 26.x API keys are admin-scoped at the API level,
   but tying the key to a low-permission user limits real damage.
2. **SOPS-encrypt the key** at `apps/production/truenas-iscsi-monitor/secret.yaml`
   following the format in `apps/production/synology-iscsi-monitor/secret.yaml`
   (single field `api_key:`, namespace `truenas-iscsi-monitor`, secret
   name `truenas-iscsi-monitor-secret`). A starter template is committed
   at `secret.yaml.example`.
3. **Wire the app into Flux** by adding `- truenas-iscsi-monitor` to
   `apps/production/kustomization.yaml`.

## Metrics exposed

| Metric | Type | Labels | Description |
|---|---|---|---|
| `truenas_iscsi_target_count` | gauge | — | Number of iSCSI targets configured on TrueNAS |
| `truenas_iscsi_extent_count` | gauge | — | Number of iSCSI extents (LUNs) configured on TrueNAS |
| `truenas_iscsi_extent_size_bytes` | gauge | `app, environment, namespace, pvc` | Provisioned size (zvol `volsize`) per extent |
| `truenas_iscsi_extent_used_bytes` | gauge | `app, environment, namespace, pvc` | Actual used (zfs `used`) per extent — useful because zvols are thin-provisioned |
| `truenas_iscsi_unmatched_extents_total` | counter | — | Extents whose name didn't match `csi-pvc-<UUID>-k8s`. Should stay flat at 0 in steady state. |
| `truenas_iscsi_scrape_errors_total` | counter | — | Total exceptions during scrape (TrueNAS unreachable, K8s API issue, etc.) |
| `truenas_iscsi_last_scrape_success_timestamp_seconds` | gauge | — | Unix timestamp of last successful end-to-end scrape (used by `TrueNASISCSIScraperStale`) |
