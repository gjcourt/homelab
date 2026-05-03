---
status: in-progress
last_modified: 2026-05-03
---

# Navidrome → Mopidy → Snapcast → HifiBerry Integration Plan

Play music from the Navidrome library through the Snapcast server to the HifiBerry
devices in the kitchen and living room, with room-by-room or fully-synchronized
multi-room playback.

## Current State

| Component | Status |
|:----------|:-------|
| Navidrome | Running in `navidrome-prod`, music at `music.burntbytes.com` |
| Snapcast server | Running in `snapcast-prod`, go-librespot sidecar (Spotify only) |
| `kitchen` (10.42.2.38) | go-librespot + `extension_snapcast` client → Snapcast server |
| `living-room` (10.42.2.39) | go-librespot + `extension_snapcast` client → Snapcast server |
| Snapcast LB | IP assigned from `home-c-pool`, ports 1704/1705/1780 |
| Navidrome Subsonic API | Enabled at `music.burntbytes.com/rest/` |

There is no direct path from Navidrome to the HifiBerries today. Spotify Connect works
(via go-librespot on the HifiBerries and the Snapcast pod), but Navidrome playback
requires a Subsonic client on a phone or desktop — audio plays on that device, not
on the HifiBerries.

## Target State

```
┌─────────────────────┐   Subsonic API    ┌─────────────────────────────────────────────┐
│  Navidrome          │◄─────────────────►│  Snapcast pod (snapcast-prod)               │
│  music.burntbytes   │                   │                                             │
│  .com               │                   │  ┌──────────────┐  ┌──────────────────────┐ │
└─────────────────────┘                   │  │  snapserver  │  │  mopidy sidecar      │ │
                                          │  │              │◄─│  (mopidy-subidy)     │ │
┌─────────────────────┐                   │  │  streams:    │  │                      │ │
│  MPD client         │   MPD (6600/TCP)  │  │  - spotify   │  │  reads Navidrome     │ │
│  (Symfonium /       │──────────────────►│  │  - navidrome │  │  library via         │ │
│   MALP / ncmpcpp)   │                   │  │              │  │  Subsonic API        │ │
└─────────────────────┘                   │  └──────┬───────┘  │  outputs PCM to      │ │
                                          │         │ TCP 1704 │  /audio/navidrome    │ │
                                          │         ▼          │  .fifo               │ │
                                          │  ┌──────────────┐  └──────────────────────┘ │
                                          │  │  snapclients │                           │
                                          │  │  kitchen     │                           │
                                          │  │  living-room │                           │
                                          │  └──────────────┘                           │
                                          └─────────────────────────────────────────────┘
```

**Control:** An MPD-compatible client (Symfonium on Android, MALP on Android, ncmpcpp
on desktop) connects to Mopidy on port 6600. Browse the Navidrome library, queue tracks,
control playback. Audio is sent as PCM into a new Snapcast stream named `navidrome`.

**Multi-room:** Via Snapweb (`snapcast.burntbytes.com`), assign each HifiBerry client to
the `navidrome` stream to hear perfectly time-synchronized audio in all rooms. Or keep
them on separate streams for independent control.

---

## Components Required

### Mopidy

