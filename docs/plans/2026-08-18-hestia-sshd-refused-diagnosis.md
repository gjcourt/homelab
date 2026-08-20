---
status: planned
last_modified: 2026-08-18
summary: "hestia's sshd intermittently refuses port 22 while every other service stays up; the MaxStartups theory carried across sessions is disproven, so collect wedged-state evidence before changing any setting"
blocked_on: "Evidence from the wedged state, which has been destroyed 4-5 times by restarting the service before capturing it. Next wedge: capture from the TrueNAS web Shell over 443 BEFORE restarting."
---

# Plan: hestia sshd refuses port 22 — diagnose before treating

## Signature

```
ICMP ping        0% loss
TCP 80/443/3260  OPEN
TCP 22           REFUSED
```

The data plane is entirely healthy throughout — 161 pods running, iSCSI fine.
Only sshd is affected. Recovery is a manual TrueNAS UI → System Settings →
Services → SSH → restart; it does not self-heal.

Observed triggers: it wedged after roughly **4 short-lived SSH sessions in ~2
minutes**, and again after 5 short invocations fired in quick succession. It has
also re-wedged ~15 minutes after a restart with 6 users connected. **A single
long session reliably survives where several short ones do not.**

## The standing hypothesis is disproven

Prior sessions recorded this as *"sessions accumulating and not being reaped"*
and pointed at `MaxSessions` / `MaxStartups`. Two independent lines of evidence
say that is the wrong tree.

