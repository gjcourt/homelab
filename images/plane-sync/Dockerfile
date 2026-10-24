# plane-sync — lab briefs and repo plans <-> Plane (lab 03-042 / plan
# docs/plans/2026-10-21-plane-lab-project-sync.md).
#
# Runs as a CronJob: reads gjcourt/lab and repo docs/plans/, mirrors them into
# Plane, and writes Plane's brief statuses back to the lab repo through one
# auto-merging PR. Needs git (clone/commit/push over HTTPS with a token).

FROM python:3.15-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY plane_sync/ plane_sync/

# nobody:nogroup. git needs a writable HOME for its config lookups; the
# CronJob mounts an emptyDir at /tmp and the state volume separately.
ENV HOME=/tmp
USER 65534:65534

ENTRYPOINT ["python", "-u", "-m", "plane_sync"]
CMD ["reconcile"]
