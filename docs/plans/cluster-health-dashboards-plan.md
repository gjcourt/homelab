# Cluster Health Dashboards Plan

This document outlines a comprehensive plan for creating a detailed suite of Grafana dashboards specifically for monitoring the health of the `melodic-muse` cluster. The plan assumes the cluster will eventually scale to a 6-node configuration, with each node having identical hardware (except for 64GB of RAM per node).

## Objectives

1.  **Holistic View**: Provide a top-down view of the entire cluster's health, performance, and capacity.
2.  **Node-Level Granularity**: Allow drilling down into individual node metrics to identify bottlenecks or hardware issues.
3.  **Capacity Planning**: Track resource usage trends to forecast when additional nodes or storage will be required.
4.  **Proactive Alerting**: Configure alerts for critical cluster events (e.g., node down, high CPU/Memory, storage full).

## Dashboard Suite Structure

The suite will consist of several interconnected dashboards, allowing users to navigate from a high-level overview down to specific component details.

### 1. Cluster Overview Dashboard

This is the primary entry point for monitoring the cluster.

*   **Golden Signals**:
    *   Cluster Status (Up/Down).
    *   Total Nodes (Expected: 6, Current: X).
    *   Total Pods (Running vs. Pending/Failed).
    *   Overall CPU/Memory/Storage Utilization (Percentage and Absolute).
*   **Resource Allocation**:
    *   CPU Requests/Limits vs. Capacity.
    *   Memory Requests/Limits vs. Capacity.
*   **Top Consumers**:
    *   Top 5 Namespaces by CPU/Memory.
    *   Top 5 Pods by CPU/Memory.
*   **Alerts**:
    *   Active critical and warning alerts.

### 2. Node Details Dashboard

This dashboard provides deep visibility into individual nodes. It should include a variable to select the specific node (e.g., `node1` through `node6`).

*   **Hardware Metrics**:
    *   CPU Usage (per core and aggregate).
    *   Memory Usage (Used, Cached, Buffers, Free out of 64GB).
    *   Disk I/O (Read/Write IOPS, Throughput, Latency).
    *   Network I/O (Bytes In/Out, Errors, Drops per interface).
    *   Temperatures (CPU, Motherboard, Disks - if exposed via node-exporter).
*   **Kubernetes Metrics**:
    *   Kubelet Status.
    *   Number of Pods running on the node.
    *   Container Restarts on the node.
*   **System Metrics**:
    *   Load Average (1m, 5m, 15m).
    *   Uptime.
    *   Context Switches.

### 3. Storage & CSI Dashboard

This dashboard focuses on the storage layer, specifically the Synology iSCSI integration and NFS mounts.

*   **Synology CSI**:
    *   Total Provisioned Capacity vs. Used Capacity.
    *   Number of active iSCSI sessions/targets.
    *   CSI Driver Error Rate (e.g., failed attaches/detaches).
    *   iSCSI Latency (if available).
*   **Persistent Volumes**:
    *   Top 10 PVCs by usage.
    *   PVCs nearing capacity (e.g., > 85% full).
    *   Orphaned PVs/PVCs.
*   **NFS Mounts**:
    *   NFS Latency and Throughput (for media mounts like `/mnt/photos`, `/mnt/media`).

### 4. Networking & Gateway Dashboard

This dashboard monitors the Cilium CNI and Gateway API performance.

*   **Cilium Metrics**:
    *   Endpoint Status (Ready/Not Ready).
    *   Policy Drops/Forwards.
    *   IPAM Allocation (Available vs. Used IPs).
*   **Gateway API**:
    *   Total Ingress Traffic (Requests/sec, Bandwidth).
    *   HTTP Error Rates (4xx, 5xx) at the Gateway level.
    *   Gateway Latency (P95, P99).
    *   TLS Certificate Expirations (via cert-manager metrics).

### 5. Control Plane Dashboard

This dashboard monitors the health of the Kubernetes control plane components.

*   **API Server**:
    *   Request Latency (Read/Write).
    *   Request Rate by Verb (GET, POST, PUT).
    *   Error Rate (4xx, 5xx).
*   **etcd**:
    *   Leader Changes.
    *   Proposal Commit Latency.
    *   Database Size.
*   **Scheduler & Controller Manager**:
    *   Scheduling Latency.
    *   Workqueue Depth.

## Implementation Plan

1.  **Verify Exporters**: Ensure `node-exporter`, `kube-state-metrics`, and `cadvisor` are deployed and scraping correctly across all nodes.
2.  **Import Community Dashboards**: Start by importing well-regarded community dashboards (e.g., Kubernetes Mixin) as a baseline.
3.  **Customize for `melodic-muse`**:
    *   Adjust thresholds and limits to reflect the 64GB RAM per node configuration.
    *   Add specific panels for Synology iSCSI and NFS monitoring.
    *   Integrate Cilium and Gateway API metrics.
4.  **Configure Alerting**: Define Alertmanager rules for critical cluster events (e.g., NodeNotReady, HighCPUUsage, StorageNearingCapacity).
5.  **GitOps Integration**: Export the finalized dashboards as JSON and commit them to the repository to be managed by Flux.