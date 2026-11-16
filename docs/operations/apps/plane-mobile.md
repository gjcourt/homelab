# plane-mobile (phone client for Plane)

## 1. Overview

A phone-first PWA for the agent desk: George reads and answers agent-desk tickets, changes
state and labels, and gets Web Push notifications. One small Python service serves the PWA, a
JSON API, an SSE stream (`/api/events`), Plane's webhook receiver (`POST /plane`) and Web Push.
Code and image: [`gjcourt/plane-mobile`](https://github.com/gjcourt/plane-mobile). Design:
its `docs/plans/2026-10-25-architecture-plan.md` ("Deployment in the homelab", "Build order").

| Environment | Namespace | Host | Plane | Authelia |
| --- | --- | --- | --- | --- |
| Staging | `plane-mobile-stage` | `desk.stage.burntbytes.com` (`app-gateway-staging`) | `plane-stage`, workspace `my-test-workspacce`, `SANDBOX` | `auth.stage.burntbytes.com` |
| Production | `plane-mobile-prod` | `desk.burntbytes.com` (`app-gateway-production`) | `plane-prod`, workspace `home`, agent-desk's projects | `auth.burntbytes.com` |

Both hosts are **LAN and WireGuard only**. Neither is in the Cloudflare tunnel, and neither has
a public DNS record: they resolve through AdGuard's wildcards (`*.burntbytes.com → 10.42.2.40`,
`*.stage.burntbytes.com → 10.42.2.42`, [dns-strategy](../../architecture/dns-strategy.md)).
Away from home the phone reaches them over WireGuard (`vpn.burntbytes.com`), with AdGuard as its
DNS. Don't add `desk` to the tunnel.

## 2. Architecture

