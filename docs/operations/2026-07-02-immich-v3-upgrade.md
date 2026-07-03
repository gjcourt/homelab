# Immich v2.7.5 → v3.0.1 upgrade runbook

**Date:** 2026-07-02 (revised 2026-07-03 after staging rehearsal)
**Status:** staged (two PRs open, NOT applied). Operator-gated — do not merge/reconcile
without executing the ordered steps below.
**Scope:** Immich server + machine-learning bump to `v3.0.1`, plus the mandatory
pgvecto.rs → VectorChord database migration that Immich v3 forces.

> This runbook is the execution companion to the planning doc
> [`docs/plans/2026-06-02-immich-vectorchord-migration.md`](../plans/2026-06-02-immich-vectorchord-migration.md).
> That plan defaulted to a fresh-cluster rebuild (Option 3A) because no
> dual-extension CNPG image had been sourced. That open question is now
> **resolved**: `ghcr.io/corentingiraud/cnpg-pgvector-vectorchord:16-migration`
> ships pgvecto.rs + VectorChord + pgvector together, which makes the
> **in-place extension swap (Option 3B)** viable and embedding-preserving. This
> runbook executes 3B, in the corrected order proven by the 2026-07-03 staging
> rehearsal (see §7).

---

## 0. Why this is TWO PRs, not one

The upgrade is split into two ordered PRs on purpose. The 2026-07-03 staging
rehearsal proved that a single combined PR is unsafe: when Flux reconciles a PR
that carries **both** the DB migration image (Phase B) **and** the app v3 bump
(Phase C), it applies them together, and **v3 crashloops against un-migrated
data** (`Must be superuser to create extension`, then the pgvecto.rs
compatibility check fails). The DB migration + data-migration + verify MUST
complete before the app image moves to v3. That gap cannot exist inside one PR.

| PR | Branch | Contents | Merge state |
|---|---|---|---|
| **PR-B** — Phase B | `feat/immich-phase-b-vectorchord-db` | `database.yaml` (prod + staging) → dual-extension migration image + `shared_preload_libraries: [vectors.so, vchord.so]`. This runbook + STATUS/plan docs. **No app bump.** | **Ready.** Non-destructive (only ADDS vchord). Merge FIRST. |
| **PR-C** — Phase C | `feat/immich-phase-c-app-v3` | `deployment.yaml` + `deployment-patch.yaml` (prod + staging) server/ML → `v3.0.1`; `database.yaml` cleanup (drop `vectors.so` from preload, remove `vectors` from `search_path`, repoint `imageName` to VectorChord-only image). | **DRAFT.** Do NOT merge until PR-B is merged **and** the manual `CREATE EXTENSION vchord` + v2.7.5 data-migration + verify (§4 Phase B) have completed on the target environment. |

