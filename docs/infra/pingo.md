# Pingo

Pingo is a background job that runs as a DaemonSet to update DNS records for `vpn.burnbytes.com`.

## Architecture

- **Deployment Type**: DaemonSet
- **Namespace**: `pingo`
- **Image**: `ghcr.io/gjcourt/pingo`

## Configuration

Pingo requires a secret to authenticate with the DNS provider. The secret is managed via SOPS and should contain the necessary API tokens and domain configuration.

### Secret Structure

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: pingo-secret
  namespace: pingo
type: Opaque
stringData:
  CLOUDFLARE_API_TOKEN: "your-api-token"
  DOMAIN: "vpn.burnbytes.com"
```

## Operations

### Viewing Logs

To view the logs for the Pingo DaemonSet:

```bash
kubectl logs -n pingo -l app.kubernetes.io/name=pingo
```

### Restarting

To restart the Pingo DaemonSet:

```bash
kubectl rollout restart daemonset pingo -n pingo
```
