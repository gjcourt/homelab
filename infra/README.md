# Homelab Infra

Infrastructure components managed by Flux. These run cluster-wide and are shared
across staging and production environments.

## Controllers (`infra/controllers/`)

| Component | Purpose |
|-----------|---------|
| [cert-manager](controllers/cert-manager/) | TLS certificate automation (Let's Encrypt) |
| [Cilium](controllers/cilium/) | CNI, Gateway API, LB IPAM, network policies |
| [CNPG](controllers/cnpg/) | CloudNativePG operator for PostgreSQL clusters |
| [kube-prometheus-stack](controllers/kube-prometheus-stack/) | Monitoring (Prometheus + Grafana) |
| [Loki](controllers/loki/) | Log aggregation |
| [Promtail](controllers/promtail/) | Log shipping to Loki |
| [Mosquitto](controllers/mosquitto/) | MQTT broker |
| [Renovate](controllers/renovate/) | Automated dependency updates |
| [Snapshot controller](controllers/snapshot/) | Volume snapshot support |
| [Synology CSI](controllers/synology-csi/) | iSCSI storage provisioner |
| [Zigbee2MQTT](controllers/zigbee2mqtt/) | Zigbee device integration |

## Configs (`infra/configs/`)

| Component | Purpose |
|-----------|---------|
| [cert-manager-issuers](configs/cert-manager-issuers/) | ClusterIssuer definitions |
| [certificates](configs/certificates/) | Shared TLS certificates |
| [Cilium](configs/cilium/) | LB IPAM pools, BGP, L2 announcements |
| [gateway](configs/gateway/) | Gateway API route configuration |