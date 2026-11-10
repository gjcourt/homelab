# hestia-deployer: Git-pull deployer for hestia's TrueNAS Custom Apps
# (HOMELAB-111, docs/plans/2026-11-09-hestia-pull-deployer-plan.md). Runs as a
# TrueNAS Custom App: hosts/hestia/hestia-deployer/docker-compose.yml.
#
# The container does the Git half (ls-remote, depth-1 fetch, compose discovery);
# the TrueNAS half runs with hestia's OWN python3 via nsenter (it needs the
# host's truenas_api_client and docker CLI).
FROM alpine:3.24

# bash            — deployer.sh
# git             — ls-remote + shallow fetch (smart HTTP, no REST API)
# ca-certificates — TLS to github.com
# python3 py3-yaml — the fetch/discovery half
# util-linux-misc — nsenter
# tzdata          — honor TZ for readable log timestamps
# docker-cli docker-cli-compose — the compose backend (DEPLOYER_BACKEND=compose,
#                   alcatraz, HOMELAB-118). Not version-pinned here (alpine
#                   keeps only the current build of a package per branch); the
#                   compose version is fixed per image DIGEST, and one image
#                   both predicts and applies, so the hashes agree. A rebuild
#                   can move it: the startup log prints `compose version`.
#                   hestia's TrueNAS path does not use them.
RUN apk add --no-cache bash ca-certificates docker-cli docker-cli-compose git python3 py3-yaml tzdata util-linux-misc

COPY deployer.py /app/deployer.py
COPY compose_backend.py /app/compose_backend.py
COPY deployer.sh /app/deployer.sh
RUN chmod 0644 /app/deployer.py /app/compose_backend.py && chmod 0755 /app/deployer.sh

ENTRYPOINT ["/app/deployer.sh"]
