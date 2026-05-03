# Cert-Manager

## 1. Overview
Cert-Manager is a Kubernetes add-on that automates the management and issuance of TLS certificates from various issuing sources. In this homelab, it is primarily used to provision Let's Encrypt certificates via DNS-01 challenges using Cloudflare.

## 2. Architecture
Cert-Manager is deployed in the `security` namespace via Flux using the official Helm chart.
- **ClusterIssuer**: A single `ClusterIssuer` named `letsencrypt-production` is configured to handle all certificate requests for the `burntbytes.com` domain.
- **DNS-01 Challenge**: It uses the Cloudflare API to automatically create and delete TXT records to prove domain ownership, allowing for wildcard certificates and internal-only services to receive valid TLS certificates without exposing them to the internet.

## 3. URLs
- **Documentation**: https://cert-manager.io/docs/

## 4. Configuration
- **Helm Values**: Located in `infra/controllers/cert-manager/values.yaml`.
- **ClusterIssuer**: Defined in `infra/configs/cert-manager-issuers/clusterissuer.yaml`.
  - Email: `skills.13freaky@icloud.com`
  - Server: `https://acme-v02.api.letsencrypt.org/directory`
- **Secrets**:
  - `cloudflare-api-token`: A SOPS-encrypted secret containing the Cloudflare API token with DNS edit permissions for the `burntbytes.com` zone.

## 5. Usage Instructions
To request a certificate, create a `Certificate` resource or add the appropriate annotations to an `Ingress` or `Gateway` resource.

Example `Certificate`:
```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: example-cert
  namespace: default
spec:
  secretName: example-tls
  issuerRef:
    name: letsencrypt-production
    kind: ClusterIssuer
  dnsNames:
    - example.burntbytes.com
```

## 6. Testing
To verify Cert-Manager is working correctly:
```bash
kubectl get pods -n security -l app.kubernetes.io/instance=cert-manager
kubectl get clusterissuer letsencrypt-production -o wide
```
The `ClusterIssuer` should show a `Ready` status of `True`.

## 7. Monitoring & Alerting
- **Metrics**: Cert-Manager exposes Prometheus metrics. Key metrics include `certmanager_certificate_expiration_timestamp_seconds` to monitor expiring certificates.
- **Logs**: Check controller logs using `kubectl logs -n security deploy/cert-manager`.

## 8. Disaster Recovery
- **Backup Strategy**: The `cloudflare-api-token` secret is stored in Git (encrypted via SOPS). The Let's Encrypt account private key (`letsencrypt-production-issuer-account-key`) is generated automatically if lost, but backing it up can prevent rate-limiting issues during a full cluster rebuild.
- **Restore Procedure**: Re-apply the Flux Kustomization. Cert-Manager will automatically re-issue any missing certificates based on the `Certificate` resources in the cluster.

## 9. Troubleshooting
- **Certificate stuck in Pending**:
  - Check the `CertificateRequest` and `Order` resources: `kubectl describe certificaterequest <name>`
  - Check the `Challenge` resource: `kubectl describe challenge <name>`
  - Verify the Cloudflare API token is valid and has the correct permissions.
  - Ensure the DNS TXT record is propagating correctly.
- **Webhook errors**: If you see errors related to the cert-manager webhook, ensure the webhook pod is running and reachable by the Kubernetes API server.
