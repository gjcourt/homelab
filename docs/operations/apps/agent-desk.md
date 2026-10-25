# agent-desk (Plane tickets → bench-cloud Jobs)

## 1. Overview

The dispatcher for lab 03-042's agent desk. When George labels a Plane ticket `ready`, it starts a
bench-cloud task Job to work the ticket into a PR, and it moves the ticket through In Progress,
In review, Blocked and Done as the work goes.
Plan: [`docs/plans/2026-10-22-agent-desk-loop.md`](../../plans/2026-10-22-agent-desk-loop.md).
Code: [`images/agent-desk/`](../../../images/agent-desk/README.md).

| Environment | Namespace | Plane | Jobs |
| --- | --- | --- | --- |
| Staging | `agent-desk-stage` | `plane-stage`, workspace `my-test-workspacce` | **Dry run** (`DRY_RUN_JOBS=true`): created with `dryRun=All` in `bench-cloud`, never stored |
| Production | `agent-desk` | `plane-prod`, workspace `home`, SANDBOX only | **Real**, `docs-fix` only (plan step 6) |

## 2. Architecture

- **One Deployment, one replica, `Recreate`.** One worker thread makes every decision, so two
  events for one ticket can't both start a Job. Don't scale it up.
- **No state of its own.** Plane (states, labels, comments, links) and the Jobs' labels
  (`agent-desk/ticket`, `agent-desk/playbook`) are the record. A restart runs one full poll and
  carries on.
- **Inputs:**
  - Plane webhooks to `http://agent-desk.<ns>.svc.cluster.local/plane`, checked against
    `X-Plane-Signature`. A delivery reads only its own ticket; it lists every ticket only when
    that one could start, since the playbook cap counts In Progress tickets in all projects;
  - a full poll every 10 minutes;
  - a read-only GitHub poll every 5 minutes, once a token is configured.
- **Config:** `apps/<env>/agent-desk/config.yaml`, which sets:
  - the project → repo map;
  - the deny list;
  - the playbooks: their caps, deadlines and prompts.

  It's a ConfigMap reviewed in this repo, and agents can't edit homelab.
- **Rights in bench-cloud** (`apps/base/bench-cloud/rbac.yaml`):
  - the `bench-task-launcher` Role, which can create, delete, get, list and watch Jobs;
  - `get` on the `bench-console` Deployment only, to read the image to run.

  The `bench-cloud-task-jobs` admission policy pins every Job's ServiceAccount and PriorityClass,
  and the ResourceQuota caps the total at 24.
- **Network:**
  - in: Plane's worker on 8080, plus kubelet probes;
  - out: DNS, Plane's api on 8000, the kube-apiserver, and the internet on 443 (0.0.0.0/0 minus
    private ranges).

## 3. Webhook setup (operator)

1. In Plane: **Workspace settings → Webhooks → Add webhook**.
   - **URL:** `http://agent-desk.agent-desk-stage.svc.cluster.local/plane` on staging,
     `http://agent-desk.agent-desk.svc.cluster.local/plane` on production.
   - **Events:** Work items and Work item comments.
2. Copy the secret Plane shows into `webhook-secret` in the SOPS secret, using the template
   `apps/<env>/agent-desk/secret-agent-desk.yaml.example`.
3. The URL's host must be the one entry in Plane's `WEBHOOK_ALLOWED_HOSTS`
   (`apps/<env>/plane/release-patch.yaml`), or Plane refuses to save the webhook.
4. **Plane also resolves the host when the webhook is saved**, so agent-desk's Service must already
   exist: "Webhook not created" in the UI, and `Hostname could not be resolved` in plane-api's log,
   until it does. The order for a new environment is therefore: the Plane change, then agent-desk
   deployed with a placeholder `webhook-secret`, then the webhook, then the real `webhook-secret`.
   agent-desk reads its secrets at start-up, so restart it after that last change:
   `kubectl -n <ns> rollout restart deploy/agent-desk`. Until then it rejects every delivery
   (`bad signature`, 401). Plane doesn't retry a 401 or switch the webhook off for one.

