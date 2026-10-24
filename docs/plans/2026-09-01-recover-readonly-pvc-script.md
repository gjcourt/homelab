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

### ⚠️⚠️ Suspending a Kustomization is the wrong lever entirely

The first draft of this plan proposed fixing the runbook's hardcoded
`apps-production` by deriving the correct Kustomization from labels. **That is a
correct fix to the wrong mechanism.**

`infra-controllers` reconciles:

```
barman-cloud  cert-manager  cilium  cnpg  democratic-csi
kube-prometheus-stack  loki  mosquitto  pingo  promtail
renovate*  snapshot  unpoller  vector  zigbee2mqtt
```

**Recovering zigbee2mqtt would suspend reconciliation of the CSI driver, the
Postgres operator, and the monitoring stack — during a storage incident.**
`infra-configs` `dependsOn` it, so that stops too. Nobody would choose this
consciously; the corrected procedure leads you there and calls it the fix.

**Use the object-scoped annotation instead:**

```bash
kubectl -n "$NS" annotate "$KIND/$NAME" kustomize.toolkit.fluxcd.io/reconcile=disabled
```

Blast radius of exactly one object. It also makes three separate problems in this
plan **disappear rather than get solved**: no Kustomization to derive, no
contention with another operator, and no "was it already suspended" question.

⚠️ **No existing use of this annotation in the repo** — it needs one game-day
scenario before it is trusted. Kustomization suspend stays documented as the
fallback.

### The runbook hardcodes the wrong Kustomization

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
2. **Refuse if CNPG-managed — but resolve by PVC, not by workload.**

   The label `cnpg.io/cluster` is real. The guard as first drafted is dead code:
   CNPG instances are **bare Pods owned by a `Cluster` CR**. There are no
   StatefulSets in any CNPG namespace and no Deployment carries a CNPG label — so
   a CNPG cluster can never resolve as `deploy/x` or `sts/x`, and the script
   would exit **1, "usage error."** At 2am that reads as *"I typed it wrong"*,
   not *"this is the dangerous path."* Wrong signpost at the worst moment.

   Match against `kubectl get cluster.postgresql.cnpg.io -n <ns>` and against the
   PVC's owning cluster. Read `cnpg.io/instanceRole` so the message distinguishes
   primary from replica — the runbook's advice differs and the primary case is
   the switchover hazard.

   *One doubt resolved:* the script **cannot** scale a Postgres primary to zero.
   There is no Deployment or StatefulSet for `kubectl scale` to act on.
3. Enumerate PVCs the workload mounts **read-write**. Skip `readOnly: true`
   mounts, matching `pvc-writeprobe`'s own exclusion.
4. Confirm at least one is actually unwritable, with a **byte write** in the
   running pod (`printf ok > <mount>/.recover-probe`, then `-s`, then remove).
   `touch` passes on a full filesystem. If everything is writable, say so and
   exit 0 — do not perform surgery on a healthy workload.
5. **Count cluster-wide unwritable PVCs** — and **fail closed**.

   ⚠️ `count(homelab_pvc_writable == 0)` returns an **empty vector** on a healthy
   cluster, not zero. `jq '.data.result[0].value[1]'` yields `null`, and
   `[ null -gt 3 ]` is a fatal error under the repo's `set -euo pipefail`
   convention — so the script dies before doing anything, on a healthy cluster.
   The obvious repair is worse: `count(... == bool 0)` returns **75**, because
   `== bool` emits a sample per series. That inverts the gate to always-tripped.

   Verified live today: `[]` · `75` · and the correct form:

   ```promql
   sum(homelab_pvc_writable == bool 0) or vector(0)   # -> 0
   ```

   ⚠️ **This gate rests on the one signal that goes mute in the scenario it
   guards.** Prometheus runs on two 20Gi RWO iSCSI PVCs, and `AGENTS.md` already
   states that if Prometheus' own PVC is affected the probe goes silent. On a
   repeat of 2026-08-13 the query plausibly returns nothing and the script
   reports "below threshold, proceeding" — **weakest exactly when load-bearing.**

   So: treat *cannot ask* as **tripping** the gate, not passing it. Distinguish
   HTTP failure, non-200, and empty vector explicitly. Cross-check
   `homelab_pvc_writeprobe_last_run_seconds` — a probe that has not swept in more
   than two intervals is itself a trip condition, and it survives Prometheus
   going read-only for the ~10 minutes before scraping stops.

   **`--yes` must not suppress this prompt.** It is the one gate that has to
   survive the flag people habitually add to reruns.

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

