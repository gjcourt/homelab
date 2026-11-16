"""Tell George, on his phone, when a ticket needs him (plan, "Notifications").

Plane's web app shows a new comment only when the page reloads, so without
this a question or a finished PR waits until he happens to look. agent-desk
POSTs {title, message, url} to a Home Assistant webhook, which pushes it to
his phone. A stopgap until the phase 2 chat channel replaces it.

The webhook id is the only credential, so it's a secret, never config.
Failures are logged and dropped: a missed notice must never stop dispatch.
"""

from __future__ import annotations

import json
import urllib.request


class Notifier:
    def __init__(self, base_url: str, webhook_id: str, log=print):
        self.url = f"{base_url.rstrip('/')}/api/webhook/{webhook_id}" if base_url and webhook_id else ""
        self.log = log
        self.sent: list[dict] = []  # what went out, for the log and the tests

    def send(self, title: str, message: str, url: str = "") -> None:
        body = {"title": title, "message": message[:300], "url": url}
        self.sent.append(body)
        if not self.url:
            return
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
            self.log(f"notified: {title}")
        except Exception as e:  # noqa: BLE001 — a missed notice mustn't stop dispatch
            self.log(f"notify failed ({title}): {e}")
