# Homelab Infra

Cluster-level controllers and configuration managed by Flux.

## Controllers (`infra/controllers/`)

| Component | Description |
|-----------|-------------|
| barman-cloud | CNPG Barman Cloud plugin (S3 WAL/backup) |
| cert-manager | TLS certificate automation |
| cilium | CNI + Gateway API + LB IPAM |
| cnpg | CloudNativePG operator (PostgreSQL) |
| democratic-csi | TrueNAS iSCSI CSI driver |
| kube-prometheus-stack | Prometheus + Grafana monitoring |
| loki | Log aggregation |
| promtail | Log shipping to Loki |
| mosquitto | MQTT broker |
| pingo | DNS updater for vpn.burnbytes.com |
| renovate | Dependency update bot |
| snapshot | Volume snapshot controller |
| zigbee2mqtt | Zigbee bridge |

## Configs (`infra/configs/`)

| Component | Description |
|-----------|-------------|
| cert-manager-issuers | Let's Encrypt ClusterIssuers |
| certificates | Shared TLS certificates |
| cilium | Cilium LB IP pools and config |
| gateway | Gateway API resources |