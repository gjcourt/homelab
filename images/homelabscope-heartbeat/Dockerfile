# homelabscope-heartbeat — hestia-side collector for scheduled jobs that can't
# run their own exporter (alcatraz-photos-pull over SSH + ZFS snapshot freshness).
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
# zfsutils-linux  — `zfs list -t snapshot` (read-only; needs host /dev/zfs)
# bash, coreutils — the script uses bash-isms + GNU `date -d`
# ca-certificates — general TLS hygiene
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client \
        zfsutils-linux \
        bash \
        coreutils \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY heartbeat.sh /usr/local/bin/heartbeat.sh
RUN chmod 0755 /usr/local/bin/heartbeat.sh

ENTRYPOINT ["/usr/local/bin/heartbeat.sh"]
