"""Mock tools. Every function is deterministic and SIMULATED — none of them
contact real CAD, SMS, or emergency services. They return fake IDs and log
recommendations only.
"""

from __future__ import annotations

import hashlib
from typing import Any


def _fake_id(prefix: str, seed: str) -> str:
    h = hashlib.sha1(seed.encode()).hexdigest()[:6].upper()
    return f"{prefix}-{h}"


def lookup_prior_incidents(location: str | None) -> list[dict[str, Any]]:
    """Deterministic mock incident history near a location (illustrative only)."""
    if not location:
        return []
    loc = location.lower()
    history: list[dict[str, Any]] = []
    if "pine" in loc or "5th" in loc:
        history.append(
            {"id": "prior_001", "type": "gas_smell", "when": "yesterday 16:32", "summary": "Gas smell near 5th and Pine, no visible fire."}
        )
    if "101" in loc or "exit 430" in loc or "exit 431" in loc:
        history.append(
            {"id": "prior_002", "type": "vehicle_hazard", "when": "past week", "summary": "Repeated shoulder incidents near US-101 exit 430."}
        )
    return history


def resolve_location(raw_location: str | None) -> dict[str, Any]:
    """Return a canonical location with confidence + aliases (mock geocoder).
    Reversible read — safe to speculatively prefetch."""
    if not raw_location:
        return {"canonical": None, "confidence": 0.0, "needs_confirmation": True, "aliases": []}
    loc = raw_location.lower()
    if "pine" in loc:
        return {
            "canonical": "5th Ave & Pine St",
            "confidence": 0.74,
            "needs_confirmation": True,
            "aliases": ["old Safeway at 5th and Pine"],
        }
    if "430" in loc or "431" in loc or "101" in loc:
        return {
            "canonical": "US-101 S, Exit 430 (Oak Blvd)",
            "confidence": 0.6,
            "needs_confirmation": True,
            "aliases": ["Exit 431 is Marina Way (next exit north)"],
        }
    return {"canonical": raw_location, "confidence": 0.5, "needs_confirmation": True, "aliases": []}


def create_mock_cad_event(incident_state: dict[str, Any]) -> dict[str, Any]:
    """Return a SIMULATED CAD event id. Never dispatches anything real."""
    seed = f"{incident_state.get('incident_type')}|{incident_state.get('location_raw')}"
    return {
        "cad_event_id": _fake_id("SIM-CAD", seed),
        "simulated": True,
        "note": "Simulated CAD record for training only — no responders dispatched.",
    }


def send_mock_sms(phone: str | None, summary: str) -> dict[str, Any]:
    """Log a SIMULATED SMS. Does not send a real message."""
    return {
        "sms_id": _fake_id("SIM-SMS", f"{phone}|{summary}"),
        "to": phone or "unknown",
        "simulated": True,
        "body_preview": summary[:120],
    }


def escalate_to_human(reason: str) -> dict[str, Any]:
    """Log a SIMULATED human-dispatcher handoff recommendation."""
    return {
        "handoff_id": _fake_id("SIM-HANDOFF", reason),
        "recommended": True,
        "simulated": True,
        "reason": reason,
        "note": "Recommend a human dispatcher take over. Simulated handoff — no real dispatch.",
    }
