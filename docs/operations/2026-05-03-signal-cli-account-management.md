---
status: Stable
last_modified: 2026-05-03
---

# signal-cli — adding, removing, and inspecting accounts

How to attach signal-cli to a Signal phone number — either as a linked device (most common) or as the primary registration on a dedicated bot number. Plus what wiring needs to follow downstream so signal-bridge and hermes-bot pick the new account up.

Prereqs:
- `kubectl` access to the cluster.
- `signal-cli` is the production deployment in the `signal-cli` namespace, with the daemon container named `signal-cli` and the bridge sidecar named `signal-bridge`.
- For "link as secondary" flow, you need physical access to the phone whose number you're linking.
- For "register as primary," you need a number that's about to become a *dedicated* bot number — Signal will deactivate the existing app on that number's phone (if any).

## Inventory: what's already linked

```bash
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli listAccounts
```

Each line is one account. Newly linked accounts may take a few seconds to appear after the QR scan completes.

To see the file structure on the PVC:

```bash
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- \
  ls -la /var/lib/signal-cli/data/
```

Each `+1XXXXXXXXXX/` directory is one account's identity, contacts, and message store.

## Flow A — link signal-cli as a secondary device on an existing account

Use this when:
- A Signal account already exists on someone's phone (the "primary" device).
- You want signal-cli to receive copies of messages sent to/from that account, so the homelab bot can act on them.

The phone whose number you're linking does **not** lose anything — it stays primary. signal-cli becomes another linked device, just like Signal Desktop.

**Privacy note**: linked devices receive every message sent to or from the account, including end-to-end-encrypted ones (since linked devices have their own E2EE keys). Make sure the human whose number you're linking understands this before scanning.

### 1. Generate the link URI

> **Don't use `signal-cli link` as a separate process** while the daemon is running. The two processes compete for the same Signal WebSocket and the link drops with `Connection closed!` (exit code 3) after the URI is generated, leaving the URI dead before anyone can scan. Each failed attempt also burns a Signal-side rate-limit budget — after ~3-5 failures Signal will block further link attempts for 1–24 hours.
>
> **Use the daemon's existing JSON-RPC interface instead.** This piggybacks on the connection the daemon already holds, so there's no conflict and no per-attempt server-side artifact:

```bash
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- bash -c '
exec 3<>/dev/tcp/127.0.0.1/7583
printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"startLink\",\"params\":{\"deviceName\":\"homelab-bot\"}}" >&3
timeout 15 head -1 <&3
exec 3>&-
'
```

The daemon responds with one JSON line:

```json
{"jsonrpc":"2.0","result":{"deviceLinkUri":"sgnl://linkdevice?uuid=...&pub_key=..."},"id":1}
```

The daemon holds the link open server-side until it's scanned (typically 5+ min) or the daemon restarts. No further client-side action is needed for the link to complete — the daemon will receive the scan asynchronously and the new account appears in `listAccounts`.

### 2. Render as QR

On your workstation. UTF-8 block QR usually scans cleaner than ANSI256 from a phone camera:

```bash
qrencode -t UTF8 -m 2 'sgnl://linkdevice?uuid=...&pub_key=...'
```

If the terminal QR doesn't scan reliably (font hinting, brightness), save as a high-resolution PNG and `open` it:

```bash
qrencode -t PNG -s 12 -m 4 -o /tmp/signal-link.png 'sgnl://linkdevice?uuid=...'
open /tmp/signal-link.png
```

(`brew install qrencode` on macOS, `apt install qrencode` on Linux.)

### 3. Scan from the phone

On the phone whose number you're linking:

- Open Signal → Settings → **Linked devices** → **Link new device** → scan the QR with the phone's camera.
- Confirm the device name when prompted.

The daemon completes the link asynchronously; nothing on the kubectl side blocks. Watch for the new account to appear (next step).

### 4. Verify

Use the daemon JSON-RPC again to list accounts (no separate process needed):

```bash
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- bash -c '
exec 3<>/dev/tcp/127.0.0.1/7583
printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":99,\"method\":\"listAccounts\"}" >&3
timeout 3 cat <&3
exec 3>&-
'
```

To poll until the new account shows up (replace `1` with the expected pre-link count):

```bash
while true; do
  N=$(kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- bash -c '
exec 3<>/dev/tcp/127.0.0.1/7583
printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":99,\"method\":\"listAccounts\"}" >&3
timeout 3 cat <&3' 2>/dev/null | grep -oE '\+[0-9]+' | wc -l | tr -d ' ')
  echo "$(date) accounts: $N"
  [ "$N" -ge 2 ] && break
  sleep 8
done
```

Once the new account appears, restart the deployment so the daemon reloads its account list cleanly:

```bash
kubectl rollout restart deploy signal-cli -n signal-cli
kubectl rollout status deploy signal-cli -n signal-cli --timeout=2m
```

Also confirm signal-bridge can see the new account:

```bash
kubectl logs -n signal-cli deploy/signal-cli -c signal-bridge --tail=20
```

Look for the `accounts=[+1...]` line in the startup banner — it should now list both numbers.

## Flow B — register signal-cli as primary on a dedicated bot number

Use this when:
- The number is not currently a Signal account, OR
- The number is a dedicated bot number you're willing to deactivate from any phone running its Signal app.

**Side effect**: any phone with Signal installed under this number will be deactivated; the user has to re-register or move to a different number.

