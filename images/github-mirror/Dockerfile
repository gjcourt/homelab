# Mirror every GitHub repo of an account (all branches + tags) to local disk as
# bare `git clone --mirror` clones. Designed to run on hestia as a TrueNAS Custom
# App — see hosts/hestia/github-mirror/docker-compose.yml — writing into the family
# tree at admin/backups/global/github/.
#
# Runs the mirror once on start (seeds immediately) then every ${INTERVAL_SECONDS}
# (default daily). A plain loop, not cron, so the container runs cleanly as a
# NON-root uid: the write-protected admin/backups tree only accepts writes as its
# owner (uid 1028 / george), which the compose sets via `user:`.
#
# The image is deliberately storage-agnostic (DEST is just an env/mount) so the
# same artifact runs as a k8s CronJob elsewhere — see the example in
# hosts/hestia/github-mirror/k8s-cronjob.example.yaml.
FROM alpine:3.23

# git   — the mirror engine
# curl  — list repos via the GitHub REST API
# jq    — parse the API JSON
# bash  — the scripts use bash-isms
# ca-certificates — HTTPS to github.com / api.github.com
# tzdata — honor TZ for readable log timestamps
RUN apk add --no-cache git curl jq bash ca-certificates tzdata

COPY github-mirror.sh /usr/local/bin/github-mirror.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/github-mirror.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
