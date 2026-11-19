# homelabscope-heartbeat — hestia-side collector for scheduled jobs that can't
# run their own exporter (alcatraz-photos-pull over SSH + ZFS snapshot freshness).
#
# It also carries the homelabscope_container_* probe, which reads the local
# Docker socket — see the comment block in heartbeat.sh and
# docs/plans/2026-08-18-hestia-deploy-monitoring-gap.md section A.
#
# Designed to run on hestia as a TrueNAS Custom App. Compose at
# hosts/hestia/monitoring/docker-compose-homelabscope-heartbeat.yml.
#
# Debian (not alpine) base: the ZFS path prefers `zfs list` via zfsutils-linux,
# and GNU date (`date -d "<iso>"`) is needed to parse the alcatraz log's ISO8601
# timestamps + the .zfs fallback's snapshot-name timestamps. busybox date can't
# do either. zfsutils-linux talks to the host /dev/zfs (bind-mounted by the
# compose); if the in-container userland can't reach it, heartbeat.sh falls back
# to the .zfs/snapshot control dir, so a zfs version mismatch degrades instead
# of failing.
FROM debian:bookworm-slim

# openssh-client — SSH to alcatraz to read the pull log
# zfsutils-linux  — `zfs list -t snapshot` (read-only; needs host /dev/zfs).
#                   Debian ships the ZFS userland in the `contrib` component,
#                   which bookworm-slim doesn't enable by default — so enable it
#                   first (deb822 `Components:` line, with a classic-format
#                   fallback), else apt reports "no installation candidate".
#                   --no-install-recommends keeps zfs-dkms (kernel build) out;
#                   we only need the read-only zfs/zpool CLI.
# bash, coreutils — the script uses bash-isms + GNU `date -d`
# ca-certificates — general TLS hygiene
# curl, jq       — the container probe talks to the Docker Engine API over
#                  /var/run/docker.sock (`curl --unix-socket`) and parses the
#                  inspect JSON. Deliberately NOT the docker CLI: the CLI pulls
#                  in ~30MB and a third-party apt repo to answer questions the
#                  REST API answers in two GETs, and curl+jq add ~3MB from
#                  Debian's own archive.
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources; \
    else \
        echo 'deb http://deb.debian.org/debian bookworm main contrib' > /etc/apt/sources.list.d/contrib.list; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        openssh-client \
        zfsutils-linux \
        bash \
        coreutils \
        ca-certificates \
        curl \
        jq \
    && rm -rf /var/lib/apt/lists/*

COPY heartbeat.sh /usr/local/bin/heartbeat.sh
RUN chmod 0755 /usr/local/bin/heartbeat.sh

ENTRYPOINT ["/usr/local/bin/heartbeat.sh"]
