# Pingo

Pingo is a background job that runs as a CronJob to update DNS records for `vpn.burntbytes.com`.

## Architecture

- **Deployment Type**: CronJob (runs every 5 minutes)
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
  DOMAINS: "vpn.burntbytes.com"
```

## Operations

### Viewing Logs

To view the logs for the latest Pingo job:

```bash
kubectl logs -n pingo -l app.kubernetes.io/name=pingo
```

### Triggering a Manual Run

To trigger a manual run of the Pingo CronJob:

```bash
kubectl create job --from=cronjob/pingo pingo-manual-run -n pingo
```