⚠️ **The obvious trap is wrong, and the obvious test passes it.**

```bash
trap 'flux resume ...' EXIT INT TERM      # DO NOT
```

**A trap handler that does not `exit` does not stop the script.** Verified: with
`trap 'echo TRAP' EXIT TERM` around a wait loop, SIGTERM fired the handler and
**the loop ran to completion**, then the trap fired again at EXIT. So `Ctrl-C`
during the detach wait would resume Flux and then **scale the workload back up**
onto the still-errored device, while the operator believes they aborted.

Test scenario 4 as originally written — *"Flux resumed by the trap"* — **passes
this bug.** It confirms the wrong thing.

```bash
_CLEANED=
cleanup() {
  [ -n "$_CLEANED" ] && return; _CLEANED=1
  [ -n "$DISABLED_BY_US" ] || return
  # kubectl patch/annotate, NOT `flux resume`: resume WAITS for the apply
  # (--timeout default 5m) and can return non-zero when spec.suspend was
  # correctly written but Ready was not reached -- which would make exit 7
  # cry wolf on a correct outcome. Retry the cheap write instead.
  for i in 1 2 3 4 5; do
    kubectl -n "$NS" annotate --overwrite "$KIND/$NAME" \
      kustomize.toolkit.fluxcd.io/reconcile- && return
    sleep 2
  done
  echo "MANUAL: kubectl -n $NS annotate $KIND/$NAME kustomize.toolkit.fluxcd.io/reconcile-" >&2
}
trap 'cleanup' EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
```

**Arm the trap BEFORE the first mutation**, gated on a `DISABLED_BY_US` flag set
only after the annotate succeeds — otherwise a signal in the window between
mutating and arming leaves no handler at all.

### Phase 2 — detach

⚠️ **Select pods by the PVC they mount, not by a label.** The first draft wrote
`kubectl get pods -l "$SEL"` and never defined `$SEL`. `app=<name>` — what
`AGENTS.md` uses — is wrong for **32 of 79 Deployments**; `cloudflared` uses
`app.kubernetes.io/name`, `immich-server` uses `app=immich,component=server`. A
selector matching nothing makes the "wait for pods to terminate" loop exit on the
first poll: a **silent pass**, not a visible failure. Selecting by PVC is both
correct and what the script actually cares about.

⚠️ **Two Deployments can share one RWO PVC.** Live in production:
`immich-upload-pvc` is mounted by **both** `immich-server` and
`immich-microservices`. Scaling one to zero will never clear that
VolumeAttachment — so the script spins the full timeout with reconciliation
disabled and the volume still read-only. **Enumerate every pod mounting the PVC
and either refuse, naming the co-mounters, or handle them together.**

⚠️ **Wait on the union of VolumeAttachments**, not one. 11 of 28 PVC-mounting
Deployments mount more than one PVC; `jellyfin`, `audiobookshelf` and `snapcast`
each mount more than one RWO iSCSI volume. Scale-to-0 detaches all of them, so
waiting on one lets you scale up while another is still attached — the exact
re-attach the gate exists to prevent. This was listed as an open question; it is
a correctness bug.

⚠️ **Assert the VolumeAttachment EXISTS before scaling down.** Static NFS PVs
(`immich-photos-pv-prod`) have none at all, so the wait returns empty on the
first poll and the script reports success having verified nothing. Absence
before scale-down is an error, not a pass.

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
| 4 | **`Ctrl-C` mid-wait** | **The script STOPS.** Reconciliation re-enabled AND the replica count not restored. Asserting only "reconciliation re-enabled" passes the no-`exit`-in-trap bug |
| 5 | **`SIGKILL` mid-wait** | Flux stays suspended — trap cannot fire. Document this as a known limit |
| 6 | Reconcile annotation already present from another run | refuse, naming the holder |
| 7 | CNPG cluster | exit 2, no mutation |
| 8 | Workload under `infra-controllers` (z2m) | annotates the Deployment only; `infra-controllers` untouched |
| 11 | **Shared RWO PVC** (`immich-upload-pvc`, two Deployments) | refuses or handles both; never spins to timeout |
| 12 | **Multi-PVC workload** (`jellyfin`, `snapcast`) | waits on the union of VAs |
| 13 | **Static NFS PVC** (`immich-photos-pvc`, no VA) | errors before scale-down, does not report success |
| 14 | **Prometheus unreachable** | gate TRIPS; `--yes` does not suppress it |
| 15 | CNPG cluster by name | exit **2** with the runbook, not exit 1 |
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

