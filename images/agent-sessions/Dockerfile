# agent-sessions — session events from bench-cloud Jobs (lab 03-042 phase 2,
# plan docs/plans/2026-11-08-agent-sessions-plan.md, step 2: the skeleton).
#
# Stdlib only: no requirements.txt to install.
FROM python:3.15-slim

WORKDIR /app

COPY images/agent-sessions/agent_sessions/ agent_sessions/

USER 65534:65534
EXPOSE 8080

ENTRYPOINT ["python", "-u", "-m", "agent_sessions"]
CMD ["serve"]