[Mopidy](https://mopidy.com) is a music server that implements the MPD protocol and can
be extended with backend plugins. It will run as a sidecar in the Snapcast pod.

**Plugins needed:**
- [`mopidy-subidy`](https://github.com/Prior99/mopidy-subidy) — Subsonic API backend
  (works with Navidrome's OpenSubsonic API)
- [`mopidy-mpd`](https://mopidy-mpd.readthedocs.io/) — MPD server frontend (included with
  Mopidy core)

**Audio output:** Mopidy's ALSA output is replaced with a file/FIFO output backend. The
raw PCM is written to `/audio/navidrome.fifo` and Snapcast reads it as a stream source.

> **Image:** There is no official Mopidy multi-arch Docker image. A custom image will
> need to be built and pushed to `gjcourt/mopidy`. See the build section below.

### Secrets

Mopidy needs Navidrome Subsonic credentials. These will go in a new SOPS secret at
`apps/base/snapcast/secret-navidrome-credentials.yaml`.

```yaml
# apps/base/snapcast/secret-navidrome-credentials.yaml (template — SOPS-encrypted in git)
apiVersion: v1
kind: Secret
metadata:
  name: navidrome-credentials
  namespace: snapcast
type: Opaque
stringData:
  NAVIDROME_URL: "https://music.burntbytes.com"
  NAVIDROME_USER: "PLACEHOLDER"
  NAVIDROME_PASSWORD: "PLACEHOLDER"
```

---

## Implementation Phases

### Phase 1: Build the Mopidy Docker image

Create `images/mopidy/Dockerfile`:

```dockerfile
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-tools \
    libcairo2 \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    mopidy==3.4.2 \
    mopidy-mpd==3.3.0 \
    mopidy-subidy==0.9.1 \
    mopidy-local==3.2.1

EXPOSE 6600

ENTRYPOINT ["/usr/local/bin/mopidy"]
```

Build and push multi-arch (matching the Snapcast pod's ARM64 node):

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t gjcourt/mopidy:3.4.2-1 \
  --push \
  images/mopidy
```

### Phase 2: Add Mopidy ConfigMap

Create `apps/base/snapcast/configmap-mopidy.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mopidy-config
  namespace: snapcast
  labels:
    app: snapcast
data:
  mopidy.conf: |
    [core]
    data_dir = /mopidy-state
    cache_dir = /tmp/mopidy-cache

    [logging]
    verbosity = 0

    [mpd]
    enabled = true
    hostname = ::
    port = 6600

    [subidy]
    url = ${NAVIDROME_URL}
    username = ${NAVIDROME_USER}
    password = ${NAVIDROME_PASSWORD}
    # Navidrome supports API version 1.16.1 (OpenSubsonic)
    api_version = 1.16.1

    [file]
    enabled = false

    [local]
    enabled = false

    [audio]
    # Write raw PCM to the shared FIFO for Snapcast
    output = filesink location=/audio/navidrome.fifo
```

> **Note:** `filesink` is a GStreamer element that writes raw bytes to a file or named
> pipe. For Mopidy to write PCM (not the GStreamer container format), the full pipeline
> must specify the format explicitly. The actual pipeline string is:
> `output = audioresample ! audioconvert ! audio/x-raw,rate=44100,channels=2,format=S16LE ! filesink location=/audio/navidrome.fifo`

### Phase 3: Add the Navidrome stream to Snapcast config

Edit `apps/base/snapcast/configmap.yaml` — add the `navidrome` stream source:

```yaml
source = pipe:///audio/navidrome.fifo?name=navidrome&sampleformat=44100:16:2&codec=flac&mode=read
```

### Phase 4: Add an init-container for the navidrome FIFO

In `apps/base/snapcast/deployment.yaml`, add a new init-container after `init-spotify-fifo`:

```yaml
- name: init-navidrome-fifo
  image: busybox:1.37
  securityContext:
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
  command:
    - sh
    - -c
    - |
      set -e
      rm -f /audio/navidrome.fifo
      mkfifo /audio/navidrome.fifo
      chmod 666 /audio/navidrome.fifo
  volumeMounts:
    - name: audio-pipes
      mountPath: /audio
```

### Phase 5: Add the Mopidy sidecar container

In `apps/base/snapcast/deployment.yaml`, add to the `containers` list:

```yaml
- name: mopidy
  image: gjcourt/mopidy:3.4.2-1
  securityContext:
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
  env:
    - name: NAVIDROME_URL
      valueFrom:
        secretKeyRef:
          name: navidrome-credentials
          key: NAVIDROME_URL
    - name: NAVIDROME_USER
      valueFrom:
        secretKeyRef:
          name: navidrome-credentials
          key: NAVIDROME_USER
    - name: NAVIDROME_PASSWORD
      valueFrom:
        secretKeyRef:
          name: navidrome-credentials
          key: NAVIDROME_PASSWORD
    - name: HOME
      value: /mopidy-state
  ports:
    - name: mpd
      containerPort: 6600
  command:
    - sh
    - -c
    - |
      # Substitute env vars into config before starting
      envsubst < /etc/mopidy/mopidy.conf > /tmp/mopidy.conf
      exec mopidy --config /tmp/mopidy.conf
  readinessProbe:
    tcpSocket:
      port: mpd
    initialDelaySeconds: 10
    periodSeconds: 5
    timeoutSeconds: 2
    failureThreshold: 6
  volumeMounts:
    - name: mopidy-config
      mountPath: /etc/mopidy/mopidy.conf
      subPath: mopidy.conf
      readOnly: true
    - name: mopidy-state
      mountPath: /mopidy-state
    - name: audio-pipes
      mountPath: /audio
  resources:
    requests:
      cpu: 25m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

Add to `volumes`:

```yaml
- name: mopidy-config
  configMap:
    name: mopidy-config
- name: mopidy-state
  persistentVolumeClaim:
    claimName: snapcast-mopidy-state
```

### Phase 6: Add PVC for Mopidy state

In `apps/base/snapcast/storage.yaml`, add:

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: snapcast-mopidy-state
  namespace: snapcast
  labels:
    app: snapcast
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: synology-iscsi
  resources:
    requests:
      storage: 1Gi
```

### Phase 7: Expose the MPD port

In `apps/base/snapcast/service.yaml`, add:

```yaml
- name: mpd
  port: 6600
  targetPort: 6600
  protocol: TCP
```

### Phase 8: Add SOPS secret & encrypt

```bash
# Fill in real credentials, then encrypt
sops -e -i apps/base/snapcast/secret-navidrome-credentials.yaml
```

Add to `apps/base/snapcast/kustomization.yaml`:

```yaml
resources:
  - configmap.yaml
  - configmap-go-librespot.yaml
  - configmap-mopidy.yaml
  - deployment.yaml
  - namespace.yaml
  - secret-navidrome-credentials.yaml
  - service.yaml
  - storage.yaml
```

---

## Connecting a Client

### Android: Symfonium (recommended)

1. Install [Symfonium](https://symfonium.app/)
2. Add media server → **MPD** → host: Snapcast LB IP, port: `6600`
3. Browse library, queue tracks → audio plays on whichever HifiBerry is assigned to
   the `navidrome` Snapcast stream
4. Use Snapweb (`snapcast.burntbytes.com`) to control which rooms are listening

### Android: M.A.L.P.

1. Add profile: type MPD, host: Snapcast LB IP, port 6600
2. Full MPD control — play, queue, browse, volume

### Desktop: ncmpcpp

```bash
ncmpcpp -h <snapcast-lb-ip> -p 6600
```

### iOS: MPDRemote / Rigelian

Connect to Snapcast LB IP on port 6600.

---

## Checklist

- [ ] Build and push `gjcourt/mopidy:3.4.2-1` (multi-arch)
- [ ] Create and SOPS-encrypt `secret-navidrome-credentials.yaml`
- [ ] Add `init-navidrome-fifo` init-container
- [ ] Add `mopidy` sidecar container
- [ ] Add `navidrome` stream source to `snapserver.conf`
- [ ] Add `snapcast-mopidy-state` PVC to `storage.yaml`
- [ ] Expose MPD port 6600 in `service.yaml`
- [ ] Update `kustomization.yaml`
- [ ] Validate: `kubectl kustomize apps/staging/snapcast`
- [ ] Deploy to staging and test with ncmpcpp
- [ ] Verify audio on one HifiBerry before promoting to production
- [ ] Update `docs/operations/apps/snapcast.md` to document the new stream

---

## Notes

- Mopidy's `mopidy-subidy` requires Navidrome's Subsonic API to be enabled, which it is
  by default. The API version `1.16.1` is correct for Navidrome's OpenSubsonic dialect.
- The `envsubst` step in the `command` is needed because Kubernetes `ConfigMap` values
  cannot directly reference `secretKeyRef`. An alternative is to use a Helm template or
  a dedicated init-container to render the config. Ensure `envsubst` is in the Mopidy
  image, or use a sed-based substitution instead.
- Mopidy's GStreamer pipeline must produce raw PCM at 44100 Hz, 16-bit, stereo to match
  the Snapcast stream source definition. Test with:
  ```bash
  kubectl exec -n snapcast-prod deploy/snapcast -c mopidy -- \
    mopidy --version
  ```
- If the Navidrome URL changes or credentials rotate, only the SOPS secret needs updating
  — no manifest changes required.

---

## Survey 2026-05-03

**Current state:** Not started. Navidrome and Snapcast both run in production (`apps/base/navidrome/`, `apps/base/snapcast/`), but Mopidy is entirely absent — `grep -r mopidy apps/` returns nothing, no init container for the FIFO, no sidecar, no config map, no PVC. All 8 implementation checklist items in the plan are outstanding.

**Outstanding next steps (per the plan):**

1. Build and push `gjcourt/mopidy:3.4.2-1` multi-arch image (Phase 1).
2. SOPS-encrypt `apps/base/snapcast/secret-navidrome-credentials.yaml`.
3. Add the `init-navidrome-fifo` initContainer to `apps/base/snapcast/deployment.yaml`.
4. Add the Mopidy sidecar container with all env vars + volume mounts.
5. Add the Navidrome stream source to the snapcast ConfigMap.
6. Add `snapcast-mopidy-state` PVC (1Gi, synology-iscsi) to `apps/base/snapcast/storage.yaml`.
7. Expose MPD port 6600 in `apps/base/snapcast/service.yaml`.
8. Validate with `kustomize build apps/staging/snapcast`, deploy, smoke-test with an MPD client (e.g., `ncmpcpp -h 10.42.2.X -p 6600`).
