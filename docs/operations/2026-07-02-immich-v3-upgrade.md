# Immich v2.7.5 → v3.0.1 upgrade runbook

**Date:** 2026-07-02
**Status:** staged (PR open, NOT applied). Operator-gated — do not merge/reconcile without executing the ordered steps below.
**Scope:** Immich server + machine-learning bump to `v3.0.1`, plus the mandatory
pgvecto.rs → VectorChord database migration that Immich v3 forces.

> This runbook is the execution companion to the planning doc
> [`docs/plans/2026-06-02-immich-vectorchord-migration.md`](../plans/2026-06-02-immich-vectorchord-migration.md).
> That plan defaulted to a fresh-cluster rebuild (Option 3A) because no
> dual-extension CNPG image had been sourced. That open question is now
> **resolved**: `ghcr.io/corentingiraud/cnpg-pgvector-vectorchord:16-migration`
> ships pgvecto.rs + VectorChord + pgvector together, which makes the
> **in-place extension swap (Option 3B)** viable and embedding-preserving. This
> runbook executes 3B.

---

## 1. Why this is high-risk

Immich **v3.0.0 removed all support for the pgvecto.rs vector extension.** Our
CNPG cluster (`immich-db-prod-cnpg-v3`) is still on pgvecto.rs today
(`cloudnative-pgvecto.rs:16-v0.3.0`, extension `vectors`, `shared_preload_libraries: vectors.so`).
If Immich is bumped to v3 before the DB is migrated to VectorChord, the server
**refuses to start** — v3 does the compatibility check and will not perform the
pgvecto.rs→VectorChord data migration itself. The migration MUST be done while
Immich is still on a v2.x build (v2.7.5 supports both extensions).

**The vector migration is one-way.** Once the DB is on VectorChord and you have
started Immich ≥ v1.133.0 against it, you must not downgrade Immich below
1.133.0. `DROP EXTENSION vectors` is irreversible without a restore.

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
| CNPG DB (migration window) | `cloudnative-pgvecto.rs:16-v0.3.0` | `corentingiraud/cnpg-pgvector-vectorchord:16-migration` (`sha256:0e4f45d3…`) |
| CNPG DB (end state, manual) | — | `ghcr.io/tensorchord/cloudnative-vectorchord:16-0.4.2` (VectorChord-only) |
| Vector extension | pgvecto.rs `vectors` 0.3.0 | VectorChord `vchord` 0.4.x |
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

Do **staging fully first**, soak, then prod. Same steps, swap namespace/cluster names.

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

### Phase B — introduce VectorChord (Immich STILL on v2.7.5)

This PR already stages the `database.yaml` change to the dual-extension
migration image with `shared_preload_libraries: [vectors.so, vchord.so]`. Apply
**only that DB change** (not the app bump) first:

4. Reconcile the `database.yaml` change (migration image). CNPG does a rolling
   restart of the 3 instances onto the image that carries both `vectors.so` and
   `vchord.so`. This is **non-destructive** — it only adds VectorChord; the
   existing pgvecto.rs columns remain readable. Wait for the cluster to report
   healthy (primary + 2 replicas) and WAL archiving green.
5. Create the VectorChord extension (the `immich` role is DB owner but not a
   superuser; `enableSuperuserAccess: true` lets you connect as `postgres` if
   the immich role can't create it):
   ```sql
   CREATE EXTENSION IF NOT EXISTS vchord CASCADE;   -- pulls in pgvector
   GRANT ALL ON SCHEMA public TO immich;
   ```
6. Restart `immich-server` (still v2.7.5):
   ```
   kubectl -n immich-prod rollout restart deploy/immich-server
   ```
   Watch the logs. Immich's automatic pgvecto.rs→VectorChord migration (present
   since server revision 228+ / ≥ v1.133.0) rebuilds the vector indexes. It is
   **normal** for logs to sit on `Reindexing clip_index` / `Reindexing
   face_index` for seconds–minutes. Wait for `Finished running migrations`.
7. **Verify** before going further:
   - Web UI loads; a **Smart Search** text query returns results.
   - **Face** grouping still shows people; trigger Face Detection if needed.
   - No `pgvecto.rs` DEPRECATION WARNING in `immich-server` logs.
   - No background-worker errors in the PG logs.

### Phase C — drop pgvecto.rs, land the app bump

8. Remove the now-unused pgvecto.rs extension:
   ```sql
   DROP EXTENSION IF EXISTS vectors;   -- ONE-WAY. Backups from Phase A are the parachute.
   ```
9. (Optional, tidy) Edit `database.yaml` to drop `vectors.so` from
   `shared_preload_libraries`, remove `vectors` from `search_path`, and — if you
   want off the community migration image — repoint `imageName` to
   `ghcr.io/tensorchord/cloudnative-vectorchord:16-0.4.2`. Reconcile; another
   rolling restart. (Can be deferred; the migration image is fine to run on.)
10. **Now** bump Immich to v3.0.1 — reconcile the `deployment.yaml` /
    `deployment-patch.yaml` image changes in this PR. `strategy: Recreate` means
    a brief outage while server + ML restart. v3 runs its own schema migrations
    on first boot.
11. Verify: web UI, SSO login, uploads, Smart Search, Face grouping, Jobs page
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
| Phase C/D — after `DROP EXTENSION vectors` or after v3 boot | **No in-place downgrade.** Restore the Phase-A base backup into a fresh cluster and revert the app image PR. Embeddings also recoverable from the Phase-A CSVs. |
| App v3 boot loops but DB already on VectorChord | Revert only the app image to a v2.x that supports VectorChord (≥ v1.133.0); DB stays. |

**Hard rule:** never bump the app to v3 before Phase B verification passes —
v3 cannot read pgvecto.rs data and cannot run the migration.

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
- [ ] `SELECT extname FROM pg_extension;` shows `vchord`, not `vectors`
