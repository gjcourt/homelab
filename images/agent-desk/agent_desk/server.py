"""Webhook receiver and the single worker thread.

Plane signs each delivery with HMAC-SHA256 of the JSON body, hex, in
X-Plane-Signature (bgtasks/webhook_task.py). Anything unsigned or wrong gets
a 401 and is dropped. A good delivery is only a hint: it queues the ticket
for the worker, which re-reads Plane before acting.

Plane retries only on connection errors, never on a non-2xx reply, and turns
the webhook off after its retries run out. So the 10-minute poll is the real
backstop, not an optimisation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POLL = ("poll",)
# Seconds before re-checking a ticket whose label isn't attributed yet. After
# the last one the 10-minute poll still catches it.
RECHECK = (3, 10, 30)
GITHUB = ("github",)


def signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.strip().lower())


def make_handler(secret: str, work: queue.Queue, health, log, seen=lambda: None):
    from .dispatcher import event_target

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == "/healthz" and health()
            self.send_response(200 if ok else 503)
            self.end_headers()
            self.wfile.write(b"ok\n" if ok else b"stale\n")

        def do_POST(self):
            if self.path != "/plane":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(n, 2 * 1024 * 1024))
            if not signature_ok(secret, body, self.headers.get("X-Plane-Signature")):
                log(f"rejected delivery {self.headers.get('X-Plane-Delivery')}: bad signature")
                self.send_response(401)
                self.end_headers()
                return
            seen()
            try:
                target = event_target(json.loads(body))
            except ValueError:
                target = None
            delivery = self.headers.get("X-Plane-Delivery")
            if target and all(target):
                log(f"delivery {delivery}: {self.headers.get('X-Plane-Event')} → queued item {target[1]}")
                work.put(("item",) + target)
            else:
                log(f"delivery {delivery}: {self.headers.get('X-Plane-Event')} → nothing to check")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    return Handler


def run(dispatcher, cfg, log=print) -> None:
    work: queue.Queue = queue.Queue()

    def health() -> bool:
        return time.time() - dispatcher.last_tick < cfg.poll_seconds * 2 + 120

    srv = ThreadingHTTPServer(("", cfg.port), make_handler(cfg.webhook_secret, work, health, log,
                                           seen=dispatcher.saw_delivery))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"listening on :{cfg.port}; poll every {cfg.poll_seconds}s; "
        f"dry_run_jobs={cfg.dry_run_jobs}")

    def timer(msg, every):
        while True:
            work.put(msg)
            time.sleep(every)

    threading.Thread(target=timer, args=(POLL, cfg.poll_seconds), daemon=True).start()
    if dispatcher.github:
        threading.Thread(target=timer, args=(GITHUB, cfg.github_poll_seconds),
                         daemon=True).start()
    # One worker: every decision is made serially, so two events for the same
    # ticket can't both start a Job.
    while True:
        msg = work.get()
        try:
            if msg == POLL:
                dispatcher.poll()
            elif msg == GITHUB:
                dispatcher.github_poll()
            else:
                attempt = msg[3] if len(msg) > 3 else 0
                if dispatcher.on_event(msg[1], msg[2]) and attempt < len(RECHECK):
                    log(f"item {msg[2]}: label not attributed yet; "
                        f"rechecking in {RECHECK[attempt]}s")
                    threading.Timer(RECHECK[attempt], work.put,
                                    args=(("item", msg[1], msg[2], attempt + 1),)).start()
        except Exception as e:  # noqa: BLE001 — the worker must outlive any one message
            log(f"error handling {msg[0]}: {e}")