- **Manifests:** `apps/base/plane-mobile/` (namespace, SA without token or RBAC, PVC
  `plane-mobile-data` 1Gi `truenas-iscsi`, Deployment, Service, base CNP, ghcr pull secret) and
  one overlay per environment (`apps/{staging,production}/plane-mobile/`): ConfigMap
  (`configMapGenerator`, env names as in the app's `config.py`), Secret (SOPS), HTTPRoute, the
  Plane and OIDC network policies, the `hostAliases` pin for the Authelia host, and the image
  tag.
- **One replica, `Recreate`.** Push subscriptions are SQLite on an RWO volume and the SSE fan-out
  is in-process. Don't scale it up.
- **Sign-in:** the app is an Authelia OIDC client (`plane-mobile`, PKCE S256,
  `client_secret_basic`, `two_factor`) and checks its own session cookie on `/api/*`. The gateway
  doesn't gate the host: Authelia has an explicit `bypass` rule for it, so a later
  `default_policy` change can't put forward-auth in front of the app's own sign-in.
- **OIDC back-channel:** the pod resolves the Authelia host through `hostAliases` to the
  gateway VIP, so its policy allows **both** the VIP on 443 and the Authelia pods on 9091
  (Envoy enforces the pod's egress against the upstream it picks; see
  `apps/base/audiobookshelf/networkpolicy.yaml`).
- **Plane:**
  - REST calls to `plane-api:8000` as George (his own API token, used for nothing else);
  - webhooks from Plane's worker straight to the Service,
    `http://plane-mobile.<ns>.svc.cluster.local/plane`, never through the gateway. The host is
    in Plane's `WEBHOOK_ALLOWED_HOSTS` (`apps/<env>/plane/release-patch.yaml`), next to
    agent-desk's;
  - Plane's own CNP (`apps/<env>/plane/networkpolicy.yaml`) admits the app on 8000 and lets
    deliveries out to it on 8080. Both halves are needed.
- **The gateway route** sends everything except `/plane` to the app. `/plane` is a rule with no
  backend, so Cilium answers it 500 at the gateway.
- **Network (CNP, default deny):** in from the gateway (`host`, `remote-node`, `ingress`) and
  probes on 8080, and from Plane's worker on 8080; out to DNS, Plane's api on 8000, the gateway
  VIP on 443 and Authelia on 9091, and the internet on 443 minus private ranges (Web Push to
  `*.push.apple.com`).

## 3. Secrets (operator-only)

Agents never run `sops` here. Each overlay has `secret-plane-mobile.yaml.example`, which lists
every key and the command that makes it. Every value differs per environment.

| Key | What | Made with |
| --- | --- | --- |
| `oidc-client-secret` | Plaintext half of the Authelia client. The digest goes in `apps/<env>/authelia/configuration.yaml` | `authelia crypto hash generate pbkdf2 --variant sha512 --random --random.length 72 --random.charset rfc3986` |
| `session-key` | Signs the session cookie. Rotating it signs every phone out | `openssl rand -hex 32` |
| `vapid-private-key` | Web Push signing key. Its public half is `VAPID_PUBLIC_KEY` in the overlay. Rotating it ends every push subscription | `openssl ecparam -name prime256v1 -genkey -noout` |
| `plane-api-key` | George's own Plane API token, only for this service | Plane → Profile settings → API tokens |
| `webhook-secret` | Verifies Plane's deliveries | Shown by Plane when the webhook is created |

## 4. Production: operator steps

These are the steps in order. The production PR is a draft with `validate-kustomize` red on
purpose until step 6: `apps/production/plane-mobile/kustomization.yaml` lists
`secret-plane-mobile.yaml`, which doesn't exist yet, and that keeps the placeholder Authelia
digest away from production Authelia.

1. **OIDC client secret.** Run
   `authelia crypto hash generate pbkdf2 --variant sha512 --random --random.length 72 --random.charset rfc3986`.
   The **digest** replaces `'$pbkdf2-sha512$310000$REPLACE-ME$REPLACE-ME'` (the `plane-mobile`
   client) in `apps/production/authelia/configuration.yaml`. The **plaintext** becomes
   `oidc-client-secret`.
2. **VAPID key pair (production's own, never staging's).**

   ```bash
   openssl ecparam -name prime256v1 -genkey -noout -out vapid-prod.pem
   openssl ec -in vapid-prod.pem -pubout -outform DER 2>/dev/null \
     | tail -c 65 | base64 | tr '/+' '_-' | tr -d '=\n'
   ```

   The PEM becomes `vapid-private-key`. The second command's output replaces
   `REPLACE-ME-production-vapid-public-key` in `VAPID_PUBLIC_KEY` in
   `apps/production/plane-mobile/kustomization.yaml`. The app refuses to start if the two
   halves don't match. Delete `vapid-prod.pem` after.
3. **Session key.** `openssl rand -hex 32` becomes `session-key`.
4. **Production Plane token.** Signed in to `plane.burntbytes.com` as yourself
   (`gjcourt@gmail.com`), create a token used only by this service under Profile settings →
   API tokens. It becomes `plane-api-key`.
5. **`ALLOWED_USER`.** Check it is your production Authelia user name (matched against
   `preferred_username`; `users.yml` is SOPS-only). The overlay has `george`, staging's.
6. **Secret, first commit.** In the PR branch:

   ```bash
   cp apps/production/plane-mobile/secret-plane-mobile.yaml.example \
      apps/production/plane-mobile/secret-plane-mobile.yaml
   # fill in steps 1-4; webhook-secret: a random placeholder for now
   #   (openssl rand -hex 32): the real one doesn't exist until step 9
   sops -e -i apps/production/plane-mobile/secret-plane-mobile.yaml
   kustomize build apps/production >/dev/null && echo ok
   ```

   Commit it **together with** the digest (step 1) and the VAPID public key (step 2). CI goes
   green.
7. **Merge.** Flux applies: the new namespace, Plane's api and worker restart for the
   `WEBHOOK_ALLOWED_HOSTS` change, and production Authelia restarts for its ConfigMap (a few
   seconds of SSO for every app).
8. **Verify sign-in and push.** See section 5, checks 1–3.
9. **Production Plane webhook.** Only after the merge, because Plane checks
   `WEBHOOK_ALLOWED_HOSTS` when the webhook is saved. In `plane.burntbytes.com` → Workspace
   settings → Webhooks → Add webhook: URL
   `http://plane-mobile.plane-mobile-prod.svc.cluster.local/plane`, events **Work items** and
   **Work item comments**. Leave agent-desk's webhook as it is.
10. **Secret, second commit.** `sops apps/production/plane-mobile/secret-plane-mobile.yaml`, put
    the secret Plane showed in `webhook-secret`, commit, PR, merge. The app reads its Secret
    files at startup and a Secret change doesn't roll the pod, so once Flux has applied it,
    delete the pod (`kubectl -n plane-mobile-prod delete pod -l app=plane-mobile`; RWO volume,
    so delete rather than `rollout restart`).
11. **Step-8 checks:** section 5, checks 4–6. Then the agent-desk link change (section 6).

## 5. Checks (build step 8)

1. `kubectl -n plane-mobile-prod get pods` is `1/1 Running`, and
   `kubectl -n plane-mobile-prod logs deploy/plane-mobile | grep -i error` is empty.
   `/readyz` stays 503 while a configured project can't be read: check every identifier in
   `PROJECTS` exists in Plane and is visible to the token's user.
2. On the LAN: `https://desk.burntbytes.com/` loads without sign-in, `/api/waiting` answers
   `401` until you sign in, and sign-in round-trips through `auth.burntbytes.com` with 2FA.
   `curl -sk -o /dev/null -w '%{http_code}\n' https://desk.burntbytes.com/plane` is `500`
   (the gateway's backendless rule; the app's receiver is in-cluster only).
3. Install it on the iPhone from Safari (Share → Add to Home Screen; production is a separate
   install from staging), sign in, turn on notifications, and send the test push from Settings.
   Repeat sign-in once over WireGuard away from the LAN.
4. **Webhook:** comment on a SANDBOX ticket in Plane. The pod log shows the `POST /plane`
   delivery (`200`), and the open ticket updates on the phone within seconds.
5. **SSE hold through `app-gateway-production`.** Hold the stream open for more than 5 minutes
   (target 15) with a real session cookie. Sign in from a desktop browser on the LAN, copy the
   `plane_mobile_session` cookie for `desk.burntbytes.com` from its dev tools, then:

   ```bash
   COOKIE='plane_mobile_session=<value>'
   start=$(date +%s)
   curl -sN --max-time 1200 -H "Cookie: $COOKIE" -H 'Accept: text/event-stream' \
     https://desk.burntbytes.com/api/events \
     | while IFS= read -r line; do
         [ -n "$line" ] && printf '%4ds %s\n' $(( $(date +%s) - start )) "$line"
       done
   echo "ended after $(( $(date +%s) - start ))s"
   ```

   Expect `hello`, then a `ping` every 25 s, until curl's own 1200 s limit ends it. Record the
   result here (date, how long, how it ended).
   - **If it's cut** before 1200 s (Envoy's route timeout or the listener's 300 s stream idle
     timeout), add a rule for `/api/events` to `apps/production/plane-mobile/httproute.yaml`,
     above the `/` rule:

     ```yaml
     - matches:
         - path:
             type: PathPrefix
             value: /api/events
       timeouts:
         request: "0s"
       backendRefs:
         - name: plane-mobile
           port: 80
     ```

     Gateway API `v1.5.0` is installed, which has `HTTPRouteRule.timeouts`. Whether Cilium
     takes `0s` as "no timeout" isn't verified; if the retest is still cut, use `1h`. Make the
     same change on staging, and retest both.
   - The app reconnects by itself (`EventSource`, then `hello` makes it re-fetch, and it
     re-reads watched tickets every 60 s), so a cut stream degrades updates but loses nothing.
   - The Envoy config can be read without changing anything:
     `kubectl get ciliumenvoyconfig -n default cilium-gateway-app-gateway-production -o yaml`.
     On staging (2026-11-08) the `desk.stage` routes set no route `timeout`, and the listener
     sets `streamIdleTimeout: 300s`.
