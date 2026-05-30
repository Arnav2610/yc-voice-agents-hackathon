"""Infer which SOP slots are already answered from structured state + notes."""

from __future__ import annotations

from chronos.state import CallState

_INFO_SLOTS = frozenset({
    "exact_location",
    "location",
    "callback_number",
    "threat_description",
    "suspect_location",
    "weapon_info",
    "direction_of_travel",
    "vehicle_hazard",
    "consciousness",
    "breathing",
    "injury_status",
})


def infer_resolved_slots(state: CallState, *, allow_safety: bool = True) -> set[str]:
    """Return slot ids the caller has already substantively answered."""
    inc = state.incident
    resolved: set[str] = set()
    notes = state.structured_notes

    if inc.location_raw and not inc.location_needs_confirmation:
        resolved.add("exact_location")
        resolved.add("location")
    elif inc.location_geocoded and not inc.location_needs_confirmation:
        resolved.add("exact_location")
        resolved.add("location")

    if allow_safety and inc.caller_safety in ("resolved", "self_evacuated", "at_risk"):
        resolved.add("caller_safety")

    if inc.third_party_risk == "resolved":
        resolved.add("trapped_person_status")
        resolved.add("last_known_location")

    fields = {(n.category, n.field) for n in notes}
    values = {n.field: n.value for n in notes}

    has_name = any(
        n.field in ("caller_name", "name", "callback_name") or n.category == "contact" and "name" in n.field
        for n in notes
    )
    has_phone = any(
        n.field in ("callback_number", "phone", "callback_phone", "phone_number")
        or (n.category == "contact" and any(c.isdigit() for c in n.value))
        for n in notes
    )
    if not has_phone:
        # Digits in transcript often mean callback given even if note extraction missed it.
        digits = sum(c.isdigit() for c in state.cumulative_text)
        if digits >= 7 and has_name:
            has_phone = True
        elif digits >= 10:
            has_phone = True

    if has_name and has_phone:
        resolved.add("callback_number")
    elif has_phone:
        resolved.add("callback_number")

    if ("threat", "description") in fields or ("hazard", "threat") in fields or values.get("threat_type"):
        resolved.add("threat_description")
    if any(n.field in ("suspect_location", "intruder_location", "door_status") for n in notes):
        resolved.add("suspect_location")
    if any(n.field in ("weapon_type", "weapon", "armed") for n in notes) or "weapon" in inc.hazards:
        resolved.add("weapon_info")
    if any(n.field in ("threat_description", "threat_type", "incident_description") for n in notes):
        resolved.add("threat_description")
    if any(n.field in ("suspect_location", "intruder_location", "door_status") for n in notes):
        resolved.add("suspect_location")

    return resolved


def info_slots_only(slots: set[str]) -> set[str]:
    return {s for s in slots if s in _INFO_SLOTS}
