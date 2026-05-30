"""Derive operator-facing checklist cell text from transcript + structured notes."""

from __future__ import annotations

from chronos.partial_hints import _extract_phone
from chronos.state import CallState

_GENERIC = frozenset({
    "weapon mentioned",
    "injuries reported",
    "injury reported",
    "breathing difficulty",
    "breathing difficulty reported",
    "confirmed",
    "provided",
    "yes",
    "unknown",
    "break-in / intruder at door",
})


def _accept(val: str | None) -> str | None:
    if not val:
        return None
    v = str(val).strip()
    if not v or v.lower() in _GENERIC:
        return None
    low = v.lower()
    if low.endswith(" mentioned") or low.endswith(" reported"):
        return None
    return v


def derive_slot_display_values(state: CallState) -> dict[str, str]:
    """Build Known/ask display text from notes and incident state (offline / fallback)."""
    inc = state.incident
    text = state.cumulative_text
    out: dict[str, str] = {}

    if inc.location_raw:
        loc = inc.location_raw
        if inc.location_needs_confirmation:
            loc += " (confirm exact address)"
        out["exact_location"] = loc
        out["location"] = loc

    safety = {
        "self_evacuated": "Caller evacuated / outside",
        "resolved": "Caller reports they are safe",
        "at_risk": "Caller still at risk / in danger",
    }.get(inc.caller_safety)
    if safety:
        out["caller_safety"] = safety

    if inc.third_party_risk == "active":
        out["trapped_person_status"] = "Someone may still be inside or unable to exit"
    elif inc.third_party_risk == "resolved":
        out["trapped_person_status"] = "All persons accounted for"

    phone = _extract_phone(text)
    names = [n.value for n in state.structured_notes if n.field in ("caller_name", "name", "callback_name")]
    if phone and names:
        out["callback_number"] = f"{names[-1]} · {phone}"
    elif phone:
        out["callback_number"] = phone

    for n in state.structured_notes:
        fld = n.field
        val = _accept(n.value)
        if not val:
            continue
        if fld in ("threat_type", "threat_description", "incident_description", "description"):
            out["threat_description"] = val
        elif fld in ("suspect_location", "intruder_location", "door_status"):
            out["suspect_location"] = val
        elif fld in ("weapon_type", "weapon", "armed"):
            out["weapon_info"] = val
        elif fld in ("injury", "injury_status") and n.category == "medical":
            out["injury_status"] = val
        elif fld in ("breathing",) and n.category == "medical":
            out["breathing"] = val
        elif fld in ("consciousness",) and n.category == "medical":
            out["consciousness"] = val

    return out


def merge_slot_display_values(state: CallState, incoming: dict[str, str]) -> bool:
    """Merge new slot display values; returns True if anything changed."""
    changed = False
    for k, v in incoming.items():
        val = _accept(v)
        if not val:
            continue
        key = str(k)
        if state.slot_display_values.get(key) != val:
            state.slot_display_values[key] = val
            changed = True
    return changed
