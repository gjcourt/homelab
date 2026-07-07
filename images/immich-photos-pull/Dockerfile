# Daily additive pull of the immich photo library from hestia → alcatraz.
#
# Always-running container; busybox crond fires pull-from-hestia.sh at 05:00
# local time (after hestia's 04:00 alcatraz→hestia backup completes, so the
# SD-card imports that leg surfaces are already on hestia). The script tees its
# output to stdout (captured by `docker logs`) and to /var/log/immich-photos-pull.log
# (bind-mounted to the alcatraz host so the hestia-side homelabscope-heartbeat
# collector can SSH in and read the `END (success, Ns)` trailer).
#
# Designed to run on alcatraz (Synology DSM, 10.42.2.11) under Container
# Manager, as root (the script chowns received files to each DSM account's
# numeric uid). Compose at hosts/alcatraz/immich-photos-pull/docker-compose.yml.
# Mirrors images/immich-photos-backup/ (the hestia-side leg).

FROM alpine:3.23

# Pinned package set (same rationale as images/immich-photos-backup):
#   - rsync          — the transport
#   - openssh-client — for `ssh -i ${SSH_KEY} ...` to hestia
#   - bash           — the script uses bash-isms (arrays, process substitution
#                      `>(tee ...)`, an EXIT trap)
#   - tzdata         — so crond honors the TZ env var; otherwise crond runs in
#                      UTC and the "05:00" entry would fire at the wrong hour
#   - coreutils      — GNU date/stat/wc for the script's `date -u +%FT%TZ`,
#                      `wc -l`, etc. (busybox variants differ subtly)
RUN apk add --no-cache \
        rsync \
        openssh-client \
        bash \
        tzdata \
        coreutils

# The script + crontab are the only thing this image carries on top of base.
# Versioning the script implies a new image build (build-immich-photos-pull.yml
# triggers on changes to images/immich-photos-pull/**).
COPY pull-from-hestia.sh /usr/local/bin/pull-from-hestia.sh
COPY crontab /etc/crontabs/root

RUN chmod 0755 /usr/local/bin/pull-from-hestia.sh && \
    chmod 0600 /etc/crontabs/root

# crond flags:
#   -f      foreground (don't daemonize — Docker wants PID 1 in the foreground)
#   -d 8    debug-level logging to stderr (8 = max verbosity)
#   -L /dev/stderr   send crond's own log lines to stderr too
CMD ["crond", "-f", "-d", "8", "-L", "/dev/stderr"]
