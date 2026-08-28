FROM alpine:3.20

# kubectl for the exec, jq to walk the pod spec, busybox httpd to serve the
# textfile. No app runtime — this only ever runs `sh -c 'printf ok > … && rm'`
# inside somebody else's container.
# kubectl is PINNED, not tracked from the "stable" channel. An unpinned
# fetch means every rebuild can silently cross a minor version and drift
# outside the supported +/-1 skew from the cluster (v1.35.0), with no record
# in-repo of what is actually running. Bump this deliberately.
ARG KUBECTL_VERSION=v1.35.0

# busybox-extras carries the `httpd` applet. Alpine's BASE busybox does NOT --
# shipping without this produced a container that ran happily, served nothing
# on :9105, failed its readiness probe, and blocked the infra-configs
# Kustomization (and therefore apps-production) until it was fixed.
RUN apk add --no-cache bash curl jq busybox-extras \
 && ARCH="$(uname -m)" \
 && case "$ARCH" in x86_64) K=amd64 ;; aarch64) K=arm64 ;; *) echo "unsupported $ARCH" && exit 1 ;; esac \
 && BASE="https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${K}" \
 && curl -fsSL -o /usr/local/bin/kubectl "${BASE}/kubectl" \
 && curl -fsSL -o /tmp/kubectl.sha256 "${BASE}/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && rm -f /tmp/kubectl.sha256 \
 && chmod +x /usr/local/bin/kubectl \
 && kubectl version --client=true \
 `# Fail the BUILD if httpd is missing, rather than discovering it in-cluster.` \
 && command -v httpd

COPY probe.sh /usr/local/bin/probe.sh
RUN chmod +x /usr/local/bin/probe.sh && mkdir -p /metrics

EXPOSE 9105
# httpd serves /metrics/metrics at the URL /metrics; the probe loop rewrites it.
CMD ["sh","-c","httpd -f -p 9105 -h /metrics & exec /usr/local/bin/probe.sh"]
