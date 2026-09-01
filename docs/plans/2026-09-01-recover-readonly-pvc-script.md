---
status: planned
last_modified: 2026-09-01
summary: "implementation plan for scripts/recover-readonly-pvc.sh — the Tier 1 deliverable from the read-only PVC recovery plan; encodes the AGENTS.md runbook with a trap so Flux always resumes, gates on volumeattachment rather than pod count, detects the owning Kustomization instead of hardcoding apps-production, and refuses CNPG outright"
---

# Plan: `scripts/recover-readonly-pvc.sh`

Implementation detail for **Tier 1** of
[2026-09-01-readonly-pvc-recovery.md](2026-09-01-readonly-pvc-recovery.md),
which argues *why* this should be a script and not a controller. This document
is *how*.

## The problem it solves

`AGENTS.md` § *Recovering read-only iSCSI volumes* documents a seven-step
recovery. Three of those steps have already cost real time, and a fourth is
wrong. The script exists to make those four unable to recur — **not to save
typing.**

### ⚠️ The runbook hardcodes the wrong Kustomization

```bash
flux suspend kustomization apps-production -n flux-system
```

Verified 2026-09-01:

```
zigbee2mqtt  ->  kustomize.toolkit.fluxcd.io/name: infra-controllers
golinks-prod ->  kustomize.toolkit.fluxcd.io/name: apps-production
```

