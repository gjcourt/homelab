"""python -m agent_sessions [serve | secret --work-item ID --job NAME]

serve (the default) runs the service. Env:
  SESSION_KEY_FILE  the shared session-key (SOPS, operator-only); required
  PORT              default 8080
  EVENT_LOG         optional file to append the JSON lines to, as well as stdout

secret prints a Secret manifest holding one Job's BENCH_SESSION_TOKEN, for a
hand-launched test Job (step 2's exit). It reads SESSION_KEY_FILE too. Pipe it
straight into `kubectl apply -f -`; don't save or paste it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

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


def secret_manifest(key: bytes, work_item: str, job: str, namespace: str, name: str) -> dict:
    return {"apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": name, "namespace": namespace,
                         "labels": {"agent-sessions/job": job}},
            "type": "Opaque",
            "stringData": {"BENCH_SESSION_TOKEN": server.token(key, work_item, job)}}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agent_sessions")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    s = sub.add_parser("secret")
    s.add_argument("--work-item", required=True)
    s.add_argument("--job", required=True)
    s.add_argument("--namespace", default="bench-cloud")
    s.add_argument("--name", help="default: <job>-session")
    a = p.parse_args(argv)
    key = read_key()
    if not key:
        print("SESSION_KEY_FILE is unset, unreadable or empty", file=sys.stderr)
        return 2
    if a.cmd == "secret":
        if not server.WORK_ITEM.match(a.work_item) or not server.JOB.match(a.job):
            print("bad work item id or Job name", file=sys.stderr)
            return 2
        print(json.dumps(secret_manifest(key, a.work_item, a.job, a.namespace,
                                         a.name or f"{a.job}-session"), indent=2))
        return 0
    port = int(os.environ.get("PORT", "8080"))
    emit = make_emit(os.environ.get("EVENT_LOG", ""))
    srv = server.serve(port, key, emit)
    emit({"listening": port})
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
