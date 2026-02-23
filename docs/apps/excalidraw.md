# Excalidraw

## 1. Overview
Excalidraw is a virtual collaborative whiteboard tool that lets you easily sketch diagrams that have a hand-drawn feel to them. It is deployed as a stateless frontend application in the homelab.

## 2. Architecture
Excalidraw is deployed as a Kubernetes `Deployment` with a single replica in the `excalidraw-prod` (and `excalidraw-stage`) namespace.
- **Database**: None. Excalidraw is entirely stateless and runs in the browser.
- **Storage**: None. Drawings are saved locally in the user's browser or can be exported as files.
- **Networking**: Exposed via Cilium Gateway API (`HTTPRoute`).

## 3. URLs
- **Staging**: https://excalidraw.stage.burntbytes.com
- **Production**: https://excalidraw.burntbytes.com

## 4. Configuration
- **Environment Variables**: None.
- **ConfigMaps/Secrets**: None.

## 5. Usage Instructions
- Navigate to the Excalidraw URL.
- Start drawing on the whiteboard.
- To save your work, use the export options (e.g., export to PNG, SVG, or Excalidraw file) or rely on your browser's local storage.

## 6. Testing
To verify Excalidraw is working:
1. Navigate to the Excalidraw URL and ensure the whiteboard loads.
2. Draw a simple shape.
3. Verify the pod is running: `kubectl get pods -n excalidraw-prod`

## 7. Monitoring & Alerting
- **Metrics**: Excalidraw does not expose Prometheus metrics natively.
- **Logs**: Check the pod logs for web server errors:
  ```bash
  kubectl logs -n excalidraw-prod deploy/excalidraw
  ```

## 8. Disaster Recovery
- **Backup Strategy**: None required. The application is stateless. Users are responsible for exporting and saving their own drawings.
- **Restore Procedure**: Re-deploy the Excalidraw manifests.

## 9. Troubleshooting
- **Whiteboard Not Loading**:
  - Verify the pod is running and healthy.
  - Check the Gateway API configuration and ensure the `HTTPRoute` is correctly routing traffic to the service.