## ⚠️ The highest-value change is not in the script

**Nothing in this cluster notices a Kustomization that has been left suspended.**
Not Flux, not Prometheus, not a human until something stops deploying.

A script can never be reliable enough to be the only thing standing between you
and that state — `SIGKILL` and a closed laptop lid both defeat any trap. **Add a
Prometheus rule that fires when any Kustomization has been suspended for more
than ~15 minutes.** Build it on `gotk_resource_info` / kube-state-metrics; Flux
GA dropped `gotk_reconcile_condition`.

That closes the hole permanently, independently of whether this script is ever
written, and it covers the `SIGKILL` case this plan otherwise documents as an
accepted limit. **It should land first.**

## Omissions found in review

- **Concurrency.** Two operators — or one plus a stale run — both disable
  reconciliation, both scale, and the first to finish re-enables while the second
  is mid-detach, at which point Flux scales the second's workload back onto the
  errored device. For a ~20-volume event, concurrent invocation is the *expected*
  case. `flock` locally, plus a cluster-side annotation recording holder and
  timestamp so a second operator on another machine is told who has it. Refuse,
  do not queue.
- **Re-entrancy is solved by the object annotation**, not by the earlier
  "don't resume if already suspended" rule — which failed wrong in exactly the
  crashed-run case it existed for: no running pod to byte-write, and a captured
  live replica count of 0.
- **Do not restore a replica count on Flux-managed workloads.** Git is
  authoritative and re-enabling reconciliation overwrites whatever the script
  wrote seconds later. `openwebui-prod` and `overture-prod` are live at git
  `replicas: 0`; "restoring" 0 makes the readiness wait never satisfy and yields
  a false exit 6. Scale to 0, wait for detach, re-enable, let Flux restore, then
  verify. The script's entire write set becomes `{replicas: 0}` plus one
  annotation.
- **Namespace terminating.** `scale --replicas=0` succeeds, scale-up is rejected.
  One `deletionTimestamp` check in Phase 0.
- **Audit trail.** `tee` a transcript per run and emit one structured summary:
  workload, PVC, PV, VA, **duration per phase**, exit code. Phase durations are
  what you cannot reconstruct later and what tells you whether 300s is right.
- **Preflight.** Verify `flux`/`kubectl`/`jq` exist, the context is the intended
  cluster, and RBAC permits the writes — *before* the first mutation. Echo the
  resolved context in the confirmation prompt. Disabling reconciliation with a
  tool you never confirmed you can invoke again is the one ordering you cannot
  recover from.
- **HPA:** zero cluster-wide today; a check costs nothing and future-proofs.
- **PDBs:** ~40 exist, several `maxUnavailable: 0` — **irrelevant**, they gate the
  eviction API only, and neither `scale` nor `delete pod` uses it. Worth a comment
  so nobody "improves" the script to use `kubectl drain`.
- **`shellcheck` runs in `make lint-shell` but no workflow calls it.** Rollout
  step 2 is real work.

## Open questions

- **Threshold for the mass-failure warning.** 3 is a guess. On 2026-08-13 it was
  ~20. Anything from 2 to 5 is defensible; needs a decision, not a default.
- **Does `flux suspend` need `--wait`?** If suspend is asynchronous there is a
  window where a reconcile is already in flight and still reverts the scale.
  Needs testing.
- **Where does the script live for an operator who is not in the repo?** A
  2am recovery may start from a phone. Worth considering whether the steps stay
  copy-pasteable from `AGENTS.md` regardless.
