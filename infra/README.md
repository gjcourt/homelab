# Homelab Infra

Cluster-level controllers and configuration managed by Flux.

## Controllers (`infra/controllers/`)

| Component | Description |
|-----------|-------------|
| cert-manager | TLS certificate automation |
| cilium | CNI + Gateway API + LB IPAM |
| cnpg | CloudNativePG operator (PostgreSQL) |
| kube-prometheus-stack | Prometheus + Grafana monitoring |
| loki | Log aggregation |
| promtail | Log shipping to Loki |
| mosquitto | MQTT broker |
| renovate | Dependency update bot |
| snapshot | Volume snapshot controller |
| synology-csi | Synology iSCSI CSI driver |
| zigbee2mqtt | Zigbee bridge |

## Configs (`infra/configs/`)

| Component | Description |
|-----------|-------------|
| cert-manager-issuers | Let's Encrypt ClusterIssuers |
| certificates | Shared TLS certificates |
| cilium | Cilium LB IP pools and config |
| gateway | Gateway API resources |