> **Environment / branch caveat (Flux topology + staging auto-combine).**
> Staging and production reconcile from **different git refs**:
> - `apps-production` Kustomization → `flux-system` GitRepository → **`master`** branch.
> - `apps-staging` Kustomization → `flux-system-staging` GitRepository → **`staging`** branch.
>
> **Production is gated purely on `master`.** Neither PR-B nor PR-C is on
> `master` until you merge it, so production is untouched until you sequence the
> merges yourself — merge PR-B, run the manual Phase-B migration + verify on
> prod, and only then merge PR-C. That is the whole point of the split.
>
> **Staging auto-combines open PRs — this is the trap.** The `staging` branch is
> force-rebuilt by CI (`.github/workflows/staging-deploy.yaml`) as **`master` +
> every open, mergeable, green PR** — and that filter does **not** exclude
> drafts. So while BOTH PR-B and PR-C are open, CI will try to lay both onto
> `staging` at once, which is exactly the combined migration-image + app-v3 apply
> that crashlooped in the rehearsal. Two things partly save you, but neither is
> something to rely on:
> 1. PR-B and PR-C edit the **same `database.yaml` lines** (`imageName`,
>    `shared_preload_libraries`), so they **conflict**; CI's merge step aborts on
>    conflict and skips one of them. Which one it keeps is **order-dependent**
>    (`gh pr list` order), so staging can end up as *master+B*, *master+C*, or a
>    skip — and *master+C alone* (v3 app on un-migrated data) is the bad one.
> 2. Staging (`immich-stage`) is the **already-rehearsed preview** environment,
>    not prod, so churn there is noisy, not dangerous.
>
> **Operating rule for the open-PR window:** keep **PR-C a draft** and do not mark
> it ready. If you want zero staging churn while PR-B soaks, **close PR-C until
> PR-B is merged to `master`**, then reopen PR-C, rebase it onto the new `master`
> (the `database.yaml` overlap with B resolves in favour of C's end-state), and
> let staging pick it up cleanly. **Production is never at risk from this** — it
> only ever sees `master`, which you control the merge order of.

---

## 1. Why this is high-risk

Immich **v3.0.0 removed all support for the pgvecto.rs vector extension.** Our
CNPG cluster (`immich-db-prod-cnpg-v3`) is still on pgvecto.rs today
(`cloudnative-pgvecto.rs:16-v0.3.0`, extension `vectors`, `shared_preload_libraries: vectors.so`).
If Immich is bumped to v3 before the DB is migrated to VectorChord, the server
**refuses to start** — v3 does the compatibility check and will not perform the
pgvecto.rs→VectorChord data migration itself. The migration MUST be done while
Immich is still on a v2.x build (v2.7.5 supports both extensions and ships the
automatic data migration).

**The vector migration is one-way.** Once the DB is on VectorChord and you have
started Immich ≥ v1.133.0 against it, you must not downgrade Immich below
1.133.0. Dropping the `vectors` extension is irreversible without a restore.

Citations:
- Immich v3.0.0 release / discussion #29439 — "drops support for pgvecto.rs";
  `DB_VECTOR_EXTENSION=pgvecto.rs` now errors.
- <https://docs.immich.app/install/upgrading/> — migrate to VectorChord; do not
  downgrade below 1.133.0 after switching.
- <https://immich.app/blog/v3-migration> — "Support for pgvecto.rs has been removed in v3."

## 2. Version delta

| Component | From | To |
|---|---|---|
| immich-server | `v2.7.5` (`sha256:c15bff75…`, index) | `v3.0.1` (`sha256:46dedfc5…`, index) |
| immich-machine-learning | `v2.7.5` (`sha256:a2501141…`, index) | `v3.0.1` (`sha256:cb2128c5…`, index) |
| CNPG DB (migration window, PR-B) | `cloudnative-pgvecto.rs:16-v0.3.0` | `corentingiraud/cnpg-pgvector-vectorchord:16-migration` (`sha256:0e4f45d3…`) |
| CNPG DB (end state, PR-C) | — | `ghcr.io/tensorchord/cloudnative-vectorchord:16-0.4.2` (`sha256:649da2df…`, VectorChord-only) |
| Vector extension | pgvecto.rs `vectors` 0.3.0 | VectorChord `vchord` 0.4.x (+ `vector`/pgvector via CASCADE) |
| Postgres major | 16 | **16 (unchanged)** |

**Postgres major stays 16.** Immich v3 accepts PG `>= 14, < 20`; no PG bump is
required or performed here. (PG17 + VectorChord is a separate follow-up.)

## 3. Breaking changes in v3.0.0 (that matter to us)

1. **pgvecto.rs removed** → VectorChord required. Handled by §4 below. (Highest risk.)
2. **OAuth: insecure requests disallowed by default** and **`oauth.issuerUrl`
   must parse as a valid URL.** Our config uses
   `issuerUrl: https://auth.burntbytes.com` (valid, HTTPS) so **no config change
   is needed** — but verify SSO login after the bump. If the Authelia hostAlias
   path ever resolves over HTTP, set `oauth.allowInsecureRequests` in admin
   settings (we should not need to).
3. **class-validator → zod validation.** API error response objects changed
   shape (`error`/`statusCode` fields removed, structured errors, correlationId
   moved to `X-Correlation-ID` header). Only matters for external API scripts —
   we have none.
4. **Metric names changed** (underscores → dots/periods). Any Immich Grafana
   panels / Prometheus rules keyed on old metric names will go blank. Check
   dashboards post-upgrade; none are known to be load-bearing.
5. Removed/changed API endpoints (replace-asset, getRandom, old timeline sync,
   `/api/server/theme`, deviceId/deviceAssetId, video duration now in **ms** not
   seconds, `AuditLogCleanup` job removed). No impact on our deployment; noted
   for completeness. Deprecated server + ML env vars removed — we set none of them.

## 4. Ordered upgrade procedure

Do **staging fully first**, soak, then prod. Same steps, swap namespace/cluster
names and land on the matching git ref (staging → `staging` branch, prod →
`master`; see §0 caveat).

### Phase A — BACK UP (do not skip)

1. Trigger an on-demand CNPG base backup and confirm it completes:
   ```
   kubectl -n immich-prod get cluster immich-db-prod-cnpg-v3
   # create an on-demand Backup CR against the barman objectstore, wait Completed
   ```
2. Verify the backup is **restorable** — spin a throwaway recovery cluster from
   the objectstore in a temp namespace, query a few rows, tear it down
   (same pattern as the 2026-04-18 DR drill).
3. Belt-and-suspenders: export the embeddings so a full ML recompute is never
   forced:
   ```sql
   COPY (SELECT * FROM face_search)  TO '/tmp/face_search.csv'  WITH (FORMAT CSV, HEADER);
   COPY (SELECT * FROM smart_search) TO '/tmp/smart_search.csv' WITH (FORMAT CSV, HEADER);
   ```
   Push the CSVs to S3 alongside the WAL archive.

### Phase B — introduce VectorChord (Immich STILL on v2.7.5) — this is **PR-B**

**PR-B** stages only the `database.yaml` change to the dual-extension migration
image with `shared_preload_libraries: [vectors.so, vchord.so]`. It carries **no
app bump**. Merge PR-B (onto the target ref) first, then:

4. Reconcile the `database.yaml` change (migration image). CNPG does a rolling
   restart of the 3 instances onto the image that carries both `vectors.so` and
   `vchord.so`. This is **non-destructive** — it only adds VectorChord; the
   existing pgvecto.rs columns remain readable. Wait for the cluster to report
   healthy (primary + 2 replicas) and WAL archiving green.

5. **Create the VectorChord extension AS A SUPERUSER — MANDATORY, MANUAL.**
   The `immich` role is the database owner but is **NOT a superuser**, and
   `CREATE EXTENSION` for `vchord`/`vector` requires superuser. If you try to let
   immich do it (or run the SQL as the `immich` role), v3 boot / the migration
   fails with:
   `permission denied to create extension "vector" — Must be superuser`.
   The cluster already has `enableSuperuserAccess: true`, so connect as the
   `postgres` superuser and pre-create the extension **before** immich migrates:
   ```sql
   -- Connect to the immich database AS postgres (superuser), NOT as immich:
   --   PGPASSWORD from the cluster's -superuser secret; psql -U postgres -d immich
   CREATE EXTENSION IF NOT EXISTS vchord CASCADE;   -- CASCADE pulls in `vector` (pgvector)
   GRANT ALL ON SCHEMA public TO immich;
   ```
   Get the superuser password:
   ```
   kubectl -n immich-prod get secret immich-db-prod-cnpg-v3-superuser \
     -o jsonpath='{.data.password}' | base64 -d
   ```
   Verify both extensions are present:
   ```sql
   SELECT extname FROM pg_extension WHERE extname IN ('vchord','vector','vectors');
   -- expect: vchord, vector, vectors  (vectors still present until Immich drops it in step 6)
   ```

6. **Restart `immich-server` (still v2.7.5) and let it migrate + auto-drop
   `vectors`:**
   ```
   kubectl -n immich-prod rollout restart deploy/immich-server
   ```
   Watch the logs. Immich v2.7.5's automatic pgvecto.rs→VectorChord migration
   moves the embeddings into VectorChord indexes **and drops the old `vectors`
   extension itself** — a manual `DROP EXTENSION vectors` is therefore
   belt-and-suspenders, not required (see §7 finding 3). It is **normal** for
   logs to sit on `Reindexing clip_index` / `Reindexing face_index`.
   > **PROD WILL ACTUALLY DO WORK HERE.** Staging had **0 embeddings**, so the
   > rehearsal did not exercise the real reindex. Production has the full photo
   > library, so this step is a **real, CPU-bound reindex that can take minutes
   > to tens of minutes**. Expect it, watch the `immich-server` logs, do not
   > kill the pod mid-reindex, and wait for `Finished running migrations` before
   > proceeding.

7. **Verify** before going further (this is the go/no-go gate for PR-C):
   - Web UI loads; a **Smart Search** text query returns results.
   - **Face** grouping still shows people; trigger Face Detection if needed.
   - No `pgvecto.rs` DEPRECATION WARNING in `immich-server` logs.
   - `SELECT extname FROM pg_extension;` shows `vchord` + `vector`, and
     **`vectors` is gone** (Immich dropped it). If `vectors` is somehow still
     present after a clean migration, drop it manually as superuser:
     `DROP EXTENSION IF EXISTS vectors;` (one-way; Phase-A backups are the parachute).
   - No background-worker errors in the PG logs.

### Phase C — land the app bump + DB cleanup — this is **PR-C** (draft until §4.7 passes)

Only after Phase B verification passes on the target environment, un-draft and
merge **PR-C**. It bundles the app v3 bump and the DB cleanup:

8. **DB cleanup** (in PR-C's `database.yaml`): `vectors.so` is removed from
   `shared_preload_libraries` (leaving `vchord.so`), `vectors` is removed from
   `search_path`, and `imageName` is repointed to the VectorChord-only image
   `ghcr.io/tensorchord/cloudnative-vectorchord:16-0.4.2`. Reconciling this does
   another rolling restart onto the slimmer image. (If you prefer to defer the
   image repoint and stay on the migration image, that is fine — it runs
   correctly with only `vchord.so` preloaded; PR-C can be trimmed accordingly.)

9. **App bump to v3.0.1** (PR-C's `deployment.yaml` / `deployment-patch.yaml`).
   `strategy: Recreate` means a brief outage while server + ML restart. v3 runs
   its own schema migrations on first boot.

10. Verify: web UI, SSO login, uploads, Smart Search, Face grouping, Jobs page
    all healthy; `kubectl -n immich-prod get pods` all Running.

### Phase D — soak + cleanup

- 24h prod soak. Confirm WAL archiving healthy, search/faces working, no
  deprecation warnings.
- Update `docs/operations/apps/immich.md` (the "PostgreSQL (CNPG)" bullet still
  says pgvector/pgvecto.rs — flip to VectorChord) and `docs/STATUS.md`.

## 5. Rollback

| Failure point | Rollback |
|---|---|
| Phase B — VectorChord create / reindex fails, but `vectors` NOT yet dropped | Revert `database.yaml` to `cloudnative-pgvecto.rs:16-v0.3.0`; Immich v2.7.5 keeps using pgvecto.rs. No data lost. |
| Phase B — Immich has migrated + dropped `vectors` | **No in-place downgrade below 1.133.0.** DB is now VectorChord-only. To roll back, restore the Phase-A base backup into a fresh cluster; embeddings also recoverable from the Phase-A CSVs. Immich stays on v2.7.5 (which supports VectorChord) — you do **not** have to advance to v3 just because the DB migrated. |
| Phase C — after v3 app boot | Revert only the app image (PR-C) to v2.7.5, which supports VectorChord; the DB stays on VectorChord. |
| Phase C — v3 boot loops but DB already on VectorChord | Same as above — revert the app image to a v2.x that supports VectorChord (≥ v1.133.0); DB stays. |

**Hard rule:** never merge/reconcile PR-C (app → v3) before Phase B verification
passes — v3 cannot read pgvecto.rs data and cannot run the migration, and it
cannot `CREATE EXTENSION` as the non-superuser `immich` role.

## 6. Post-upgrade verification checklist

- [ ] `kubectl -n immich-prod get pods` — server, ML, redis, 3× DB all Running/Ready
- [ ] `kubectl -n immich-prod get cluster` — healthy, 3 instances, WAL archiving OK
- [ ] Web UI loads at https://photos.burntbytes.com
- [ ] SSO (Authelia OIDC) login works
- [ ] Photo upload from mobile app succeeds
- [ ] Smart Search text query returns results
- [ ] People / face grouping intact
- [ ] Admin → Jobs processing without errors
- [ ] No `pgvecto.rs` deprecation warning in server logs
- [ ] `SELECT extname FROM pg_extension;` shows `vchord` + `vector`, **not** `vectors`

## 7. Lessons from the staging rehearsal (2026-07-03)

We rehearsed the full pgvecto.rs → VectorChord + v3.0.1 migration on the
`immich-stage` cluster before touching prod. Findings — all folded into the
procedure above:

1. **A combined PR is unsafe.** Reconciling a PR that carried Phase B (migration
   image) **and** Phase C (app v3) together applied both at once, and v3
   crashlooped against un-migrated data. → The upgrade is now **two ordered PRs**
   (§0): PR-B (DB) merges and its manual migration completes *before* PR-C (app)
   is even un-drafted.

2. **The `immich` DB role is not a superuser and cannot `CREATE EXTENSION`.**
   v3 boot failed with `permission denied to create extension "vector" — Must be
   superuser`. → `vchord` (which CASCADEs in `vector`/pgvector) must be
   **pre-created as the `postgres` superuser** before immich migrates. This is
   now the explicit, mandatory manual step §4.5. `enableSuperuserAccess: true`
   is already set on both clusters, so the `-superuser` secret is available.

3. **Immich v2.7.5's automatic migration drops the old `vectors` extension
   itself.** The v2.7.5 server migration moves the embeddings **and** runs
   `DROP EXTENSION vectors` as part of the migration. → The manual
   `DROP EXTENSION vectors` is **belt-and-suspenders, not required**; §4.6 now
   just verifies that `vectors` is gone afterward and only drops it manually if
   it somehow survived.

4. **Staging had 0 embeddings — the real reindex cost was NOT exercised.** The
   migration completed instantly on staging because there was no vector data to
   move. Prod has the real photo library, so the v2.7.5 reindex is a genuine,
   CPU-bound wait. → §4.6 now flags this as an expected minutes-to-tens-of-minutes
   wait with a "watch the logs, don't kill the pod" note.

5. **Final verified-good state:** extensions `vchord` + `vector` present,
   `vectors` gone, and immich **v3.0.1 boots clean**. That is the target state
   the §6 checklist asserts.

### Note: can CNPG pre-create the extension declaratively (avoid the manual step)?

Partly, but not cleanly for *this* migration, so we keep §4.5 manual:

- The operator is **CloudNativePG 1.26.1** (Helm chart `cloudnative-pg` 0.25.0,
  `appVersion: 1.26.1`). Declarative extension management via the `Database` CRD
  (`spec.extensions[].name` / `ensure: present`) **was introduced in CNPG 1.26**,
  so the operator technically supports it, and a `Database` CR *can* be applied
  against an already-running cluster (it is not bootstrap-only).
- **But** `bootstrap.initdb.postInitSQL` only runs at cluster **creation**, so it
  is useless for this in-place migration of an existing cluster (the `immich`
  cluster already exists; postInitSQL will never re-run).
- We deliberately do **not** adopt a `Database` CR here because: (a) no other
  cluster in this repo uses one — it would be a novel, unrehearsed pattern
  introduced during a one-way, high-risk migration; (b) the `immich` database was
  bootstrapped via `initdb`, and layering a `Database` CR to manage its
  extensions/ownership risks reconcile conflicts; and (c) this is a **one-shot,
  operator-babysat** step where a manual superuser `CREATE EXTENSION vchord
  CASCADE` is clearer and easier to sequence against the "watch the reindex" wait
  than a declarative reconcile whose CASCADE behavior for `vchord`→`vector` we
  have not verified. Adopting declarative `Database`-CR extension management is a
  reasonable **follow-up** for steady state, not for this cutover.
