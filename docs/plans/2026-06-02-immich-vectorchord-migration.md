---
status: planned
last_modified: 2026-06-10
summary: "Migrate Immich CNPG from pgvecto.rs to VectorChord"
---

# Immich: migrate pgvecto.rs → VectorChord

## Why now

Two pressures converge:

1. **Upstream deprecation.** Immich logs this on every startup since recent versions:
   > `DEPRECATION WARNING: The pgvecto.rs extension is deprecated and support for it will be removed very soon. See https://docs.immich.app/install/upgrading#migrating-to-vectorchord`
2. **Source image is archived.** The `cloudnative-pgvecto.rs` repo (TensorChord) was archived on 2025-07-30. No further upstream maintenance for the image we currently pin.

These together mean the current CNPG cluster is on a dead path. The trigger to act now (rather than "later") was the 2026-06-02 face-matching regression: pgvecto.rs's HNSW index state got out of sync with PG's catalog (BG-worker error: "The index is not existing in the background worker"), Immich's startup migration recreated both indexes once we dropped them, and the issue resolved — but the underlying class of bug stays as long as we're on pgvecto.rs. VectorChord eliminates the BG-worker indirection.

## Architecture goal

Replace the pgvecto.rs extension with VectorChord on the Immich CNPG cluster, with no embedding-data loss. The Postgres major version (currently 16) stays; only the vector extension flips.

```
before:  CNPG cluster runs ghcr.io/tensorchord/cloudnative-pgvecto.rs:16-v0.3.0
         extensions:   vectors 0.3.0 (pgvecto.rs)
         shared_preload_libraries: vectors.so
         search_path:  "$user", public, vectors

after:   CNPG cluster runs <CNPG image with VectorChord>      ← unresolved; see "Constraint A"
         extensions:   vchord 0.x  (VectorChord)
         shared_preload_libraries: vchord.so  (likely)
         search_path:  default
```

## Current state (measured 2026-06-02)

```yaml
prod cluster:    immich-db-prod-cnpg-v3
  image:         ghcr.io/tensorchord/cloudnative-pgvecto.rs:16-v0.3.0
  instances:     3 (primary + 2 replicas)
  storage:       2Gi per instance, truenas-iscsi (~1Gi used)
  PG version:    16
  extensions:    cube, earthdistance, pg_trgm, plpgsql, unaccent, uuid-ossp, vectors 0.3.0
  vector tables: face_search, smart_search
  vector indexes: face_index (HNSW, m=16, ef_construction=300, vector_cos_ops)
                  clip_index (HNSW, m=16, ef_construction=300, vector_cos_ops)
  WAL archiver:  Barman Cloud (immich-db-prod-cnpg-v3-backup)

staging cluster: immich-db-staging-cnpg-v1
  same image / extensions / shape (recovered from backup 2026-04-18)
  bootstrap.recovery (not initdb) — Git must keep this; switching back to
  initdb breaks server-side dry-run

immich workloads: immich-server, immich-microservices, immich-machine-learning, immich-redis
asset count:      ~7000 in prod (TBD count after the 2026-06-02 chmod fix backfill completes)
face_search rows: 0 (the BG-worker bug blocked all face-detect job writes for 7+ days
                     before resolution; user is currently triggering Face Detection in UI)
```

## Constraint A — source the replacement image (unresolved)

The Immich docs reference `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` — but that's a **docker-compose Postgres image, not a CNPG-flavored image**. CNPG needs a container that:
- Exposes Postgres on the CNPG conventions (entrypoint, exporters, signals)
- Is built on a `cloudnative-pg` base or replicates its surface
- Ships VectorChord pre-installed
- Optionally keeps pgvecto.rs side-by-side for the migration window (so we can read old indexes while building new ones)

Options ranked:

| # | Approach | Effort | Risk | Maintenance burden |
|---|---|---|---|---|
| 1 | **Use a community CNPG+VectorChord image** | Low | Medium (untested deps) | Low (if active) |
| 2 | **Build our own** under `images/cnpg-vectorchord/` | Medium | Low | Medium (we own bumps) |
| 3 | **Skip CNPG, run vanilla immich-app/postgres** with a StatefulSet | High | High (lose CNPG features: backups, failover, monitoring) | Low long-term |
| 4 | **Defer** — stay on pgvecto.rs until forced | None | Increases over time | None now |

**Recommended: 1 with a fallback to 2.** Spend an hour searching for an actively-maintained community image first; if nothing meets the bar, build our own from a small Dockerfile on top of the official `cloudnative-pg` base.

