---
applyTo: "images/**,**/Dockerfile*"
---

# Docker & image builds

- Prefer multi-stage builds when it reduces runtime size or removes build tooling.
- Prefer pinned versions (base image tags, package versions, downloaded artifacts).
- Avoid runtime GitHub downloads; download during image build instead.
- Prefer Alpine when feasible, but choose Debian/Ubuntu when it avoids compatibility pain (e.g., glibc-dependent binaries).
- Build multi-arch images when required by the cluster (amd64/arm64).
