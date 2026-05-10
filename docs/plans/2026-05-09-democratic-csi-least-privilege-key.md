---
status: planned
last_modified: 2026-05-09
---

# Democratic-CSI: Migrate from `truenas_admin` to a Least-Privilege Dedicated User

## Context

The democratic-csi driver currently authenticates to TrueNAS at hestia
(`10.42.2.10`) using an API key bound to **`truenas_admin`** (uid 950,
`FULL_ADMIN`, allowlist `[{method: *, resource: *}]`). Verified live
during the 2026-05-09 work on the truenas-iscsi-monitor exporter.

That gives the driver controller pod admin authority over the entire
NAS — far beyond what it needs to manage iSCSI volumes. The realistic
threat model is **supply-chain compromise** of the democratic-csi chart
or container image: today, that grants an attacker the ability to
rotate API keys, install certificates, restart any service, modify
users, run commands via SSH service control, and modify boot
environments. With a scoped key it grants only "wipe iSCSI volumes" —
still bad, but bounded to the driver's stated job.

The companion `truenas-iscsi-monitor` already follows this pattern: a
dedicated user (`melodic-muse-cluster`, uid 3000, `READONLY_ADMIN` with
755 read-only methods on its allowlist), separate API key, SOPS-encrypted
secret. This plan extends the pattern to the driver, finishing the
two-key separation.

The work is real because TrueNAS Scale ships no "CSI admin" preset
role — we assemble one from primitives, then **soak it** behind a
test StorageClass for a week to catch any silently-missing role
before flipping production.

## Decisions made

- **Same SOPS-secret pattern** as `truenas-iscsi-monitor`: dedicated
  user, dedicated API key, separate secret file at the existing path
  `infra/controllers/democratic-csi/secret-truenas-api-key.yaml`. No
  new top-level architecture; just a different value behind the same
  reference.
- **Don't delete the old key — disable it.** Cutover risk is real;
  rollback should be one click in the TrueNAS UI. Deletion happens
  only after a week of clean operation.
- **Validate via a parallel StorageClass first.** Don't switch the
  production StorageClass before confirming the new key handles the
  full PV/PVC lifecycle (create / mount / snapshot / expand / delete).

## Starter role set for the new user

Built from the FULL_ADMIN allowlist, intersected with what the
democratic-csi driver actually calls (extracted from the existing
`infra/controllers/democratic-csi/truenas-api-proxy.yaml` JSON-RPC
mapping plus upstream chart docs).

| Role | Why | Required for |
|---|---|---|
| `SHARING_ADMIN` | Bundles iSCSI extent / target / targetextent / portal / auth / initiator write | All CSI iSCSI ops |
| `DATASET_WRITE` | Create + update zvols | PVC create + expand |
| `DATASET_DELETE` | Delete zvols on PVC removal | PVC delete |
| `SNAPSHOT_WRITE` | Take zfs snapshots | VolumeSnapshot create |
| `SNAPSHOT_DELETE` | Clean up snapshots | VolumeSnapshot delete |
| `POOL_READ` | Pool capacity / config introspection | All ops (preflight) |
| `SHARING_ISCSI_GLOBAL_READ` | Read iSCSI global config | All ops |
| `SERVICE_READ` | Confirm iSCSI service running | Driver startup checks |