Search hits to evaluate before deciding (operator task before Phase 1):
- `cloudnative-pg-i` ecosystem (the CNPG-Image initiative)
- `cnpg-i-machinery` example builds
- Search GitHub for `cloudnative-pg` + `vchord.so` + `Dockerfile`
- Tensorchord roadmap for whether they plan a successor to `cloudnative-pgvecto.rs`

If we build our own, the Dockerfile is small:

```dockerfile
ARG PG_MAJOR=16
FROM ghcr.io/cloudnative-pg/postgresql:${PG_MAJOR}-bookworm
USER root
# Install VectorChord
ARG VCHORD_VERSION=0.4.3
RUN apt-get update && apt-get install -y curl ca-certificates \
 && curl -fL -o /tmp/vchord.deb "https://github.com/tensorchord/VectorChord/releases/download/v${VCHORD_VERSION}/postgresql-${PG_MAJOR}-vchord_${VCHORD_VERSION}-1_amd64.deb" \
 && apt-get install -y /tmp/vchord.deb \
 && rm /tmp/vchord.deb \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
USER postgres
```

This goes under `images/cnpg-vectorchord/` with a `build-cnpg-vectorchord.yml` GHA workflow that mirrors the `build-immich-photos-backup.yml` pattern.

## Phased approach

Cluster-database migrations of this shape have repeatedly burned us when done in one shot. Stage first, soak, then promote.

### Phase 0 — image sourcing (operator + agent, ~1 day)

Resolve Constraint A. Either pick a community image (and inspect its Dockerfile) or land a `images/cnpg-vectorchord/` build chain. Verifies: `docker run --rm <image> postgres -V && cat /usr/share/postgresql/16/extension/vchord.control`.

Exit: image digest pinned in a PR draft, ready to consume in Phase 1.

### Phase 1 — staging migration (agent-driven, ~30 min active + 1 week soak)

The staging cluster has `bootstrap.recovery` (rebuilt during the 2026-04-18 DR drill). We cannot bootstrap a fresh `initdb` against the same `immich-db-staging-cnpg-v1` name without breaking server-side dry-run. Pattern: bump the cluster name (`-v2`) so initdb is permitted.

1. **PR 1 — `apps/staging/immich/database.yaml`**:
   - New cluster: `immich-db-staging-cnpg-v2`
   - `imageName` → new VectorChord image
   - `shared_preload_libraries: [vchord.so]` (replace `vectors.so`)
   - Remove the `search_path: '"$user", public, vectors'` parameter (revert to default)
   - `bootstrap.initdb` (not recovery) — staging gets a clean slate; we accept losing existing staging assets
   - `postInitSQL` switches `CREATE EXTENSION vchord CASCADE` (replaces `CREATE EXTENSION vectors`)
   - Old cluster `immich-db-staging-cnpg-v1` stays in Git until Phase 1 verified clean, then PR 1.1 removes it
2. Apply, wait for Flux reconcile, watch CNPG pods boot.
3. Update Immich staging `apps/staging/immich/` secret/configmap pointing the DB host at the new cluster service name.
4. Restart `immich-server` staging. Watch startup logs for "Reindexing clip_index" / "Reindexing face_index" / "Finished running migrations" — same shape as the 2026-06-02 prod recovery.
5. Trigger Face Detection + Smart Search → Missing in the staging UI. Wait for jobs to complete; verify a face-match query returns results.
6. **Soak period: 7 days**. During soak, validate (a) no pgvecto.rs deprecation warnings, (b) no BG-worker errors in PG logs, (c) face match + smart search return expected results, (d) WAL archiving + base backups work against the new cluster.

Exit: staging is green for 7 consecutive days.

### Phase 2 — production migration prep (agent + operator, ~30 min)

Before touching prod:

1. **Backup verification**: ensure the most recent Barman base backup of `immich-db-prod-cnpg-v3` is restorable. Test by spinning up a `v4-restore-test` cluster from the backup in a temp namespace, query a few rows, tear down. (Same pattern as the 2026-04-18 DR drill.)
2. **Export face/smart-search embeddings** to S3 as a belt-and-suspenders. Even though Immich can regenerate them, regeneration takes hours of ML compute and Face Detection has to re-classify every face. Concrete:
   ```sql
   COPY (SELECT * FROM face_search) TO '/path/face_search.csv' WITH (FORMAT CSV, HEADER);
   COPY (SELECT * FROM smart_search) TO '/path/smart_search.csv' WITH (FORMAT CSV, HEADER);
   ```
   Push to S3 alongside the WAL archive.
