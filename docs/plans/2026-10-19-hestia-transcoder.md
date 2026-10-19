---
status: planned
last_modified: 2026-10-19
summary: "Move x265 encoding from winpc to a hestia Custom App running 3-4 encodes in parallel; winpc only rips and hands off"
---

# Plan: encode on hestia, rip on winpc

## Context

Every disc goes through `hosts/winpc-5600x/media-pipeline/transcode.ps1`. It
encodes one title at a time on winpc's Ryzen 5 5600X, gates it, pushes it to
hestia and records each step in the shared ledger. The pipeline is sound, but
it is slow. The 2026-10-17 batch had 81 HD titles, about 27.5 h of source, and
took more than a day of wall time. A low-disk abort on 10-18 then stalled it
until someone cleared space.

hestia's EPYC 8324P sits about 80% idle and already holds the library. A
benchmark on 2026-10-18 tested whether it should do the encoding. It should,
but only by running encodes in parallel. A single encode is no faster there.

## Measured, not assumed (2026-10-18/19)

Both machines ran the same x265 build, **4.2+37-b81f650e**. winpc uses the
BtbN win64 build from 2026-07-22. hestia used the BtbN linux64 build from
`autobuild-2026-07-31-14-10`, sha256-verified. The command was the one
`transcode.ps1` builds today:
`-map 0:v:0 -map 0:a -c:v libx265 -b:v 4700k`, plus AAC for every audio track
at its own channel count. The test title was *Her* (2013): 1080p, 125.8 min,
four 5.1 tracks, 28.9 GB raw. Both hestia runs used `nice -n 19`.

| Measurement | Result |
|---|---|
| hestia, one full encode | 7,661 s wall = **0.99x realtime**; load average about 11–12 of 64 threads |
| hestia, three 10-min slices concurrently | 480 s wall for 1,800 s of source = **3.75x aggregate**; peak load 46 |
| winpc, same kind of title (from `all-hd2.log`) | about **1.0–1.4x** realtime, one title at a time |
| Raw copy winpc → hestia, Wi-Fi (433 Mbps link) | 15.9 min, 242 Mbps |
| Raw copy winpc → hestia, wired Realtek GbE | **4.1 min, 938 Mbps**, byte-identical (`cmp`) |

**What this means:**
- x265 does not scale one 1080p encode across 64 threads. Each Zen 4c core is
  also slower than a 5600X core, so **parallelism is the whole win**.
- Three slices running together each went *faster* (1.25x) than the lone
  encode. The lone run was partly slowed by a 29 GB `cmp` reading the disk at
  the same time.
- A peak load of 46/64 means four concurrent encodes should fit. Five would
  need measuring.
- Since winpc moved to wired GbE (10-19), the hand-off costs about 4 min per
  feature against about 2 h of encoding. **The network is not the bottleneck.**
  10 GbE for winpc is not needed: there is no free PCIe slot, and its USB
  ports are 5 Gbps.

**Not measured:** how hestia's other jobs behave while it encodes. That means
NFS latency for Jellyfin and Immich, ARC pressure, and the GHA runner.
Phase 2 has to measure this before cut-over.

## Goal

- winpc **rips and hands off**. hestia **encodes, gates, lands and records**.
- **Throughput:** at least 3x today's titles per hour, without degrading
  hestia's other services.
- **Every guard `transcode.ps1` has today is kept, and none are weakened:**
  - the frame-count gate
  - duration, bitrate and real-frame validation
  - the audio-track gate
  - the subtitle mux after encode
  - `.incoming` with hash-then-rename
  - `-Replace` semantics
  - ledger rows
  - verify-before-delete
- **Same output:** same encoder, same flags, same library layout and names.
  Jellyfin must not be able to tell which machine encoded a title.

## Non-goals

- Changing encoder settings, codec or bitrate. That is a separate decision,
  and the benchmark only holds while the settings stay the same.
- Ripping on hestia. The optical drive, MakeMKV and `rescue-disc.ps1` stay on
  winpc.
- GPU encoding. hestia has no GPU (sold 2026-05), and NVENC-class quality per
  bit is lower anyway.
- The CD path (`import-music.ps1`). It is already copy-only and fast.

## Design

### Shape

