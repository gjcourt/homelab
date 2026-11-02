# agent-desk — Plane tickets → bench-cloud Jobs (lab 03-042, plan
# docs/plans/2026-10-22-agent-desk-loop.md).
#
# Built from the repo root so the Job template is the one `bench-cloud run`
# uses (scripts/bench-cloud/job.yaml), not a copy that can drift.

FROM python:3.12-slim

WORKDIR /app

COPY images/agent-desk/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY images/agent-desk/agent_desk/ agent_desk/
COPY scripts/bench-cloud/job.yaml /app/job.yaml

USER 65534:65534
EXPOSE 8080

ENTRYPOINT ["python", "-u", "-m", "agent_desk"]