**`MaxStartups` cannot produce `ECONNREFUSED`.** Its check runs in sshd's
`drop_connection()` path, *after* the kernel has completed the three-way
handshake and `accept()` has returned a socket. The client sees the TCP connect
succeed and then get closed —
`kex_exchange_identification: read: Connection reset by peer` or
`Connection closed by remote host`. Never "Connection refused".
([`sshd_config(5)`](https://man.openbsd.org/sshd_config), OpenSSH `sshd.c`.)
It also self-clears as unauthenticated connections drain, which is incompatible
with a fault that persists until a service restart.

**`MaxSessions` is further off still.** It limits shell/exec/subsystem *channels
within one already-authenticated connection*. It cannot affect a new TCP
connect, and it would make the "one long session" workaround worse, not better —
the opposite of what we observe. The hypothesis on file conflates channel count
with connection count.

A genuine `ECONNREFUSED` is a kernel-level RST at SYN. That narrows the field
sharply.

## Competing explanations

Ranked by fit to the observed signature.

**H — systemd socket activation tripping a trigger limit. Leading candidate.**
If sshd is fronted by `ssh.socket` and it trips `TriggerLimitBurst` /
`StartLimitBurst`, the socket unit enters `failed`, the kernel has no listener,
and every subsequent connect is refused — and only a service restart re-arms it.
This fits **all four observations simultaneously**: refused rather than filtered,
triggered by a burst of short connections, harmless to one long connection, and
recoverable only by restart. *Confirm:* `systemctl status ssh.socket` shows
`failed` with `Result=trigger-limit-hit` or `start-limit-hit`, `NRefused`
non-zero. *Rule out:* `ssh.socket` does not exist or is not enabled.

**A — the sshd master is simply gone.** Debian's stock `ssh.service`
historically carries no `Restart=`, so a crash leaves the unit dead with nothing
listening until someone restarts it. *Confirm:* no listener in
`ss -ltnp 'sport = :22'`, unit `failed`/`inactive`, `NRestarts` incremented.
*Rule out:* a listener present while connections are still refused.

**E — resource exhaustion in the accept loop.** fd/task limits causing sshd to
stop calling `accept()`. Normally yields *filtered*, not refused — **unless**
`net.ipv4.tcp_abort_on_overflow=1`, which converts backlog overflow into RST.
That sysctl is the only thing reconciling E with this signature, so check it
explicitly.

**F — a packet filter REJECTs port 22.** Docker 29.4 under TrueNAS Apps rewrites
nft rules on container churn. *Confirm:* a `reject` rule matching dport 22
present only when wedged. *Rule out:* identical ruleset healthy vs wedged.

**G — middleware regenerating `sshd_config` and failing to restart.**
*Confirm:* `/var/log/middlewared.log` shows an ssh config write or
`service.control` call just before the wedge; `sshd -t` non-zero.

**D — stale sshd children accumulating.** The hypothesis on file. Kept because
it is cheap to test, not because it explains a refused connect. *Confirm:*
`pgrep -c sshd` ratchets up per short session and never returns to baseline.

**B / C — `MaxStartups` / `MaxSessions`.** Disproven above. Retained only so the
diagnosis records why, and so `journalctl` is checked for
`drop connection #N ... [preauth]` — whose *absence* over 14 days, given
repeated wedges, is close to conclusive.

## A load source nobody counted

`infra/controllers/democratic-csi/values.yaml` still sets `driver:
truenas-iscsi`, which resolves to `FreeNASSshDriver`. **Every volume provision,
expand and delete opens an SSH session from the cluster into hestia's sshd**,
and expand runs `sudo sh -c "echo 1 > .../resync_size"`. The GHA runner and the
immich rsync jobs are further automated clients.

So "4 short-lived interactive sessions" is very likely undercounting the real
session rate by a wide margin — the interactive sessions may be the straw rather
than the load. `docs/plans/2026-06-28-democratic-csi-ssh-to-api-driver.md`
(blocked on TrueNAS 26.x) now has a second, independent justification.

## Prior art

- `docs/operations/incidents/2026-04-25-hifiberry-ui-tcp-socket-exhaustion.md` —
  **closest structural analogue.** A listener that was `active (running)` in
  systemd but functionally dead because sockets accumulated in `CLOSE_WAIT`
  until the accept backlog filled. `/proc/net/tcp` inspection was the definitive
  diagnostic, and `systemctl restart` hung where `kill -9` plus auto-restart
  worked. **Different in one crucial respect:** that failure presented as
  *filtered* (SYNs dropped). This one presents as *refused* (RST).
- `docs/operations/hifiberry-os-watchdog.md` — directly reusable mitigation
  shape (cron probes localhost, restarts the stuck service, logs via `logger`).
- `docs/operations/2026-05-14-truenas-app-update-quirk.md` — precedent that
  TrueNAS middleware reports `SUCCESS` on operations that did not take effect.
  Relevant to trusting a UI-driven SSH restart.
- **No incident postmortem exists for this**, and `docs/STATUS.md` does not
  mention it. That is why it has been re-diagnosed from scratch across multiple
  sessions.

## Diagnosis

### The evidence only exists while wedged, and we keep destroying it

Restarting the SSH service is the first thing anyone does, and it erases the
state. That has now happened four or five times. **The next wedge is the
opportunity.**

SSH is by definition unavailable, so capture out of band:

1. **TrueNAS UI → System Settings → Shell**, over 443, which is confirmed to
   stay OPEN during the wedge. *Verify this path works while healthy, before
   needing it.*
2. **IPMI at `10.42.2.13`** (ASRock BMC) → HTML5 console, if the web UI is also
   impaired.

Minimum capture, in this order, **before touching restart**:

```bash
systemctl status ssh.service ssh.socket --no-pager -l
systemctl show ssh.socket -p Accept -p MaxConnections -p TriggerLimitBurst -p NRefused -p Result
ss -ltnp 'sport = :22'
ss -tan 'sport = :22' | awk '{print $1}' | sort | uniq -c
pgrep -c sshd; who | wc -l
nft list ruleset | grep -nE 'reject|dport 22'
sysctl net.ipv4.tcp_abort_on_overflow
journalctl -u ssh.service -u ssh.socket --since '-1h' --no-pager | tail -100
```

`systemctl status ssh.socket` is the single highest-value line: it decides
between H and everything else.

### Healthy baseline — one batched session

Run once, healthy, changing nothing, so the wedged capture has something to diff
against. **One `ssh` invocation feeding a script on stdin** — never a loop of
short connections, which is the thing that appears to trigger the fault.

Capture: unit topology (`systemctl cat ssh.service ssh.socket`), effective
config as sshd parses it (`sshd -T | grep -iE 'maxstartups|maxsessions|clientalive'`),
process and session counts, socket states on :22, fd headroom against
`LimitNOFILE`, the nft ruleset, 14 days of `journalctl -u ssh`, `midclt call
ssh.config`, and a per-source histogram of accepted connections over 24h to
quantify the automated clients.

### Flight recorder — the highest-value action available

A 30-second sampler appending `date`, `pgrep -c sshd`, listener presence, socket
state counts, `systemctl is-active ssh.service ssh.socket`, and the master's fd
count to a rolling file on `/mnt/main`.

When it wedges, read the file **over NFS/SMB from any machine, with no shell
access at all**, and get the *trajectory into* the wedge rather than a post-hoc
snapshot.

Install it as a **TrueNAS UI → System Settings → Advanced → Cron Job**, not a
hand-rolled systemd timer: cron jobs live in the middleware config DB and are
included in config backups, whereas anything written into `/etc` is discarded
when the OS dataset is swapped on upgrade.

It changes nothing about how sshd behaves.

## Mitigations, ranked

1. **Formalise the batch-into-one-session discipline.** Zero change on hestia,
   fully reversible, survives upgrades trivially. Write it into
   `hosts/hestia/README.md` as a hard rule: every interaction is one
   `ssh … 'bash -s' <<EOS` invocation, never a loop of short ones. Optionally
   `ControlMaster auto` / `ControlPersist 10m` for `10.42.2.10` in
   `~/.ssh/config` to make it automatic. *Caveat:* multiplexing collapses N
   sessions onto one connection and N channels — helps against connection-rate
   limits, hurts against `MaxSessions` (default 10). Given C is near-ruled-out,
   net win.
2. **Flight recorder** (above). Trivially reversible, near-zero blast radius.
3. **External TCP probe + alert.** The cluster runs kube-prometheus-stack but has
   **no blackbox exporter**. A TCP probe of `10.42.2.10:22` every 30s converts
   "George notices" into a timestamped onset. Must carry a `severity` label —
   the default route receiver is `null` and unlabelled alerts are silently
   dropped.
4. **Reduce automated session churn** — quantify the CSI driver's rate first;
   see above.
5. **sshd tuning via SCALE UI → Services → SSH → Auxiliary Parameters.** The one
   supported path for arbitrary directives; stored in the middleware config DB
   and re-emitted on every regeneration, so it survives upgrades. **Moderate-to-high
   risk: a malformed parameter can stop sshd starting and lock you out.**
   Preconditions: web Shell open in a browser tab and IPMI reachable before
   applying. Candidates *contingent on diagnosis*: `ClientAliveInterval 60` +
   `ClientAliveCountMax 3` (the reaper, if D confirms), `LoginGraceTime 30`.
6. **Watchdog cron** probing `127.0.0.1:22`, recovering via
   `midclt call service.control RESTART ssh` (the middleware owns the service;
   `systemctl restart` skips its bookkeeping). Require **3 consecutive
   failures**, `logger`-tag every action. Deploy it *with* the flight recorder,
   never instead of it, or a diagnosable failure becomes an invisible one.
7. **Hand-editing `/etc/ssh/sshd_config` or a systemd drop-in.** Listed only to
   be rejected: SCALE regenerates `sshd_config` from
   `sshd_config.mako` against the config DB on every service start, so edits are
   reverted or ignored, and `/etc` does not survive an upgrade. If a
   systemd-level change is genuinely required (H's `TriggerLimitBurst`), that is
   an iX bug report, not a local hand-edit.

**The correct first step is 1 + 2 + 3 and nothing else.** Every server-side knob
currently on the table aims at a hypothesis the refused signature already argues
against.

## Verification

There is **no quantitative reproduction yet**, which means there is currently no
way to claim a fix is a fix.

1. **Build one.** With web Shell and IPMI already open, run N sequential short
   ssh invocations with a TCP probe between each; record the connection index at
   which refusal begins and `pgrep -c sshd` throughout. Anecdotally 4–6. Run it
   twice to check the number is stable.
2. **Post-change gate:** the same burst at **5× the failure count** (wedges at 5
   → run 25), with `pgrep -c sshd` returning to baseline afterwards. Necessary,
   not sufficient.
3. **Soak:** the blackbox probe showing **zero refusals over 7 days** of normal
   workload, including at least one democratic-csi volume operation and one GHA
   deploy run — a burst test does not exercise the automated clients.
4. **Flight recorder stays running through the soak.** If it wedges anyway, we
   get the trajectory instead of starting over for the sixth time.

Do not declare it fixed on a quiet afternoon. The bar is the original signature
confirmed gone under conditions that previously produced it.

Record the outcome as
`docs/operations/incidents/2026-08-XX-hestia-sshd-refused.md` and add a
`docs/STATUS.md` Known-issues line in the same PR.

## Open questions

- **Is ssh socket-activated on TrueNAS SCALE 26.x?** The entire ranking pivots
  on this and it is one `systemctl status ssh.socket` away.
- **Is the refusal a genuine RST, or a probe artefact?** "REFUSED" from `nc`
  means RST, which is what rules out `MaxStartups`. If some observations came
  from `ssh` reporting `Connection closed by remote host`, that is a *different*
  signature and reopens B. Confirm which tool produced the word.
- **Does the TrueNAS web Shell actually work while sshd is refusing?** The whole
  evidence plan depends on it. Test while healthy.
- **How many SSH sessions per hour does democratic-csi actually open?**
- **Is 26.x recent enough that a middleware regression (G) is likely?** If so the
  response is a bug report, not local tuning.
- **What happened in the 15-minutes-after-restart recurrence with 6 users?** No
  current hypothesis explains it well. The journal from that window would settle
  whether this is a rate limit or a slow leak.
