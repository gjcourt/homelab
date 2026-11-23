"""python -m agent_sessions [serve | secret --work-item ID --job NAME]

serve (the default) runs the service. Env:
  SESSION_KEY_FILE  the shared session-key (SOPS, operator-only); required
  PORT              default 8080
  EVENT_LOG         optional file to append the JSON lines to, as well as stdout

secret prints a Secret manifest holding one Job's BENCH_SESSION_TOKEN, for a
hand-launched pilot Job (step 2's exit). It reads SESSION_KEY_FILE too. Pipe it
straight into `kubectl apply -f -`; don't save or paste it.
  --deadline SECONDS  required: the Job's activeDeadlineSeconds; the token
                      expires 15 minutes after it (George's D2). That expiry
                      is the token's real bound.
  --job-uid UID       the Job's uid: the Secret is then owned by the Job, so
                      Kubernetes deletes it when the Job object is deleted
                      (not when the Job finishes). Only with the Job's own
                      namespace, bench-cloud: an owner in another namespace
                      doesn't resolve, and the Secret would be deleted at once.
agent-desk does the same for every session Job (a later PR).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

from . import server


def read_key() -> bytes:
    path = os.environ.get("SESSION_KEY_FILE", "")
    try:
        with open(path, "rb") as f:
            return f.read().strip()
    except OSError:
        return b""


def make_emit(path: str):
    lock = threading.Lock()

    def emit(record: dict) -> None:
        line = json.dumps(record, sort_keys=True)
        with lock:
            print(line, flush=True)
            if path:
                with open(path, "a") as f:
                    f.write(line + "\n")
    return emit


TOKEN_LABEL = "agent-sessions/token"   # the admission policy keys on it (D1)
JOB_NAMESPACE = "bench-cloud"          # where the Jobs, and so their token Secrets, live


def secret_manifest(key: bytes, work_item: str, job: str, namespace: str, name: str,
                    deadline: int, job_uid: str = "", now: float | None = None) -> dict:
    expires = server.token_expires(deadline, now)
    meta = {"name": name, "namespace": namespace,
            "labels": {TOKEN_LABEL: "true", "agent-sessions/job": job},
            "annotations": {"agent-sessions/expires": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                    time.gmtime(expires))}}
    if job_uid:
        meta["ownerReferences"] = [{"apiVersion": "batch/v1", "kind": "Job", "name": job,
                                    "uid": job_uid}]
    return {"apiVersion": "v1", "kind": "Secret", "metadata": meta, "type": "Opaque",
            "stringData": {"BENCH_SESSION_TOKEN": server.token(key, work_item, job, expires)}}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agent_sessions")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    s = sub.add_parser("secret")
    s.add_argument("--work-item", required=True)
    s.add_argument("--job", required=True)
    s.add_argument("--namespace", default=JOB_NAMESPACE)
    s.add_argument("--name", help="default: <job>-session")
    s.add_argument("--deadline", type=int, required=True,
                   help="the Job's activeDeadlineSeconds; the token expires 15 minutes after")
    s.add_argument("--job-uid", default="",
                   help="the Job's uid: the Job owns the Secret, deleted with the Job object")
    a = p.parse_args(argv)
    key = read_key()
    if len(key) < server.MIN_KEY:
        print(f"SESSION_KEY_FILE is unset, unreadable or shorter than {server.MIN_KEY} bytes "
              "(make one with: openssl rand -hex 32)", file=sys.stderr)
        return 2
    if a.cmd == "secret":
        if not server.WORK_ITEM.match(a.work_item) or not server.JOB.match(a.job):
            print("bad work item id or Job name", file=sys.stderr)
            return 2
        if a.job_uid and a.namespace != JOB_NAMESPACE:
            print(f"--job-uid needs --namespace {JOB_NAMESPACE}, the Job's own: an owner in "
                  "another namespace doesn't resolve, and Kubernetes would delete the Secret at once",
                  file=sys.stderr)
            return 2
        if not 0 < a.deadline <= server.MAX_TOKEN_LIFE - server.TOKEN_GRACE:
            print(f"--deadline must be 1 to {server.MAX_TOKEN_LIFE - server.TOKEN_GRACE} seconds",
                  file=sys.stderr)
            return 2
        print(json.dumps(secret_manifest(key, a.work_item, a.job, a.namespace,
                                         a.name or f"{a.job}-session", a.deadline, a.job_uid),
                         indent=2))
        return 0
    port = int(os.environ.get("PORT", "8080"))
    emit = make_emit(os.environ.get("EVENT_LOG", ""))
    srv = server.serve(port, key, emit)
    emit({"listening": port})
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
