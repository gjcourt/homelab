# Incident: `*.burntbytes.com` wildcard expired — DNS-01 self-check blocked by split-horizon DNS

**Date:** 2026-08-05
**Status:** **Resolved** — new certificate issued (valid to 2026-11-03), all production hosts serving valid TLS. Root cause fixed in [#1274](https://github.com/gjcourt/homelab/pull/1274); detection gap closed in [#1275](https://github.com/gjcourt/homelab/pull/1275)
**Severity:** High — total production TLS outage. Every `*.burntbytes.com` host served an expired certificate; browsers blocked with a security interstitial, `curl` refused the connection outright
**Environments affected:** production (`*.burntbytes.com`) and staging (`*.stage.burntbytes.com`)
**Authors:** George Courtsunis

---

> **The failure lasted 30 days before anyone could see it.** The renewal broke on
> 2026-07-06 and produced no signal a human would notice until the old certificate
> lapsed a month later. The outage is the cheap part of this incident; the
> month of silence is the expensive part.

## Summary

The `*.burntbytes.com` wildcard expired **2026-08-05 05:22:11 GMT**, taking TLS down on
every production host at once. It surfaced as "something is wrong with Home Assistant" —
Home Assistant was fine (`1/1 Running`, 0 restarts, 4d uptime); it was simply the first
host someone happened to open.

cert-manager had scheduled renewal for **2026-07-06** and did start it. The ACME Order
then sat `pending` for **30 days** and never completed.

Cloudflare published the DNS-01 challenge TXT records correctly — they were live in
public DNS the entire time:

```
dig +short TXT _acme-challenge.burntbytes.com @1.1.1.1
"MCL2XKaR2Kh1WGbLOifBcRD9hFj__unrJsLh5_HTs0s"
"cRtiB_eOUoS11wC_RMwGGd0G4xOsqM-8yG80MDYqnLQ"
```

What failed was cert-manager's **own propagation self-check**, which runs *before* it asks
Let's Encrypt to validate. With no `--dns01-recursive-nameservers` configured, that check
uses the cluster resolver:

```
cert-manager → CoreDNS → node /etc/resolv.conf → AdGuard (10.42.2.43)
```

AdGuard serves a split-horizon view of `burntbytes.com` and answers
`NOERROR / ANSWER: 0` for `_acme-challenge.burntbytes.com`. The self-check could therefore
never pass, no matter how correct the published records were. Renewal stalled; the old
certificate ran out.

## Timeline

| When | What |
|---|---|
| 2026-07-06 05:22 | Renewal scheduled and started. Order created, DNS-01 challenges issued |
| 2026-07-06 → 08-05 | Order `pending`. Self-check queries AdGuard, gets `ANSWER: 0`, retries. 30 days |
| 2026-08-05 05:22 | Certificate expires. All production hosts begin serving it expired |
| 2026-08-05 ~16:00 | Reported as "Home Assistant is broken" |
| 2026-08-05 ~16:10 | Diagnosed: expired wildcard, not an app fault. Stored and served certs identical (same serial), so not a stale-cache issue |
| 2026-08-05 ~16:30 | [#1274](https://github.com/gjcourt/homelab/pull/1274) merged: `--dns01-recursive-nameservers=1.1.1.1:53,8.8.8.8:53` + `-only` |
| 2026-08-05 ~16:45 | 30-day-old CertificateRequests deleted to force fresh ACME authorizations (the old ones had expired) |
| 2026-08-05 16:00:38 | New certificate issued, valid to 2026-11-03. Challenge went `pending` → `valid :: Successfully authorized domain` |
| 2026-08-05 ~17:00 | All hosts verified 200/302. Staging recovered by the same path with no separate action |

## Why nobody was warned

Two independent gaps. Either alone would have caught this.

**1. `Ready=True` is not a health signal for renewal.** The Certificate reported
`Ready=True` for the entire month, and it was *correct* — the existing certificate had not
expired yet. Readiness describes the current certificate, not whether renewal is working.
The honest signal was the second condition, `Issuing=True / Renewing`, unchanged since
2026-07-06, and `status.renewalTime` pinned a month in the past. Nothing watched either.

**2. cert-manager metrics were never scraped.** There was no ServiceMonitor, so
`certmanager_certificate_expiration_timestamp_seconds` did not exist in Prometheus at all.
No expiry alert was possible even in principle. The cluster's eight certificate-related
alert rules are all kubelet/apiserver internals — **nothing watched the public certificates
users actually hit.**

## Fixes

**Root cause — [#1274](https://github.com/gjcourt/homelab/pull/1274)**
`infra/controllers/cert-manager/values.yaml`:

```yaml
extraArgs:
  - --dns01-recursive-nameservers=1.1.1.1:53,8.8.8.8:53
  - --dns01-recursive-nameservers-only
```

The documented remedy for split-horizon DNS. `-only` prevents falling back to the cluster
resolver.

**Detection — [#1275](https://github.com/gjcourt/homelab/pull/1275)**
Enables the chart's ServiceMonitor and adds `infra/configs/alerts/cert-manager-rules.yaml`.
The load-bearing alert is `CertManagerRenewalOverdue`: `renewalTime` sits in the future on
a healthy certificate and jumps forward on each successful renewal, so a value in the past
means renewal is due and has not completed. It would have fired **2026-07-07** — four weeks
before a browser error did.

Two traps found while writing that PR, both worth remembering:

- The chart labels its ServiceMonitor `prometheus: default`, but this cluster's Prometheus
  selects on `release: kube-prometheus-stack`. Enabling the ServiceMonitor without adding
  that label creates an object Prometheus silently ignores — monitoring that doesn't
  monitor, which is the same class of bug as the incident itself.
- With the default `honorLabels: false`, prometheus-operator overwrites the metric's
  `namespace` label with the *target's* namespace (always `security`), so every alert
  annotation would have named the wrong namespace and the runbook line would have printed
  the wrong `kubectl -n`.

## Prevent recurrence

- [x] DNS-01 self-check bypasses cluster DNS (#1274)
- [x] cert-manager metrics scraped; expiry + stalled-renewal alerts (#1275)
- [x] `CertManagerDown` uses `absent()`, not just `up == 0`, so the watcher failing is
      itself detectable — a bare `up == 0` stops evaluating when the series vanishes,
      which is precisely the pre-incident state
- [ ] Consider the same split-horizon check for any future ACME solver added to the cluster
- [ ] Broader question this raises: **what else is monitored only by a metric nobody
      scrapes?** cert-manager was silently unmonitored for the cluster's entire lifetime.
      An audit of Helm charts whose `serviceMonitor.enabled` defaults to `false` would
      likely find more.

## Lessons

**A "Ready" condition that describes current state says nothing about whether renewal is
working.** Any resource that self-renews needs an alert on the *renewal* path, not the
readiness of the artifact it last produced. This generalises well beyond certificates.

**A month of retries produced no escalation.** cert-manager retried the self-check for 30
days without ever surfacing differently than a transient failure. Retry without a
time-boxed escalation is indistinguishable from silence.

**The first symptom was a user opening a browser.** That is the monitoring of last resort.
