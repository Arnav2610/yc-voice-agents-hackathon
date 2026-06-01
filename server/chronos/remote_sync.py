"""Push live call state from Pipecat Cloud (or local bot) to a remote dashboard."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any


def push_live_state(payload: dict[str, Any]) -> None:
    """Fire-and-forget POST to CHRONOS_DASHBOARD_URL/chronos/ingest."""
    base = os.getenv("CHRONOS_DASHBOARD_URL", "").strip().rstrip("/")
    key = os.getenv("DASHBOARD_INGEST_KEY", "").strip()
    if not base or not key:
        return

    body = json.dumps(payload).encode("utf-8")

    def _post() -> None:
        try:
            req = urllib.request.Request(
                f"{base}/chronos/ingest",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Chronos-Ingest-Key": key,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=4)
        except (urllib.error.URLError, TimeoutError, OSError):
            pass

    threading.Thread(target=_post, daemon=True, name="chronos-remote-sync").start()
