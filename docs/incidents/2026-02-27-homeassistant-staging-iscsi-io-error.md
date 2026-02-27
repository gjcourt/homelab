# Incident: HomeAssistant Staging CrashLoopBackOff (iSCSI btrfs I/O Error)

**Date**: 2026-02-27  
**Severity**: Low — staging environment only; production unaffected  
**Status**: Resolved  

---

## Summary

HomeAssistant staging (`homeassistant-stage` namespace) entered a `CrashLoopBackOff` loop (1056+ restarts over ~6 days). The underlying iSCSI volume (`homeassistant-config-pvc`) had an I/O error causing the container filesystem to fail during startup. HomeAssistant exited with code 1 immediately on launch due to being unable to read the `/config` directory.

---

## Timeline

| Time (approx.) | Event |
|---|---|
| 2026-02-21 | HomeAssistant staging last successful start (per pod `lastState.terminated`) |
| 2026-02-21 | `homeassistant-7b9bb5b599-hkxf2` enters crash loop |
| 2026-02-27 | User reports: "homeassistant stage still crashing" |
| 2026-02-27 08:30 | Investigation: exit code 143 (SIGTERM), container terminates in ~5 seconds |
| 2026-02-27 08:35 | Logs show `OSError: [Errno 5] I/O error: '/config'` |
| 2026-02-27 08:40 | Fix: scale deployment to 0, wait 30s, scale back to 1 |
| 2026-02-27 08:41 | New pod `homeassistant-7b9bb5b599-zkwxg` started and reached `1/1 Running` |

---

## Root Cause

The iSCSI volume backing the `homeassistant-config-pvc` PersistentVolume experienced an I/O error, which caused the btrfs filesystem to remount read-only (or go into an error state). When HomeAssistant attempted to read `/config` during startup, Python threw:

```
OSError: [Errno 5] I/O error: '/config'
```

This is the same failure class as documented in [2026-02-08-pv-recovery.md](2026-02-08-pv-recovery.md). The underlying cause is iSCSI session instability on the Synology-backed storage. The HomeAssistant container itself is healthy; the issue is entirely at the storage layer.

The crash loop self-perpetuated: Kubernetes kept restarting the container but the same broken mount was reused without a fresh iSCSI session negotiation. Kubernetes does not automatically re-attach iSCSI volumes between pod restarts when the pod lands on the same node.

---

## Detection

The issue was reported by the user approximately 6 days after it began. There were no alerting triggers because:
- The staging environment has no uptime monitoring
- CrashLoopBackOff does not fire a PagerDuty/Alertmanager alert in the current config

---

## Impact

- **HomeAssistant staging**: completely unavailable for ~6 days
- **HomeAssistant production**: unaffected (separate PVC/node attachment)
- **Users**: no direct user impact (staging is a non-user-facing dev environment)

---

## Fix

Scaling the deployment to 0 forces Kubernetes to detach the iSCSI volume from the node. Scaling back to 1 triggers a fresh attach and mount, which recovers the filesystem:

```bash
# Scale down (forces PVC detach)
kubectl scale deployment/homeassistant -n homeassistant-stage --replicas=0

# Wait for pod termination and iSCSI session teardown
sleep 30

# Scale back up (forces fresh PVC attach+mount)
kubectl scale deployment/homeassistant -n homeassistant-stage --replicas=1

# Verify recovery
kubectl get pods -n homeassistant-stage
```

**No data loss occurred.** The btrfs remount-to-ro is a protective measure — writes are blocked but existing data is intact.

---

## Lessons Learned

1. **This pattern keeps recurring**: HomeAssistant prod (Feb 8), HomeAssistant staging (Feb 21–27), memos prod (Feb 27). The Synology iSCSI sessions are periodically going into an error state causing btrfs protective remount.

2. **Staging needs basic pod-restart alerting**. A CrashLoopBackOff that persists for 6 days without detection indicates a monitoring gap. Consider adding an Alertmanager rule for `kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}`.

3. **The scale-down/up workaround is reliable** but it is manual. If this continues, consider a CronJob or automated runbook that detects CrashLoopBackOff + btrfs read-only and auto-scales to recover.

---

## References

- [synology-iscsi-operations.md](../guides/synology-iscsi-operations.md)
- [synology-iscsi-cleanup.md](../guides/synology-iscsi-cleanup.md)
- [2026-02-08-pv-recovery.md](2026-02-08-pv-recovery.md)
- [2026-02-15-iscsi-targets-disabled.md](2026-02-15-iscsi-targets-disabled.md)
