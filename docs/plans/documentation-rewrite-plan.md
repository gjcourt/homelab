# Documentation Rewrite Plan

This document outlines a comprehensive plan for rewriting the entire suite of documentation for the homelab repository. The goal is to provide detailed, consistent, and actionable instructions for every application and infrastructure component.

## Objectives

1.  **Consistency**: Establish a standard template for all application and infrastructure documentation.
2.  **Completeness**: Ensure every component has documentation covering usage, configuration, testing, disaster recovery, and monitoring.
3.  **Accessibility**: Make it easy for anyone (or future self) to understand how to operate and maintain the cluster.

## Standard Template

Every application document (e.g., `docs/apps/<app-name>.md`) should follow this structure:

1.  **Overview**: Brief description of the application and its purpose in the homelab.
2.  **Architecture**: How it's deployed (StatefulSet, Deployment), dependencies (Postgres, Redis), and storage (PVCs, NFS).
3.  **URLs**: Links to staging and production instances.
4.  **Configuration**:
    *   **Environment Variables**: List of key environment variables and their purposes.
    *   **Command Line Options**: Any specific command-line arguments used in the deployment.
    *   **ConfigMaps/Secrets**: Details on how configuration files and secrets are managed (e.g., SOPS).
5.  **Usage Instructions**: How to interact with the application (UI, API, CLI).
6.  **Testing**: How to verify the application is working correctly (e.g., health checks, manual tests).
7.  **Monitoring & Alerting**:
    *   Key metrics to watch (Prometheus).
    *   Log locations and queries (Loki).
    *   Configured alerts (Alertmanager).
8.  **Disaster Recovery**:
    *   **Backup Strategy**: How data is backed up (e.g., CNPG Barman, Velero, manual scripts).
    *   **Restore Procedure**: Step-by-step instructions to restore from a backup.
9.  **Troubleshooting**: Common issues and their resolutions.

## Execution Plan

### Phase 1: Infrastructure Documentation

Rewrite documentation for core infrastructure components:

*   [x] `docs/infra/flux.md`: Detailed guide on Flux CD, reconciliation, and troubleshooting.
*   [x] `docs/infra/cilium.md`: Cilium CNI, Gateway API, and LoadBalancer IPAM.
*   [x] `docs/infra/cert-manager.md`: Certificate management and Cloudflare DNS-01 challenges.
*   [x] `docs/infra/storage.md`: Synology CSI driver, NFS mounts, and iSCSI operations.
*   [x] `docs/infra/monitoring.md`: Kube-Prometheus-Stack, Loki, Promtail, and Grafana.

### Phase 2: Core Applications Documentation

Rewrite documentation for essential applications:

*   [ ] `docs/apps/authelia.md`: Expand existing docs with DR and monitoring.
*   [ ] `docs/apps/homepage.md`: Configuration, widget setup, and troubleshooting.
*   [ ] `docs/apps/adguard.md`: DNS configuration, HA setup, and sync jobs.

### Phase 3: Media & Data Applications Documentation

Rewrite documentation for media and data-heavy applications:

*   [ ] `docs/apps/immich.md`: Machine learning, hardware acceleration, Postgres vector DB, and NFS storage.
*   [ ] `docs/apps/jellyfin.md`: Hardware transcoding and media mounts.
*   [ ] `docs/apps/navidrome.md`: Expand existing docs with DR and monitoring.
*   [ ] `docs/apps/audiobookshelf.md`: Storage and backup procedures.
*   [ ] `docs/apps/snapcast.md`: Expand existing docs with DR and monitoring.

### Phase 4: Utility Applications Documentation

Rewrite documentation for utility applications:

*   [ ] `docs/apps/memos.md`: Database backups and SSO integration.
*   [ ] `docs/apps/linkding.md`: Database backups and SSO integration.
*   [ ] `docs/apps/mealie.md`: Database backups and SSO integration.
*   [ ] `docs/apps/golinks.md`: Database backups and SSO integration.
*   [ ] `docs/apps/excalidraw.md`: Usage and configuration.
*   [ ] `docs/apps/vitals.md`: Database backups and SSO integration.

## Review and Maintenance

*   After each phase, review the documentation against the standard template.
*   Establish a process to update documentation whenever a significant change is made to an application or infrastructure component.