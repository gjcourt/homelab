# Daily rsync of the immich photo library from alcatraz → hestia.
#
# Always-running container; busybox crond fires immich-photos-backup.sh
# at 04:00 local time (after CNPG S3 backups complete at 02:00). The
# script tees output to stdout (captured by `docker logs`) and writes
# Prometheus textfile metrics on success.
#
# Designed to run on hestia as a TrueNAS Custom App. Compose at
# hosts/hestia/immich-photos-backup/docker-compose.yml.

FROM alpine:3.23

# Pinned package set:
#   - rsync          — the transport
#   - openssh-client — for `ssh -i ${SSH_KEY} ...`
#   - bash           — the script uses bash-isms (process substitution `>(tee ...)`)
#   - tzdata         — so cron honors TZ env var; otherwise crond runs in UTC
#                      and the "04:00" entry would fire at 20:00 ET
RUN apk add --no-cache \
        rsync \
        openssh-client \
        bash \
        tzdata

# The script + crontab are the only thing this image carries on top of base.
# Versioning the script implies a new image build (workflow triggers on
# changes to images/immich-photos-backup/**).
COPY immich-photos-backup.sh /usr/local/bin/immich-photos-backup.sh
COPY crontab /etc/crontabs/root

RUN chmod 0755 /usr/local/bin/immich-photos-backup.sh && \
    chmod 0600 /etc/crontabs/root

# crond flags:
#   -f      foreground (don't daemonize — Docker wants PID 1 in the foreground)
#   -d 8    debug-level logging to stderr (8 = max verbosity)
#   -L /dev/stderr   send crond's own log lines to stderr too
CMD ["crond", "-f", "-d", "8", "-L", "/dev/stderr"]