```
winpc                                   hestia
─────                                   ──────
MakeMKV / rescue-disc.ps1
  ↓ C:\Video\<disc>\*.mkv
submit.ps1 (queue.tsv rows)  ──scp──▶   /mnt/main/media-work/inbox/<job>/
  ledger: SUBMITTED (src sha256)          source.mkv  +  job.json  (written LAST)
                                        transcoder app (N=3 slots, nice 19)
                                          preflight → encode → gates → subtitle mux
                                          → <lib>/<name>.mkv.incoming → sha256 → rename
                                          ledger: ENCODED, LANDED  (same ledger.tsv)
                                          inbox/<job> → done/ (or failed/ with reason)
verify-before-delete.ps1 -Mode Video    ◀── proves from LANDED rows, as today
```

### hestia side: `hosts/hestia/transcoder/`

- **A TrueNAS Custom App**, the same pattern as `qbittorrent/` and
  `libation/`. It is deployed by `deploy-hestia.yml` and its compose image is
  pinned by digest.
- **The image is `ghcr.io/gjcourt/transcoder`.** It holds a Python 3 worker,
  stdlib plus `ffprobe -of json`, and the **BtbN static ffmpeg pinned by
  release tag and sha256** in the Dockerfile, so x265 is reproducible. Renovate
  tracks the tag. A bump changes encoder output, so it gets a shadow run
  (Phase 2) before it merges, and it is never automerged.
- **Why Python and not PowerShell 7 on Linux:**
  - About half of `transcode.ps1`'s guards exist only for Windows quirks: the
    OEM codepage, Win32-OpenSSH stdout hangs, `Invoke-SshRead`, native stderr
    under `Stop`.
  - The worker runs on the same box as the library, so the remote-read
    machinery disappears entirely.
  - Python keeps the gates small and testable.
- **Mounts:**
  - `/mnt/main/media-work`: a new `main/media-work` dataset, read-write,
    **excluded from snapshots** because it is scratch. It is outside
    `family/media/music`, so the alcatraz pull never sees it.
  - `/mnt/main/family/media/video`: read-write, the library.
  - `/mnt/main/archive/_inventory/rips`: the ledger.
- **Limits:**
  - **Encode slots:** `SLOTS=3` to start, raised to 4 only after Phase 2
    measures it.
  - **CPU:** compose `cpus: 48` caps the encoder at three-quarters of the box,
    whatever the slot count.
  - **Priority:** each ffmpeg runs under `nice -n 19` and the idle I/O class
    (`ionice -c3`).
  - **Memory:** `mem_limit: 24g`. x265 at 1080p uses a few GB per encode, and
    hestia's 188 GB is mostly ARC.

### Job hand-off, atomic and verifiable

- `submit.ps1` replaces the encode loop on winpc. It reads the **same
  `queue.tsv`** (the queue stays data) and runs the **same preflight
  reporting**. For each row it:
  1. `scp`s the source into `inbox/<job>.partial/source.mkv`.
  2. Hashes the source **on hestia** and compares it against winpc's sha256.
  3. Writes `job.json` with the name, library dir, bitrate, `MinKbps`,
     `Replace`, the source sha256 and the source's original winpc path.
  4. Renames `<job>.partial` to `<job>`.

  The worker only ever sees complete jobs. That is the same idea as
  `rescue-disc.ps1`'s `.partial` folder.
- **The ledger gains one event: `SUBMITTED`.** It carries `Src` (the winpc
  path), `Sha` and `Job`. Every later row for that job carries the same `Src`,
  so `verify-before-delete.ps1`'s key ("the `LANDED` row's `Src`") keeps
  working unchanged.
- **Job ids are the ledger run id plus the row index,** so a resubmitted title
  is a new job. `-Replace` is still required to overwrite a library file.

### Worker loop, a port of today's stages

1. **Claim:** rename `inbox/<job>` to `work/<job>`. The rename is atomic, so
   two slots can never take the same job.
2. **Preflight:** `ffprobe` the source and reproduce the per-stream audio
   bitrate rule (`AacBitrate`). It skips with `NO AUDIO` exactly as today.
3. **Encode:** the identical ffmpeg argument list, written to
   `work/<job>/out.va.mkv`.
4. **Gates, each ported as-is with its README rationale linked:**
   - frame count by packet count (the soft-telecine lesson)
   - duration delta within `DurTolPct`
   - bitrate at or above `MinKbps`
   - a real decoded frame of at least `MinFrameKB`
   - the audio-track gate: same track count and channel counts
5. **Subtitle mux:** after the encode, as today. The subtitle backpressure
   deadlock was a pipe-buffer problem, and the post-encode mux keeps it from
   coming back.