3. **Communicate downtime window**. Production cutover involves an Immich restart; expect 5-15 min of unavailability.

Exit: backups verified, embeddings exported, change-window scheduled.

### Phase 3 — production cutover (agent-driven, ~45 min)

Two viable cutover shapes; pick one based on the staging experience.

#### Option 3A — fresh cluster (recommended; matches staging)

Same shape as Phase 1, against prod. New cluster `immich-db-prod-cnpg-v4` with `bootstrap.initdb`. Immich repoints; embeddings regenerate from the assets that already exist on the NFS PV (no asset re-upload).

Cost: ~2-6 hours of CPU-bound Face Detection + Smart Search jobs after cutover, depending on asset count.

#### Option 3B — in-place extension swap (faster, riskier)

If a community / custom image bundles **both** pgvecto.rs AND VectorChord:
1. Restart cluster on the new image.
2. `CREATE EXTENSION vchord; DROP EXTENSION vectors CASCADE;` (drops face_index + clip_index)
3. Restart immich-server → bootstrap migration creates new VectorChord indexes.
4. Embeddings preserved in `face_search` + `smart_search` rows; only indexes regenerated.

Cost: ~20 min reindex, no embedding regeneration.

Risk: relies on the image supporting both extensions simultaneously (most CNPG-VectorChord builds may not).

**Default to 3A** unless Phase 0 image selection makes 3B trivially safe.

### Phase 4 — verify + cleanup (~1 day)

- 24-hour soak of prod
- WAL archiving healthy (no `barman-cloud-check-wal-archive` errors)
- Face Detection + Smart Search returning results
- No DEPRECATION WARNING in immich-server logs
- Then: remove the old `immich-db-prod-cnpg-v3` cluster from Git, tear down its PVCs (Retain reclaim leaves the data on disk as a manual rollback parachute — destroy after 7 more days)

## Risk + rollback

| Risk | Mitigation | Rollback |
|---|---|---|
| VectorChord index format incompatible with embeddings exported from pgvecto.rs | Phase 2 export is a belt-and-suspenders; primary recovery is Immich regenerating from assets | Restore from Phase 2 base backup + revert PR; takes ~30 min |
| CNPG image bug — pods crashloop | Phase 1 staging soak catches | Revert image, keep old cluster live |
| Asset NFS PV breaks during cutover | Unrelated; we don't touch the NFS PV in this plan | N/A |
| Performance regression (VectorChord slower than pgvecto.rs on our workload) | VectorChord is widely benchmarked faster; if regression, tune VectorChord HNSW params or revert | Same rollback path |
| Embedding-data loss during cutover | Phase 2 export + Phase 4 retention of old PVCs for 7d | Restore CSV exports into a fresh pgvecto.rs cluster |

## Out of scope

- **PG major version upgrade**. We stay on PG 16 throughout. PG 17 + VectorChord is a separate follow-up plan.
- **Sharding/scaling**. Single primary + 2 replicas, same as today.
- **Other apps with vector data**. None today; this is Immich-only.
- **Replacing Barman Cloud / WAL archiving**. Stays as-is.

## PRs (this plan)

| # | What | When |
|---|---|---|
| 1 | This plan doc | Now (PR opens with this commit) |
| 2 | `images/cnpg-vectorchord/` Dockerfile + GHA build workflow | Phase 0 (only if we build our own) |
| 3 | `apps/staging/immich/database.yaml` v1 → v2 (new cluster, VectorChord) | Phase 1 |
| 4 | `apps/staging/immich/database.yaml` remove old v1 cluster (post-soak) | Phase 1 cleanup |
| 5 | `apps/production/immich/database.yaml` v3 → v4 (new cluster, VectorChord) | Phase 3 |
| 6 | `apps/production/immich/database.yaml` remove old v3 cluster | Phase 4 cleanup |

## Open questions

1. **Image source**: community CNPG-VectorChord vs. build our own? (Phase 0 deliverable.)
2. **Cutover shape**: 3A (fresh cluster, regenerate embeddings) or 3B (in-place extension swap)? (Decided after Phase 0 + staging experience.)
3. **Timing**: prod cutover during a low-traffic window (weekend morning)? Or coordinate around someone's Immich usage pattern?
4. **Snapshot consideration**: should we ZFS-snapshot the staging iSCSI volume before Phase 1, in case Flux ordering surprises us? (Cheap; recommended.)
