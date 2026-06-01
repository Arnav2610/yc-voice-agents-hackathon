"""Persistent registry of live/recent calls for the remote operator dashboard."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from chronos import config

CALLS_DIR = config.RUNTIME_DIR / "calls"
INDEX_PATH = CALLS_DIR / "index.json"
_LOCK = threading.Lock()
_ACTIVE_MS = 120_000


def _ensure_dir() -> None:
    CALLS_DIR.mkdir(parents=True, exist_ok=True)


def _read_index() -> dict[str, Any]:
    _ensure_dir()
    if not INDEX_PATH.exists():
        return {"calls": {}}
    try:
        return json.loads(INDEX_PATH.read_text())
    except Exception:
        return {"calls": {}}


def _write_index(index: dict[str, Any]) -> None:
    _ensure_dir()
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2))
    tmp.replace(INDEX_PATH)


def _call_path(call_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in call_id)
    return CALLS_DIR / f"{safe}.json"


def _is_active(meta: dict[str, Any], now: int | None = None) -> bool:
    now = now or config.now_ms()
    if meta.get("status") == "complete":
        return False
    return (now - int(meta.get("updated_ms") or 0)) < _ACTIVE_MS


def upsert_call(payload: dict[str, Any]) -> str | None:
    """Store full latest payload for a call; returns call_id."""
    snap = payload.get("snapshot") or {}
    call_id = snap.get("call_id")
    if not call_id:
        return None

    _ensure_dir()
    now = int(payload.get("ts") or config.now_ms())
    events = payload.get("events") or []
    status = "complete" if any(e.get("event_type") == "call_complete" for e in events) else "active"

    with _LOCK:
        path = _call_path(call_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=0))
        tmp.replace(path)

        index = _read_index()
        calls = index.setdefault("calls", {})
        inc = snap.get("incident") or {}
        calls[call_id] = {
            "call_id": call_id,
            "caller_from": snap.get("caller_from"),
            "caller_to": snap.get("caller_to"),
            "incident_type": inc.get("incident_type"),
            "location": inc.get("location_geocoded") or inc.get("location_raw"),
            "status": status,
            "updated_ms": now,
            "started_ms": calls.get(call_id, {}).get("started_ms") or now,
        }
        _write_index(index)
    return call_id


def get_call(call_id: str) -> dict[str, Any] | None:
    path = _call_path(call_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def list_calls(include_recent_complete: bool = True) -> list[dict[str, Any]]:
    """Return call summaries, newest first."""
    now = config.now_ms()
    with _LOCK:
        index = _read_index()
        rows = list((index.get("calls") or {}).values())

    rows.sort(key=lambda r: int(r.get("updated_ms") or 0), reverse=True)
    out: list[dict[str, Any]] = []
    for row in rows:
        active = _is_active(row, now)
        if active:
            row = {**row, "live": True}
            out.append(row)
        elif include_recent_complete and row.get("status") == "complete":
            out.append({**row, "live": False})
        elif include_recent_complete and (now - int(row.get("updated_ms") or 0)) < 3_600_000:
            out.append({**row, "live": False})
    return out


def pick_default_call_id() -> str | None:
    """Most recently updated active call, else most recent any."""
    calls = list_calls(include_recent_complete=True)
    for row in calls:
        if row.get("live"):
            return row["call_id"]
    return calls[0]["call_id"] if calls else None
