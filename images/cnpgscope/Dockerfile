# cnpgscope — read-only CloudNativePG fleet health.
#
# The primary use is a local operator CLI (`./cnpgscope.py`); this image exists
# so the same tool can run as an in-cluster CronJob (a periodic fleet sweep that
# alerts on a non-zero exit code). It needs Python (stdlib only) plus a `kubectl`
# binary and `bash` (the per-instance df/du probe runs `bash -c`).
FROM python:3.12-slim

# kubectl — pinned, strictly increasing. Bump alongside the cluster's server
# version (currently Kubernetes v1.35.0).
ARG KUBECTL_VERSION=v1.35.0
ARG TARGETARCH=amd64

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY cnpgscope.py /usr/local/bin/cnpgscope.py
RUN chmod 0755 /usr/local/bin/cnpgscope.py

USER 65534:65534

ENTRYPOINT ["/usr/local/bin/cnpgscope.py"]
