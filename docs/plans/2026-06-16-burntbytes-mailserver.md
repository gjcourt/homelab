---
status: planned
last_modified: 2026-06-16
summary: "Self-hosted mail for burntbytes.com (<10 accounts): Mailu on the cluster + VPS SMTP gateway + SES smarthost"
---

# burntbytes.com Mail Server

Self-hosted email for **burntbytes.com**, fewer than 10 personal mailboxes. Mailboxes and
storage live on the cluster + TrueNAS; the internet-facing SMTP path and outbound deliverability
are delegated to a small VPS relay and AWS SES respectively, because those are the parts that are
both painful and partly out of our control.

> **Decision still open:** for <10 mailboxes, hosted email on the domain (Migadu, flat per-domain
> ~$19–90/yr) is economically competitive with this self-host **and** removes all maintenance +
> deliverability risk. Pursue this plan only if the goal is the project itself (learning, data
> sovereignty, homelab control). See [§7](#7-the-hosted-alternative-decide-first).

## 1. Goals & non-goals

**Goals**

- Real IMAP mailboxes (`@burntbytes.com`), < 10 accounts, with webmail.
- Mailbox data on our own hardware (TrueNAS), not a third party.
- Inbox-placement-grade deliverability to Gmail / Outlook (SPF + DKIM + DMARC aligned).
- No silent mail loss: outbound never depends solely on a residential link or homelab uptime.

**Non-goals**

- Bulk / marketing sending. Personal volume only.
- Self-hosting the **sending reputation** — explicitly delegated to SES. We never send outbound
  directly from a homelab or fresh-VPS IP as the primary path.
- Calendar/contacts (CalDAV/CardDAV) — out of scope for v1; Mailu can add later.

## 2. Why a VPS is required (and why Cloudflare Tunnel can't do this)

- **Cloudflare Tunnel is HTTP(S) only** — it does not carry SMTP (25/465/587) or IMAP (993). The
  existing `cloudflare-tunnel` ingress path is irrelevant to mail.
- Mail requires a **stable public IPv4 with a matching PTR (reverse-DNS)** record. Residential ISP
  links typically (a) block port 25 in/out and (b) give no PTR control. A cheap VPS provides both.
- The VPS is a **dumb relay**: no mailboxes, no message storage. It is the public `MX` for inbound
  and the outbound hop to SES. All state stays on the cluster.

## 3. Architecture

```
        Internet
           │  inbound SMTP :25                 outbound
           ▼                                      ▲
   ┌────────────────┐    WireGuard tunnel   ┌──────┴──────────────┐
   │  VPS (tiny)    │◄─────────────────────►│  Talos cluster      │
   │  static IPv4   │  inbound mail  ─► down │  ns: mailu          │
   │  PTR: mail.    │  outbound mail ─► up   │   • front (Postfix) │
   │   burntbytes   │                        │   • imap (Dovecot)  │
   │  Postfix relay │                        │   • antispam(Rspamd)│
   │  = public MX   │                        │   • admin + webmail │
   └───────┬────────┘                        │   • redis           │
           │ relay outbound                  └──────────┬──────────┘
           ▼                                            │ PVCs (RWX)
   ┌────────────────┐                          ┌────────▼──────────┐
   │  AWS SES        │  owns sending IP rep,    │  TrueNAS (hestia) │
   │  smarthost      │  DKIM signing for send   │  maildir + config │
   └────────────────┘                          └───────────────────┘
```

**Component responsibilities**

| Component | Runs on | Role |
| :--- | :--- | :--- |
| Postfix (edge relay) | VPS | Public MX; receives :25, forwards inbound down the tunnel; relays outbound to SES |
| WireGuard | VPS ↔ a cluster node | Private transport for inbound/outbound mail between VPS and cluster |
| Mailu `front` (Postfix) | cluster `mailu` ns | Internal SMTP, routing, DKIM-sign for local domain |
| Mailu `imap` (Dovecot) | cluster | IMAP/POP, mailbox delivery (LMTP) |
| Mailu `antispam` (Rspamd) | cluster | Spam scoring, greylisting, DKIM/ARC |
| Mailu `admin` + `webmail` | cluster | Account admin UI + Roundcube/SnappyMail (behind Authelia + cloudflare-tunnel) |
| AWS SES | AWS | Outbound smarthost: sending IP reputation + DKIM for sends |
| TrueNAS | hestia | Maildir + Mailu config on an RWX PV (NFS) |

## 4. DNS (Cloudflare — all mail records **grey-clouded / proxy OFF**)

| Type | Name | Value | Notes |
| :--- | :--- | :--- | :--- |
| `A` | `mail.burntbytes.com` | VPS IPv4 | proxy **OFF**; MX target |
| `MX` | `burntbytes.com` | `10 mail.burntbytes.com` | inbound entry point |
| `TXT` (SPF) | `burntbytes.com` | `v=spf1 include:amazonses.com ip4:<VPS_IP> -all` | authorize SES + the VPS relay |
| `CNAME`×3 (DKIM) | `<token>._domainkey…` | provided by SES | SES "Easy DKIM" |
| `TXT` (DKIM) | `dkim._domainkey.burntbytes.com` | Mailu-generated public key | signs intra-domain / received mail |
| `TXT` (DMARC) | `_dmarc.burntbytes.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@burntbytes.com; adkim=s; aspf=s` | start at `quarantine`, tighten to `reject` after a clean week |
| **PTR** | (VPS provider panel) | → `mail.burntbytes.com` | reverse DNS — deliverability-critical, set at the VPS host, not Cloudflare |
| `TXT` (MTA-STS, optional) | `_mta-sts` + policy host | enforce TLS | nice-to-have, defer |

**Gotcha:** the `A`/`MX` mail records must be unproxied (grey cloud). Cloudflare does not proxy mail
and a proxied record breaks SMTP. Reuses the apex/CNAME lessons from
`feedback_cloudflare_tunnel_apex_and_reload`.

## 5. Cluster wiring (Mailu)

- **Namespace:** `mailu` (Flux Kustomization `apps/mailu`, `mailu-prod` per the `-prod` suffix
  convention; no staging variant — mail is a singleton like `cloudflare-tunnel`).
- **Chart:** Mailu official Helm chart, pinned, via a Flux `HelmRelease`. (Alternative: mailcow,
  but it's docker-compose-native and fights k8s — Mailu is the cluster-native choice.)
- **Storage:** one RWX PV on TrueNAS NFS for `/data` (maildir + config). Mail is small; size ~20Gi
  to start. NOT iSCSI (single-writer) — Dovecot + admin need shared access. Pre-bind with a
  `claimRef` per `feedback_flux_pvc_volumename_anti_pattern`.
- **Secrets:** SES SMTP credentials, Mailu `SECRET_KEY`, initial admin password — all SOPS-encrypted
  (`.sops.yaml`), shipped as a `.yaml.example` template in the draft PR per
  `feedback_sops_modifications_operator_only`; operator fills real values.
- **Exposure:**
  - Webmail + admin UI → `cloudflare-tunnel` ingress, gated by **Authelia** SSO
    (`mail.burntbytes.com/admin`, `/webmail`). HTTP only — fine for Cloudflare.
  - SMTP/IMAP do **not** go through the tunnel. IMAP (993) is reached over the LAN / WireGuard only;
    public SMTP terminates on the VPS relay.
- **Outbound:** Mailu `front` relayhost → SES endpoint (`email-smtp.<region>.amazonaws.com:587`,
  STARTTLS, SMTP creds). The VPS relay can also point at SES; pick one authoritative outbound hop
  (prefer Mailu → SES directly over the tunnel; keep the VPS inbound-only if simpler).

## 6. VPS relay

- **Spec:** 1 vCPU / 2 GB, Hetzner CX22 (~€4/mo) or equivalent. Needs static IPv4 **and** PTR
  control (Hetzner/DO/Vultr/Linode all allow rDNS edits; verify before buying).
- **Software:** minimal Postfix as a transport relay:
  - inbound: `relay_domains = burntbytes.com`, `transport_maps` → cluster `front` over WireGuard.
  - outbound (if routed here): `relayhost = [SES]:587` + SASL creds.
- **WireGuard:** VPS ↔ one cluster node (or the LAN gateway); static peer, restrict to mail ports.
- **Hardening:** never an open relay (`smtpd_relay_restrictions = permit_mynetworks,
  reject_unauth_destination`); fail2ban; only 25 open to the world, everything else WG-only.

## 7. The hosted alternative (decide first)

| Path | Monthly | Maintenance | Deliverability risk | Data location |
| :--- | :--- | :--- | :--- | :--- |
| **This plan (self-host + VPS + SES)** | ~$5 + pennies | ~1–2 h/mo + occasional fire | medium (mitigated by SES) | our hardware |
| **Migadu** (flat per-domain) | ~$2–8 (billed yearly) | ~zero | provider-owned | Migadu |
| **Purelymail** (usage) | ~$1 | ~zero | provider-owned | Purelymail |
| **Fastmail** ($5/user) | $5 × accounts | zero | provider-owned | Fastmail |

For < 10 personal mailboxes, **Migadu** is the rational baseline: similar cost, none of the
maintenance or deliverability exposure. Choose self-host only for the learning / sovereignty value.

## 8. Phased rollout

1. **Decide** self-host vs Migadu (§7). If Migadu → close this plan as `superseded`.
2. **VPS + DNS groundwork:** provision VPS, set PTR, add `A`/`MX`, stand up WireGuard. Verify port 25
   reachable inbound (`telnet mail.burntbytes.com 25` from outside).
3. **SES:** verify the domain, add Easy-DKIM CNAMEs, request production access (exit sandbox — free),
   create SMTP credentials.
4. **Mailu on cluster:** Flux `HelmRelease` + RWX PVC + SOPS secrets; bring up admin/imap/front/
   antispam; create the <10 accounts.
5. **Wire the paths:** VPS inbound → cluster `front`; cluster `front` outbound → SES. Confirm DKIM
   signing on send.
6. **Deliverability validation:** send to [mail-tester.com](https://www.mail-tester.com) (target 10/10),
   send/receive against Gmail + Outlook, confirm SPF/DKIM/DMARC `pass` in headers. Watch DMARC `rua`
   reports for a week.
7. **Tighten:** DMARC `p=quarantine` → `p=reject`; optional MTA-STS; document runbook in
   `docs/operations`.
8. **Backups:** include the maildir PV in the existing TrueNAS snapshot/replication; verify restore.

## 9. Risks & mitigations

| Risk | Mitigation |
| :--- | :--- |
| Outbound flagged as spam | Send via SES (warmed, reputable); SPF/DKIM/DMARC aligned; mail-tester gate before go-live |
| Blocklist hit on VPS IP | Outbound goes through SES, not the VPS IP; inbound-only VPS has minimal listing exposure |
| Port 25 blocked / no PTR at VPS host | Verify PTR + port 25 **before** committing to a provider (step 2) |
| Silent mail loss on homelab outage | Inbound MX is the always-on VPS; it queues if the cluster/tunnel is briefly down (Postfix retries) |
| Open-relay misconfig → spam cannon | Strict `smtpd_relay_restrictions`; test with an external relay-check before exposing :25 |
| Single-writer PVC corruption | RWX NFS (not iSCSI) for Dovecot; PV on TrueNAS with snapshots |
| Cert / tunnel reload gotchas | Webmail/admin via cloudflare-tunnel reuses known reload + Authelia-gating patterns |

## 10. Open questions

- VPS host: Hetzner (cheapest, EU) vs a US region closer to home for latency? Mail is latency-
  tolerant — optimize for clean IP + PTR + price.
- SES region + whether to route outbound Mailu→SES directly (skip the VPS on egress) — preferred,
  keeps the VPS purely inbound.
- Which webmail (Roundcube vs SnappyMail) — cosmetic.
- Do we want catch-all / plus-addressing for the personal accounts? (Mailu supports both.)
