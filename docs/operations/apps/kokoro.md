# Kokoro (local text-to-speech)

## 1. Overview
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) text-to-speech, served by
[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) with an OpenAI-compatible API, so Home
Assistant can speak with no cloud service. CPU only. This is the default TTS voice from lab brief
[03-036](https://github.com/gjcourt/lab/blob/main/03-homelab-automation/03-036-local-tts.md): the
voice `af_heart` came first in the 2026-10-02 blind audition of 13 stock voices.

## 2. Architecture
- **Image**: `ghcr.io/remsky/kokoro-fastapi-cpu`, pinned by tag and digest. Apache-2.0, and so are the Kokoro-82M weights.
- **Namespaces**: `kokoro-prod`, `kokoro-stage`. One replica, `Recreate` strategy.
- **Model**: baked into the image at build time. `DOWNLOAD_MODEL=false` skips the startup re-check, so the pod has **no internet egress** (DNS only).
- **Storage**: none persistent. `emptyDir` volumes at `/tmp` and `/app/api/temp_files`; the root filesystem is read-only.
- **Security**: UID 1000 (the image's `appuser`), all capabilities dropped. The web player and CORS are turned off because only Home Assistant calls it.
- **Networking**: `ClusterIP` on 8880, no HTTPRoute. A `CiliumNetworkPolicy` admits only Home Assistant pods in the same environment, and each Home Assistant overlay adds the matching egress rule.

## 3. Endpoints
- **Production**: `http://kokoro.kokoro-prod.svc.cluster.local:8880/v1`
- **Staging**: `http://kokoro.kokoro-stage.svc.cluster.local:8880/v1`
- API: `POST /v1/audio/speech` (OpenAI speech format) and `GET /v1/audio/voices`. Health: `GET /health`.

## 4. Configuration
- **Default voice**: `af_heart`, set by the `DEFAULT_VOICE` env in `apps/base/kokoro/deployment.yaml`. Home Assistant can request any other preset per call.
- **Threads**: `OMP_NUM_THREADS=3` matches PyTorch's thread pool to the 3-CPU limit, to avoid the throttling the audition ran into.
- **Resources**: request 500m / 1.5Gi, limit 3 CPU / 3Gi. These are estimates; re-measure after deploy and adjust.

## 5. Home Assistant setup (one-time)
Home Assistant's core OpenAI integration only talks to `api.openai.com`, so Kokoro goes through the
**OpenAI TTS** custom integration from HACS (`sfortis/openai_tts`), which accepts any OpenAI-compatible server.
1. HACS → search **OpenAI TTS** → download, then restart Home Assistant.
2. Settings → Devices & services → Add integration → **OpenAI TTS**.
3. Point it at the endpoint above for the environment, with model `kokoro` and voice `af_heart`. Any non-empty API key works; the server doesn't check it.
4. Settings → Voice assistants → choose it as the text-to-speech engine and try a phrase.

## 6. Testing
- Pod ready (the first start loads the model and runs a warm-up): `kubectl -n kokoro-prod get pods`
- Logs: `kubectl -n kokoro-prod logs deploy/kokoro`
- From Home Assistant: Developer tools → Actions → `tts.speak` on a media player.

## 7. Disaster recovery
Stateless; deleting the pod is safe.

## 8. Troubleshooting
- **Stuck not-ready**: the startup probe allows about 5 minutes for model load and warm-up. Check the logs for a load error.
- **CrashLoop writing a file**: something wrote outside `/tmp` or `/app/api/temp_files` on the read-only root. The log names the path; add an `emptyDir` for it.
- **HA can't connect**: confirm Home Assistant's CiliumNetworkPolicy has the `kokoro-<env>` egress rule (patched in `apps/<env>/homeassistant/kustomization.yaml`) and that the integration's URL matches the environment.
- **Slow speech**: check for CPU throttling in the pod metrics; raise the CPU limit and `OMP_NUM_THREADS` together.