**zigbee2mqtt is owned by `infra-controllers`.** It is also the workload in
[#1080](https://github.com/gjcourt/homelab/issues/1080) — so the documented
recovery, followed literally, suspends a Kustomization that does not own the
thing being recovered, and Flux reverts the scale-to-0 anyway. The operator sees
the workload scale back up on its own and concludes the fix failed.

Anything under `infra/controllers/` or `infra/configs/` has this problem:
zigbee2mqtt, mosquitto, the monitoring stack, pvc-writeprobe itself.

**The script must derive the owner from the resource's own labels, never assume.**

### The three known traps

1. **`rollout restart` is not sufficient on RWO.** It creates the replacement pod
   before terminating the old one, so the new pod attaches to the same errored
   device and comes up read-only again.
2. **Pod termination is not proof the device was released.** `volumeattachment`
   reaching zero is. Scale up too early and you re-attach to the errored device —
   which looks identical to the fix not working.
3. **`wc -l` on BSD/macOS emits leading whitespace**, so a string comparison
   against `"0"` never matches and the wait loop spins forever — **with
   production reconciliation suspended.** The worst outcome in the procedure, and
   it is a shell quirk, not a Kubernetes one.

## Scope

**In scope:** Deployments and StatefulSets on RWO iSCSI whose PVC has gone
read-only, in any namespace, under any Flux Kustomization.

**Out of scope, deliberately:**

- **CNPG clusters.** Refuses and prints the runbook section. Deleting a primary
  before confirming replicas are unready triggers an unplanned switchover; that
  needs judgement.
- **Deleting PVCs.** Never, under any flag. The CNPG-replica path in the runbook
  sometimes requires it; that stays manual.
- **Deciding *whether* to recover.** The operator runs it, on a named workload.
  The script does not watch, poll, or self-trigger.
- **Mass recovery.** If many PVCs are unwritable it warns and asks for
  confirmation — that is an infrastructure event and wants a human, per the
  parent plan.

## CLI surface

```
scripts/recover-readonly-pvc.sh [options] <namespace> <workload>

  <workload>            deploy/<name> | statefulset/<name> | <name> (auto-detect)

  --dry-run             print the plan; touch nothing
  --yes                 skip the confirmation prompt (for a known-good rerun)
  --timeout <seconds>   per-wait-stage timeout (default 300)
  --pvc <name>          restrict to one PVC when the workload mounts several
  -h, --help
```

**Exit codes** — meaningful, because this will end up in a runbook one-liner:

| Code | Meaning |
| ---: | --- |
| 0 | Recovered and verified writable |
| 1 | Usage error |
| 2 | Refused: CNPG-managed workload |
| 3 | Refused: operator declined at the confirmation prompt |
| 4 | Timed out waiting for pods to terminate |
| 5 | Timed out waiting for `volumeattachment` to clear |
| 6 | Recovered but the post-check write still fails |
| 7 | Flux resume failed — **cluster left with reconciliation suspended, page yourself** |

## Algorithm

### Phase 0 — identify and refuse

1. Resolve the workload; auto-detect kind if not given.
2. **Refuse if CNPG-managed** — check for the `cnpg.io/cluster` label on the pods
   or an owning `Cluster` CR. Exit 2 with the runbook section printed.
3. Enumerate PVCs the workload mounts **read-write**. Skip `readOnly: true`
   mounts, matching `pvc-writeprobe`'s own exclusion.
4. Confirm at least one is actually unwritable, with a **byte write** in the
   running pod (`printf ok > <mount>/.recover-probe`, then `-s`, then remove).
   `touch` passes on a full filesystem. If everything is writable, say so and
   exit 0 — do not perform surgery on a healthy workload.
5. **Count cluster-wide unwritable PVCs** via `homelab_pvc_writable == 0`. If
   above a threshold (default 3), warn that this looks like an infrastructure
   event and require explicit confirmation.

### Phase 1 — suspend the *correct* Kustomization

```bash
KS=$(kubectl -n "$NS" get "$KIND/$NAME" \
      -o jsonpath='{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}')
KS_NS=$(kubectl -n "$NS" get "$KIND/$NAME" \
      -o jsonpath='{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/namespace}')
```

- If the labels are absent, the resource is not Flux-managed — **skip suspend
  entirely** rather than guessing.
- **Re-entrancy:** if the Kustomization is *already* suspended, record that and
  **do not resume it on exit.** Someone else may have suspended it for a
  migration; resuming it would break their work silently.
- Only after recording that state, suspend, and arm the trap:

```bash
trap 'flux resume kustomization "$KS" -n "$KS_NS" || echo "MANUAL RESUME REQUIRED" >&2' EXIT INT TERM
```

**This trap is the single highest-value line in the script.** Every other step
can be done by hand; this is the one a human reliably forgets under pressure.

### Phase 2 — detach

**Deployment:** scale to 0, then wait for **both**:

```bash
# pods gone -- note -eq and tr -d ' ', not a string compare
until [ "$(kubectl -n "$NS" get pods -l "$SEL" --no-headers 2>/dev/null \
          | wc -l | tr -d ' ')" -eq 0 ]; do ... done

# and volumeattachment gone -- pod termination is NOT proof
PV=$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.spec.volumeName}')
until [ -z "$(kubectl get volumeattachment -o jsonpath=\
     "{range .items[?(@.spec.source.persistentVolumeName=='$PV')]}{.metadata.name}{end}")" ]; do ... done
```

**StatefulSet:** `kubectl delete pod` on the affected member. No scale, no
suspend needed — the controller recreates it in place and the delete is enough
to detach. This is the safe path the parent plan identifies as the only
automatable one.

Both waits are bounded by `--timeout`; exceeding it exits 4 or 5 **with the trap
still resuming Flux.**

### Phase 3 — reattach and verify

1. Scale back to the original replica count (captured in phase 0 — do not assume
   1).
2. Wait for the pod to be `Ready`.
3. **Verify with a real byte write**, not pod status. Status lies here; that is
   the whole premise of `pvc-writeprobe`.
4. Exit 6 if the write still fails — recovered the attachment, did not fix the
   volume. That is a different problem and the operator needs to know which.

### Phase 4 — resume and confirm

The trap fires. Then explicitly verify:

```bash
flux get kustomizations -A   # SUSPENDED must be False for the one we touched
```

If resume failed, exit 7 loudly. A silently suspended Kustomization is worse
than the original fault.

## Test plan

Reuses the game-day harness now documented in `AGENTS.md` §
*Testing the alert (game-day)*.

| # | Scenario | Expect |
| --- | --- | --- |
| 1 | Healthy workload | exits 0, changes nothing |
| 2 | `--dry-run` on a read-only PVC | prints plan, suspends nothing |
| 3 | Deployment, PVC remounted `ro` | recovers, verifies, Flux resumed |
| 4 | **`Ctrl-C` mid-wait** | Flux resumed by the trap |
| 5 | **`SIGKILL` mid-wait** | Flux stays suspended — trap cannot fire. Document this as a known limit |
| 6 | Kustomization already suspended beforehand | left suspended on exit |
| 7 | CNPG cluster | exit 2, no mutation |
| 8 | Workload under `infra-controllers` (z2m) | suspends `infra-controllers`, not `apps-production` |
| 9 | Non-Flux-managed workload | no suspend attempted |
| 10 | `volumeattachment` never clears | exit 5, Flux resumed |

Scenarios 3–6 and 8 run against a throwaway PVC on `truenas-iscsi-ephemeral` in
a scratch namespace, never production.

## Rollout

1. Land the script under `scripts/` with the repo's existing shell conventions.
2. Add `shellcheck` to CI if not already covering `scripts/`.
3. Replace the seven-step block in `AGENTS.md` with the one-liner **plus** the
   manual steps kept underneath — the script can be unavailable, and the runbook
   must still work without it.
4. Update the `PvcNotWritable` alert annotation to name the script.
5. Fix the `apps-production` hardcode in `AGENTS.md` regardless of whether the
   script lands — that bug is live now.

## Open questions

- **Threshold for the mass-failure warning.** 3 is a guess. On 2026-08-13 it was
  ~20. Anything from 2 to 5 is defensible; needs a decision, not a default.
- **Should it handle multiple PVCs on one workload in a single run**, or require
  `--pvc` and one invocation each? Simpler is one at a time; more convenient is
  all of them.
- **Does `flux suspend` need `--wait`?** If suspend is asynchronous there is a
  window where a reconcile is already in flight and still reverts the scale.
  Needs testing.
- **Where does the script live for an operator who is not in the repo?** A
  2am recovery may start from a phone. Worth considering whether the steps stay
  copy-pasteable from `AGENTS.md` regardless.
