# gha-runner-watchdog: keeps hestia's self-hosted GitHub runner on its Git pin
# and recreates it when it dies (HOMELAB-108). Runs as a TrueNAS Custom App:
# hosts/hestia/gha-runner-watchdog/docker-compose.yml.
#
# The image is only a launcher. watchdog.py runs with hestia's OWN python3 via
# nsenter (it needs the host's truenas_api_client and docker CLI), so the image
# carries the script, bash and nsenter and nothing else.
FROM alpine:3.24

# bash            — watchdog.sh
# util-linux-misc — nsenter
# tzdata          — honor TZ for readable log timestamps
RUN apk add --no-cache bash util-linux-misc tzdata

COPY watchdog.py /app/watchdog.py
COPY watchdog.sh /app/watchdog.sh
RUN chmod 0644 /app/watchdog.py && chmod 0755 /app/watchdog.sh

ENTRYPOINT ["/app/watchdog.sh"]
