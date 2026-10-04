# Wyoming Piper (local text-to-speech)

## 1. Overview
Piper text-to-speech served over the Wyoming protocol, so Home Assistant can speak
without a cloud TTS service. CPU only — the cluster has no GPU. Phase 1 of lab brief
[03-036](https://github.com/gjcourt/lab/blob/main/03-homelab-automation/03-036-local-tts.md):
start with a stock voice; a voice trained on George's own recordings may replace it later.

## 2. Architecture
- **Image**: `rhasspy/wyoming-piper` (source: [OHF-Voice/wyoming-piper](https://github.com/OHF-Voice/wyoming-piper)), pinned by tag and digest. It wraps the `piper-tts` engine from [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl).
- **Namespaces**: `wyoming-piper-prod`, `wyoming-piper-stage`. One replica, `Recreate` strategy.
- **Storage**: none persistent. Voices download from Hugging Face into an `emptyDir` at `/data` on first start (~63 MB per medium voice) and again after a reschedule. No iSCSI dependency.
- **Security**: the upstream image runs as root; here it runs as UID 65534 with a read-only root filesystem and all capabilities dropped. Verified locally against 2.5.2 before deployment: it writes only the voice files under `--data-dir`.
- **Networking**: `ClusterIP` service on 10200, no HTTPRoute. A `CiliumNetworkPolicy` admits only Home Assistant pods in the same environment, and egress is limited to DNS plus `huggingface.co` / `**.hf.co` on 443 (`**.` because the download redirect lands on a multi-label host such as `us.aws.cdn.hf.co`, which Cilium's single-label `*.` would not match). Each Home Assistant overlay adds the matching egress rule to its own policy.

## 3. Endpoints
- **Production**: `wyoming-piper.wyoming-piper-prod.svc.cluster.local:10200`
- **Staging**: `wyoming-piper.wyoming-piper-stage.svc.cluster.local:10200`

## 4. Configuration
- **Default voice**: `en_US-lessac-medium` (the `--voice` arg in `apps/base/wyoming-piper/deployment.yaml`). This is a placeholder until the 03-036 audition picks one. Change the default with that arg; Home Assistant can also request any other voice per call, which downloads on first use.
- **Voice licences** vary per voice (see each voice's `MODEL_CARD` in `rhasspy/piper-voices`). Several, including `en_US-ryan-*` and `en_US-hfc_*`, are non-commercial only. The default `en_US-lessac-medium` is trained on the Lessac Blizzard 2013 data, which CSTR distributes under a research licence. Fine for household use; check the card before using any voice elsewhere.
- **Resources**: request 100m / 512Mi, limit 2 CPU / 1Gi. Measured about 400 MB RSS with one medium voice loaded.

## 5. Home Assistant setup (one-time, UI)
The Wyoming integration is a config-flow integration and can't be set up in YAML.
1. Settings → Devices & services → Add integration → **Wyoming Protocol**.
2. Host: the endpoint for the environment (above). Port: `10200`.
3. Settings → Voice assistants → set **Piper** as the text-to-speech engine for the pipeline, and pick the voice.
4. Test it: Developer tools → Actions → `tts.speak` on a media player, or "Try voice" in the pipeline editor.

## 6. Testing
- Pod ready: `kubectl -n wyoming-piper-prod get pods`
- Server answering: the liveness probe runs the image's own check (`python -m wyoming_piper.health_check`, a Wyoming Describe/Info round trip). Recent failures show in `kubectl -n wyoming-piper-prod describe pod -l app=wyoming-piper`.
- Logs show the voice download and `Ready`: `kubectl -n wyoming-piper-prod logs deploy/wyoming-piper`

## 7. Performance (measured 2026-10-02)
Measured on a Ryzen 5 PRO 4650GE node with a 4-CPU quota, while sharing the CPU with other work:

| Measurement | Result |
|---|---|
| 23-second paragraph | 3.0–6.5 s to synthesize (real-time factor 0.13–0.28) |
| First audio, warm | ~1.1 s |
| First request after start | ~4 s, because it also loads the model |

Re-measure in-cluster once deployed.

## 8. Disaster recovery
Stateless. Deleting the pod re-downloads the default voice on start.

## 9. Troubleshooting
- **Stuck not-ready on first start**: the voice download is failing. Check the logs for the Hugging Face URL. If Hugging Face's download redirect moves off `hf.co`, the `toFQDNs` rule in `networkpolicy.yaml` needs the new host.
- **HA can't connect**: confirm Home Assistant's own CiliumNetworkPolicy has the `wyoming-piper-<env>` egress rule (patched in `apps/<env>/homeassistant/kustomization.yaml`), and that the host typed into the integration matches the environment.
- **Restarts during long renders**: synthesis runs on the server's event loop, so a very long text can starve the health check. Liveness only fails after about 2 minutes of failures. If it still restarts, split the text into smaller requests.