**Explicitly excluded** (FULL_ADMIN has these; we don't grant them):
- `API_KEY_WRITE` — would let a compromised driver mint new admin keys
- `SYSTEM_*_WRITE`, `SYSTEM_UPDATE_*`, `BOOT_ENV_*` — system control
- `ACCOUNT_WRITE`, `PRIVILEGE_WRITE` — user/role manipulation
- `SSH_*`, `CERTIFICATE_WRITE` — lateral movement primitives
- `KMIP_*` — key escrow / encryption admin
- `POOL_WRITE`, `DISK_WRITE` — pool/disk-level operations CSI doesn't do
- `NETWORK_*_WRITE` — network config

The plan is correct if every CSI operation the cluster runs in steady
state succeeds with these roles, **and nothing else** in the FULL_ADMIN
list is missed. Phase B's test PVC catches missing-role failures
explicitly.

---

## Phase A — Provision the new user and key

Read-only on the cluster side. No production impact.

### A.1 Create user in TrueNAS UI

`Settings → Local Users → Add`:

- Username: `melodic-muse-csi`
- UID: `3001`
- Password: random; not used (auth is via API key)
- Disable shell access; set `nologin`
- Role list: per "Starter role set" above

### A.2 Create API key

`Settings → API Keys → Add`:

- Name: `melodic-muse-csi`
- User: `melodic-muse-csi` (from A.1)
- Save the token; copy out-of-band.

### A.3 Verify the key out-of-band

Mirror the JSON-RPC probe used during the truenas-iscsi-monitor work:

```bash
NEW_KEY="..."  # paste the token from A.2
NEW_KEY="$NEW_KEY" python3 <<'PY'
import os, ssl, json
from websockets.sync.client import connect
ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
i = [0]
def call(ws, m, p=None):
    i[0] += 1
    ws.send(json.dumps({"jsonrpc":"2.0","id":i[0],"method":m,"params":p or []}))
    return json.loads(ws.recv())

with connect("wss://10.42.2.10/api/current", ssl_context=ctx) as ws:
    auth = call(ws, "auth.login_with_api_key", [os.environ["NEW_KEY"]])
    me = call(ws, "auth.me")
    print("auth:", auth.get("result"))
    print("user:", me["result"]["pw_name"])
    roles = me["result"]["privilege"]["roles"]["$set"]
    for r in ("SHARING_ADMIN","DATASET_WRITE","DATASET_DELETE","SNAPSHOT_WRITE","SNAPSHOT_DELETE","POOL_READ","FULL_ADMIN","API_KEY_WRITE"):
        print(f"  {'+' if r in roles else '-'} {r}")
    # Read tests — must succeed
    print("query iscsi.extent:", "OK" if "result" in call(ws, "iscsi.extent.query", [[]]) else "FAIL")
    print("query pool.dataset:", "OK" if "result" in call(ws, "pool.dataset.query", [[["type","=","VOLUME"]]]) else "FAIL")
    # Negative test — must FAIL with permission denied
    r = call(ws, "api_key.create", [{"name":"probe","user_id":1}])
    print("api_key.create (must deny):", r.get("error",{}).get("message","UNEXPECTED SUCCESS")[:80])
PY
```

### A.4 GO criteria

- `auth.login_with_api_key` returns `True`.
- `me.privilege.roles` contains `SHARING_ADMIN`, `DATASET_WRITE`, `DATASET_DELETE`, `SNAPSHOT_WRITE`, `POOL_READ`. **Does not** contain `FULL_ADMIN` or `API_KEY_WRITE`.
- Read methods (`iscsi.extent.query`, `pool.dataset.query`) succeed.
- `api_key.create` is **denied** (permission error, not "invalid params").

### A.5 Rollback

Delete the user + key in the TrueNAS UI. No cluster state changed.

---

## Phase B — Validate end-to-end via a parallel StorageClass

Cluster work, but isolated to a new StorageClass and a throwaway PVC.
The production StorageClass and existing PVCs are untouched.

### B.1 Stage a parallel StorageClass + driver instance

Two sub-options; pick whichever fits the existing democratic-csi
deployment shape.

**Option B.1.a — Second helm release, separate namespace.** Install a
second copy of the democratic-csi chart (e.g. `democratic-csi-test`)
in its own namespace, pointed at the new API key. Provision a new
StorageClass `truenas-iscsi-csikey-test` referencing this driver
instance only.

**Option B.1.b — Reuse existing controller, swap key behind a feature
flag.** The existing chart's controller container reloads on secret
change. Stage the new secret name behind a one-off Deployment patch,
swap the env reference, redeploy controller only.

**Recommended:** B.1.a. It keeps production untouched; rollback is
"delete the test namespace."

The new key's secret lives at:
- `infra/controllers/democratic-csi-test/secret-truenas-api-key.yaml`
  (SOPS-encrypted via the existing `.sops.yaml` config)
- Template: copy `infra/controllers/democratic-csi/secret-truenas-api-key.yaml` shape.

### B.2 Provision a throwaway PVC

```yaml
# tests/csi-key-test-pvc.yaml (committed in this PR for repeatability)
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: csi-key-test-pvc
  namespace: default
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: truenas-iscsi-csikey-test
  resources:
    requests:
      storage: 1Gi
```

### B.3 Run the lifecycle test

```bash
kubectl apply -f tests/csi-key-test-pvc.yaml
kubectl wait --for=condition=Bound pvc/csi-key-test-pvc -n default --timeout=120s
# Confirms: pool.dataset.create + iscsi.extent.create + iscsi.target.create + iscsi.targetextent.create

kubectl run csi-key-test --image=busybox --restart=Never -n default \
  --overrides='{"spec":{"containers":[{"name":"csi-key-test","image":"busybox","command":["sh","-c","echo hello > /data/probe && sleep 30"],"volumeMounts":[{"mountPath":"/data","name":"v"}]}],"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"csi-key-test-pvc"}}]}}'
kubectl wait --for=condition=Ready pod/csi-key-test -n default --timeout=120s
# Confirms: PV mounts, volume is writable

# Snapshot test (only if VolumeSnapshotClass exists for this driver)
# kubectl apply -f tests/csi-key-test-snapshot.yaml
# kubectl wait --for=jsonpath='{.status.readyToUse}'=true volumesnapshot/csi-key-test-snapshot -n default --timeout=120s
# Confirms: pool.snapshot.create

kubectl delete pod csi-key-test -n default
kubectl delete pvc csi-key-test-pvc -n default
# Confirms: pool.dataset.delete + iscsi.extent.delete + iscsi.target.delete

# Verify the extent is actually gone on TrueNAS:
ssh truenas_admin@10.42.2.10 'midclt call iscsi.extent.query "[]"' \
  | jq '.[].name' | grep csi-key-test
# Expected: empty (extent successfully deleted)
```

### B.4 Watch for silent failures

`kubectl logs -n democratic-csi-test deploy/...controller... -f` during
each lifecycle phase. Any `permission denied` / `not authorized` /
`role required` error → a missing role; add it to the user, retry.
Common offenders likely needing addition (have ready, don't add
preemptively):

- `SHARING_ISCSI_AUTH_WRITE` (if CHAP is enabled)
- `SHARING_ISCSI_INITIATOR_WRITE` (if initiator restrictions exist)
- `FILESYSTEM_DATA_WRITE` (some specific operations)

### B.5 GO criteria

- All four lifecycle ops complete: PVC bind, pod mount, snapshot create
  (if snapshots enabled), PVC delete.
- Driver controller logs show no `permission denied` / `role required` errors.
- TrueNAS extent + zvol fully removed after PVC delete (no orphans).

### B.6 Rollback

Delete the test namespace + StorageClass. Original controller untouched.

---

## Phase C — Cut over the production driver

After Phase B passes. Production reconciliation: ~30 min downtime
on CSI provisioning operations during the swap (existing mounted PVs
keep working; what pauses is *new* PVC creation and PV deletion).

### C.1 Plan a maintenance window

- No PVC creation / deletion in flight.
- Optionally `kubectl cordon` the node holding the controller pod so
  scheduling pauses.

### C.2 Swap the secret

Update `infra/controllers/democratic-csi/secret-truenas-api-key.yaml`
in-place: the file path stays the same, the SOPS-encrypted value swaps
to the new key.

```bash
# Operator-only; mirror the truenas-iscsi-monitor secret rotation:
# 1. Decrypt to a temp file
# 2. Replace the api_key value with the new token from Phase A.2
# 3. sops -e -i to re-encrypt
# 4. Verify by re-decrypting
# 5. Commit
```

> **SOPS edits are operator-only.** A draft PR with the file
> placeholder + a `.example` template is the established pattern;
> CI breakage is the gate that confirms the operator has filled it
> in.

### C.3 Roll the democratic-csi controller

```bash
flux reconcile kustomization apps-production -n flux-system --with-source
kubectl rollout restart deployment -n democratic-csi <controller-name>
kubectl rollout status deployment -n democratic-csi <controller-name> --timeout=180s
```

### C.4 Smoke test in production

Same lifecycle test from B.3 against the production StorageClass:

```bash
kubectl apply -f tests/csi-key-test-pvc.yaml  # use real prod SC
kubectl wait --for=condition=Bound pvc/csi-key-test-pvc -n default --timeout=120s
kubectl delete pvc csi-key-test-pvc -n default
```

### C.5 GO criteria

- Controller pod reaches Ready with the new secret.
- Lifecycle smoke test (C.4) passes against the production StorageClass.
- No `permission denied` errors in driver logs.

### C.6 Rollback

Two options, in order of escalation:

1. **Re-enable the old `truenas_admin` key** in the TrueNAS UI
   (don't delete it during cutover — see C.7); revert the SOPS
   secret commit; reconcile; restart controller.
2. If you have to roll for several hours, escalate to a separate
   incident postmortem; `truenas_admin`-key operations would have
   resumed on rollback.

### C.7 Soak — 7 days, then retire the old key

Watch for:

- Any `permission denied` from driver logs over the soak window.
- Any failed CSI operation (CNPG snapshot, PVC expand, pod re-mount
  during rolling restart) — these touch less-common API methods that
  the lifecycle test may not have covered.

If clean for 7 days: **disable** (don't delete) the `truenas_admin`
API key in the TrueNAS UI. Wait another 7 days. If still clean:
delete it.

---

## Phase D (optional) — Document and codify

After successful cutover and 14-day clean soak:

- Update `docs/architecture/networking/cluster-load-balancing.md`
  references to "TrueNAS API key" to note the two-key model.
- Add a runbook at `docs/operations/apps/democratic-csi.md`
  documenting:
  - Required role list (current truth, in case TrueNAS adds new roles)
  - Key rotation procedure (mirror truenas-iscsi-monitor's pattern)
  - "If a role is missing, error looks like X; add Y to the user"
- Reference the same pattern in `docs/architecture/networking/README.md`'s
  threat-model section: two distinct least-privilege users, role-set
  documented, blast radius bounded.

---

## Verification matrix

| Test | After Phase A | After Phase B | After Phase C |
|---|---|---|---|
| New user exists with documented role set | ✓ | ✓ | ✓ |
| New API key authenticates | ✓ | ✓ | ✓ |
| `api_key.create` denied (negative control) | ✓ | ✓ | ✓ |
| Throwaway PVC binds via parallel SC | — | ✓ | n/a |
| Throwaway PVC unbinds cleanly | — | ✓ | n/a |
| Production controller reaches Ready with new key | — | — | ✓ |
| Production smoke-test PVC binds + unbinds | — | — | ✓ |
| `truenas_admin` API key disabled | — | — | (after soak) |

## Out of scope

- **Migrating existing PVs to the new key** — they don't need to be
  re-provisioned; the driver only uses the key for new operations.
  Existing iSCSI sessions are independent.
- **Dataset-level ACLs / quotas** — a TrueNAS user with `DATASET_WRITE`
  can write to any dataset by default. If you want pool/dataset
  isolation (e.g., the CSI user can only write under `main/k8s/`),
  that's a separate ACL plan.
- **Splitting the monitor and CSI users by zpool** — both would need
  to be scoped per pool if you ever introduce a second zpool. Not yet.
- **TrueNAS RBAC for the truenas-api-proxy** — the proxy currently
  reuses the driver key. If we later pull metrics from the proxy
  endpoint instead of bypassing it, this plan would need an extension.

## Sources / references

- TrueNAS 26.x role list: dump from `auth.me` against `truenas_admin`
  on hestia, captured 2026-05-09.
- democratic-csi method coverage: `infra/controllers/democratic-csi/truenas-api-proxy.yaml`
  REST→JSON-RPC mappings.
- Companion least-privilege precedent: `apps/base/truenas-iscsi-monitor/`
  + the `melodic-muse-cluster` user (READONLY_ADMIN).
- SOPS pattern: `infra/controllers/democratic-csi/secret-truenas-api-key.yaml`
  + `apps/production/truenas-iscsi-monitor/secret.yaml` (already in tree).
