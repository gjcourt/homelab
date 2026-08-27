FROM alpine:3.20

# kubectl for the exec, jq to walk the pod spec, busybox httpd to serve the
# textfile. No app runtime — this only ever runs `sh -c 'printf ok > … && rm'`
# inside somebody else's container.
RUN apk add --no-cache bash curl jq \
 && ARCH="$(uname -m)" \
 && case "$ARCH" in x86_64) K=amd64 ;; aarch64) K=arm64 ;; *) echo "unsupported $ARCH" && exit 1 ;; esac \
 && curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/${K}/kubectl" \
 && chmod +x /usr/local/bin/kubectl

COPY probe.sh /usr/local/bin/probe.sh
RUN chmod +x /usr/local/bin/probe.sh && mkdir -p /metrics

EXPOSE 9105
# httpd serves /metrics/metrics at the URL /metrics; the probe loop rewrites it.
CMD ["sh","-c","httpd -f -p 9105 -h /metrics & exec /usr/local/bin/probe.sh"]