**Production secrets** (operator, both from `.example` templates):

- `apps/production/agent-desk/secret-agent-desk.yaml`:
  - `plane-api-key`: the bot's first key;
  - `webhook-secret`;
  - `github-token`: a read-only fine-grained PAT, with Pull requests and Actions read on
    `gjcourt/agent-sandbox`;
  - `notify-webhook-id`: the production id already in Home Assistant's secret.
- `apps/production/bench-cloud/secret-plane-jobs-token.yaml`: the bot's **second** key, for
  ticket Jobs, so each key can be revoked on its own.

**Plane retries only on connection errors**, never on a non-2xx reply. After its retries run out it
**deactivates the webhook**, and the only notice is an email, which goes nowhere because SMTP is
off. If tickets only move on the 10-minute poll, check the webhook is still active in that
settings page.

### Phone notifications (operator)

agent-desk tells George when a ticket reaches Blocked or In review, through a Home Assistant
webhook (plan, "Notifications"). One random id per environment, in two SOPS secrets:

1. `openssl rand -hex 24` → the id.
2. `apps/<env>/homeassistant/secret-homeassistant-secrets.yaml` (from its `.example`):
   `agent_desk_webhook_id: <id>`. **It must exist before the Home Assistant deployment that
   mounts it**: without `secrets.yaml`, HA can't load `automations.yaml`.
3. `notify-webhook-id: <id>` in `apps/<env>/agent-desk/secret-agent-desk.yaml`. Without it,
   agent-desk logs `no phone notifications` at startup and carries on.

To check: `kubectl -n agent-desk-<env> logs deploy/agent-desk | grep notif` shows each one sent
(`notified: SANDBOX-8 needs you`) or failed. On staging the notice appears as a persistent
notification in staging Home Assistant, never on the phone.

## 4. Observability

```bash
kubectl -n agent-desk-stage logs deploy/agent-desk -f      # "<KEY>: <action> — <reason>"
kubectl -n agent-desk logs deploy/agent-desk -f            # production
kubectl -n bench-cloud get jobs -l agent-desk/ticket        # dispatcher Jobs (none on staging: dry run)
```

- Every accepted delivery logs `delivery <id>: <event> → queued item <id>`, and a rejected one logs
  `rejected delivery <id>: bad signature`. No delivery line after a change in Plane means the
  webhook didn't reach the dispatcher.
- A ticket checked because of a delivery logs its decision, including why it was skipped
  (`SANDBOX-4: skip — ready not applied by George`). The 10-minute poll logs only what it acts on.
- **Plane sends a webhook before it records who changed a label** (measured 1.4 s on staging,
  2026-10-24). When a delivered ticket carries `ready` or `over-cap` that the record doesn't yet
  credit to George, the dispatcher logs `label not attributed yet; rechecking in Ns` and looks again
  after 3, 10 and 30 s. A label the bot added logs this too, three times, and never starts.
- Readiness fails when no poll has finished in two intervals: the worker is stuck or Plane is
  unreachable.

## 5. Common operations

- **Change a cap, deadline or prompt:** edit `config.yaml` and open a PR. The ConfigMap's name
  changes with its content, so the Deployment rolls out on merge.
- **Stop all dispatching:** `kubectl -n agent-desk-stage scale deploy/agent-desk --replicas=0`.
  Flux restores it on the next reconcile, so for a longer stop, suspend that environment's apps
  Kustomization, or set `replicas: 0` in a PR.
- **A ticket won't start:** in order, check that:
  1. `ready` was added by George; a `ready` added by anyone else, or removed and re-added by an
     agent, doesn't count;
  2. the ticket has exactly one `playbook:` label;
  3. the project is in `projects`;
  4. the ticket isn't already In Progress or In review;
  5. its playbook isn't at its cap. A ticket over the cap shows Awaiting approval with a comment,
     and George's `over-cap` starts it. The dispatcher removes `over-cap` once the ticket starts: it
     approves one start. A ticket that later resumes from Blocked while the playbook is full waits
     again, unless George re-adds it.