6. **Land:**
   - Write to `<lib>/<name>.mkv.incoming` in the **library** dataset. It is a
     different dataset from `media-work`, so this is a copy, not a rename.
   - Hash it there, then rename it onto the final name.
   - `LANDED` records the hash.
7. **Finish:**
   - **Success:** move the job to `done/` and delete its source.
   - **Any gate failure:** move it to `failed/<job>/` with `reason.txt`, keep
     the source, and write `VERIFY_FAIL`. Today's "keeping local" behaviour is
     unchanged; only the location moves.
8. **Restart safety:** on startup, anything still in `work/` goes back to
   `inbox/` and its partial outputs are discarded. Encodes are re-runnable;
   only `LANDED` is a commitment.

### When winpc's raw copy may be deleted: decision needed

| Option | winpc frees space when | Risk |
|---|---|---|
| **A: after `LANDED`** (today's proof, unchanged) | the title is encoded and landed | none new. But winpc holds every raw rip until hestia catches up, which is the space problem the 10-18 abort hit. |
| **B: after a verified hand-off** (`SUBMITTED` plus a re-hash of the inbox copy) | minutes after ripping | the raw exists **once**, on hestia's 8-disk raidz2 pool (last scrubbed 2026-09-20), until it lands. That is more durable than winpc's single SATA SSD, but it is still one copy, with no snapshot. |

**Recommendation: B.** It is the point of the change, and the only copy then
sits on the more durable disk. `verify-before-delete.ps1 -Mode Video` would
gain a second proof type: the `SUBMITTED` row's inbox file still exists and
still hashes to the recorded sha256. It re-checks the file, as the `LANDED`
proof does, rather than trusting the row.

### Observability

- **homelabscope:** `homelabscope_job_last_success_seconds{job="transcoder"}`
  is touched on each `LANDED`, plus a queue-depth gauge (inbox + work).
- **Alert:** a job that has sat in `work/` for more than 6 h, or a
  `failed/` directory that is not empty. Both use the existing email route.
- **Logs:** the worker logs to stdout (`docker logs transcoder`), one line per
  stage. ffmpeg `-stats` goes to a per-job log file, not stdout.

## Phases

| Phase | Work | Exit criterion |
|---|---|---|
| **0** | This plan, as a plan-only PR | George approves the design and the deletion option. |
| **1** | Image and worker. **Gate tests** run in CI on lavfi-synthesised clips: no audio, one track dropped, a stereo downmix, truncation, and soft-telecined 23.976-in-29.97. The compose ships with `x-deploy.archived: true`. | CI is green. Every gate rejects its bad clip and passes the good one. |
| **2** | **Shadow run.** Deploy the app, hand-submit 3–4 titles that winpc is also encoding, and compare the gate verdicts, durations and sizes. During one full-load night, record the NFS latency seen by Jellyfin and Immich pods, ARC hit rate and `node_load`. | Identical verdicts, sizes within 1%, and no measurable service impact at `SLOTS=3`. Then decide whether 4 slots is safe. |
| **3** | Cut over. `submit.ps1` becomes the default and `transcode.ps1` stays as `-Local` fallback. `verify-before-delete.ps1` gains the `SUBMITTED` proof if option B is chosen. Runbook updated. | One real disc batch lands entirely through hestia. |
| **4** | Retire the winpc encode loop after two clean batches. Keep `transcode.ps1` encode-only code for one release, then delete it. | `media-pipeline/README.md` describes the new flow. |

## Alternatives rejected

- **Faster winpc CPU.** A Ryzen 7 5700G (about $200) gives roughly 1.3x and
  would free the PCIe slot. It is still one title at a time, against about
  3.75x already sitting idle on hestia.
- **10 GbE for winpc.** It would save about 3 min per feature against roughly
  30 min of parallel encoding per feature. It would also need the GPU's only
  PCIe slot or an M.2 adapter. Not worth it.
- **Encoding in the Talos cluster.** The workers would read 30–40 GB sources
  and write over NFS, and would compete with workloads that have SLOs. hestia
  is where the bytes already are.
- **Running more ffmpeg processes on winpc.** Six cores are already saturated
  by one encode.

## Open questions for George

1. **Deletion timing:** option A or B?
2. **Slots:** start at 3, or go straight to 4 if Phase 2 shows no service
   impact?
3. **Inbox location:** a new `main/media-work` dataset excluded from
   snapshots, or an existing scratch dataset?