6. **A real notification opens the app** (after section 6): the next agent-desk Blocked or In
   review notification on the phone opens `desk.burntbytes.com/t/<KEY>`. Record whether iOS
   opens the installed PWA or Safari.

HA notifications stay on. Until the Web Push exit criterion is met (plan, "Replacing Home
Assistant notifications"), both arrive, which is intended.

## 6. agent-desk link change (C6), after the checks

A separate PR, merged only once section 5's checks 1–5 pass. It gives agent-desk an
`appWebUrl` (`apps/production/agent-desk/config.yaml`), and agent-desk's phone notifications
then link to `https://desk.burntbytes.com/t/<KEY>` instead of Plane. Links agent-desk writes
elsewhere (the ticket link in a PR description) stay on Plane. It needs an agent-desk image
built from the merge, pinned in `apps/production/agent-desk/kustomization.yaml`.

Rollback: remove `appWebUrl` from the config. Nothing in Home Assistant changes; its automation
forwards `data.url` as given.

## 7. Troubleshooting

- **Envoy 403 on sign-in callback or `upstream connect error`:** the OIDC back-channel. Check
  `networkpolicy-oidc.yaml` allows both the VIP and the Authelia pods, and that the VIP matches
  `hostAliases` and `kubectl get gateway -A`.
- **No webhook deliveries:** Plane turns a webhook off after its retries run out, and it retries
  only on connection errors. Check Workspace settings → Webhooks, the allowlist in
  `release-patch.yaml`, and Plane's egress rule to `plane-mobile-prod` on 8080. The app shows
  "Updates may be late" when it suspects the webhook.
- **`/readyz` 503 forever:** an unknown project identifier, or a bad `plane-api-key`. The log
  says which.
- **Pod won't start after a key change:** the VAPID halves don't match, or a Secret key is
  missing. The log names it.
- **PVC read-only:** see AGENTS.md, "Recovering read-only iSCSI volumes". Losing the volume loses
  only push subscriptions (turn notifications on again) and the duplicate-comment guard.