### 1. Get a Signal captcha token

Open https://signalcaptchas.org/registration/generate.html in a browser, complete the captcha, then right-click the "Open Signal" link and copy the URL. It looks like `signalcaptcha://05.signal-hcaptcha.5fad9...`.

### 2. Register

```bash
kubectl exec -it -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli -u +1XXXXXXXXXX register \
  --captcha 'signalcaptcha://...'
```

Signal sends an SMS to the number with a verification code. (Use `--voice` instead of just `register` to receive a phone call instead.)

### 3. Verify

```bash
kubectl exec -it -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli -u +1XXXXXXXXXX verify NNNNNN
```

Where `NNNNNN` is the 6-digit code. After verify, set a profile name:

```bash
kubectl exec -it -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli -u +1XXXXXXXXXX updateProfile \
  --given-name "homelab-bot"
```

Restart the deployment as in Flow A step 4 so the daemon picks up the new account.

## Post-link wiring

Linking signal-cli to a new account does **not** automatically expose it to hermes-bot. Two more pieces:

### 1. signal-bridge — extend the account allowlist

The bridge filters which accounts external clients (hermes-bot, others) can subscribe to.

`apps/base/signal-cli/deployment.yaml` — `signal-bridge` container env:

```yaml
- name: HERMES_ALLOWED_ACCOUNTS
  value: "+16179397251,+1<new-number>"
```

Open a PR. Once merged and Flux reconciles, `kubectl logs -n signal-cli deploy/signal-cli -c signal-bridge` should log `accounts=[...]` with both numbers.

### 2. hermes-bot — pick which account it listens on

Each hermes-bot Deployment listens on **one** Signal account (the upstream gateway is single-account). Two patterns:

**Pattern A — bot stays on the primary number; new account is just for someone else's awareness.**
No hermes-bot change. The new account flows through signal-bridge but no agent acts on it.

**Pattern B — second hermes-bot Deployment for the new account.**
Copy `apps/base/hermes/` to `apps/base/hermes-secondary/`, change `SIGNAL_ACCOUNT` to the new number, give it its own PVC + namespace overlay (`hermes-secondary-prod`). Higher engineering cost; only worth it if you actually want a second bot persona.

**Pattern C — single hermes-bot, multiple senders allowed.**
Default. Add the new sender's number to `SIGNAL_ALLOWED_USERS` (sender allowlist) on the existing hermes-bot. The single bot then accepts DMs from both senders to the primary account. This is the typical "spouse can use the bot" flow.

## Removing an account

To unlink a secondary device (back out of Flow A):

```bash
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli -u +1XXXXXXXXXX listDevices
# pick the device id for "homelab-bot"
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli -u +1XXXXXXXXXX removeDevice -d <id>
```

Or do it from the phone: Signal → Settings → Linked devices → swipe to remove.

To remove a primary registration (Flow B):

```bash
kubectl exec -n signal-cli deploy/signal-cli -c signal-cli -- \
  signal-cli --config /var/lib/signal-cli -u +1XXXXXXXXXX unregister
```

The data dir under `/var/lib/signal-cli/data/+1.../` stays on the PVC unless you also delete it manually. Keeping it around lets you re-link without re-registering.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `signal-cli link` exits with `Link request error: Connection closed!` (exit code 3) | A separate `signal-cli` process is fighting the daemon for the Signal WebSocket | Don't use `signal-cli link`; use the daemon's `startLink` JSON-RPC method (Flow A step 1). |
| Daemon `startLink` returns a URI but scan never completes | Most likely: too many recent failed link attempts, Signal-side rate limit | Wait 1-24 hours, then retry once with the JSON-RPC path. Any single attempt that ends in success doesn't count toward the limit. |
| Phone says "invalid response" or "QR code not recognized" | Scanning a stale QR (URI from a previous attempt that already errored out, or one that's been server-side invalidated) | Generate a fresh URI via JSON-RPC. The previous one is dead. |
| `register` returns `CAPTCHA_REQUIRED` | Captcha token expired (they're short-lived, ~10 min) | Generate a fresh one and retry. |
| `verify` returns `INVALID_CODE` | SMS didn't arrive, or used the wrong code | Re-`register` with a fresh captcha; the previous registration was abandoned. |
| signal-bridge logs `accounts=[+1XXX]` (only one) after linking | Bridge cached the account list at startup | `kubectl rollout restart deploy signal-cli -n signal-cli` |
| hermes-bot still doesn't see new sender's messages | `SIGNAL_ALLOWED_USERS` doesn't include the new sender | Add to `apps/base/hermes/configmap.yaml` and roll out |
| `Failed to read local accounts list` (CrashLoop) | PVC is empty (no accounts ever linked, e.g., `signal-cli-stage`) | Run Flow A or B against that environment |

## Cross-references

- Plan: [`2026-05-02-signal-cli-hermes-rollout.md`](../plans/2026-05-02-signal-cli-hermes-rollout.md) (now `superseded`, kept for the original architecture rationale)
- App manifests: [`apps/base/signal-cli/`](../../apps/base/signal-cli/), [`apps/base/hermes/`](../../apps/base/hermes/)
- Bridge code: [`images/signal-bridge/`](../../images/signal-bridge/) — see its README for the full env var contract
- The original migration to k8s-native signal-cli (data-dir restore from the TrueNAS Custom App): see commits 2026-05-01 and 2026-05-02 in `git log apps/base/signal-cli/`
