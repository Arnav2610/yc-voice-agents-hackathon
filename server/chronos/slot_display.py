"""Operator-facing Known/ask cell text — only for resolved checklist slots."""

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


def _filter_keys(values: dict[str, str], allowed_slots: set[str] | None) -> dict[str, str]:
    if allowed_slots is None:
        return values
    return {k: v for k, v in values.items() if k in allowed_slots}


def derive_slot_display_values(
    state: CallState, *, allowed_slots: set[str] | None = None
) -> dict[str, str]:
    """Build display text from notes/state for resolved slots only (offline fallback)."""
    inc = state.incident
    text = state.cumulative_text
    out: dict[str, str] = {}

    if inc.location_raw:
        if allowed_slots is None or "exact_location" in allowed_slots or "location" in allowed_slots:
            loc = inc.location_raw
            if inc.location_needs_confirmation:
                loc += " (confirm exact address)"
            if allowed_slots is None or "exact_location" in allowed_slots:
                out["exact_location"] = loc
            if allowed_slots is None or "location" in allowed_slots:
                out["location"] = loc

    if allowed_slots is None or "caller_safety" in allowed_slots:
        safety = {
            "self_evacuated": "Caller evacuated / outside",
            "resolved": "Caller reports they are safe",
            "at_risk": "Caller still at risk / in danger",
        }.get(inc.caller_safety)
        if safety and inc.caller_safety != "unknown":
            out["caller_safety"] = safety

    if allowed_slots is None or "trapped_person_status" in allowed_slots:
        if inc.third_party_risk == "active":
            out["trapped_person_status"] = "Someone may still be inside or unable to exit"
        elif inc.third_party_risk == "resolved":
            out["trapped_person_status"] = "All persons accounted for"

    if allowed_slots is None or "callback_number" in allowed_slots:
        phone = _extract_phone(text)
        names = [
            n.value for n in state.structured_notes if n.field in ("caller_name", "name", "callback_name")
        ]
        if phone and names:
            out["callback_number"] = f"{names[-1]} · {phone}"
        elif phone:
            out["callback_number"] = phone

    slot_field_map = {
        ("threat_type", "threat_description", "incident_description", "description"): "threat_description",
        ("suspect_location", "intruder_location", "door_status"): "suspect_location",
        ("weapon_type", "weapon", "armed"): "weapon_info",
    }
    for n in state.structured_notes:
        val = _accept(n.value)
        if not val:
            continue
        target = None
        for fields, slot_id in slot_field_map.items():
            if n.field in fields:
                target = slot_id
                break
        if n.category == "medical":
            if n.field in ("injury", "injury_status"):
                target = target or "injury_status"
            elif n.field == "breathing":
                target = target or "breathing"
            elif n.field == "consciousness":
                target = target or "consciousness"
        if not target:
            continue
        if allowed_slots is not None and target not in allowed_slots:
            continue
        out[target] = val

    return _filter_keys({k: v for k, v in out.items() if _accept(v)}, allowed_slots)


def merge_slot_display_values(
    state: CallState,
    incoming: dict[str, str],
    *,
    allowed_slots: set[str] | None = None,
) -> bool:
    """Merge display values; ignore keys for unresolved slots."""
    changed = False
    for k, v in incoming.items():
        key = str(k)
        if allowed_slots is not None and key not in allowed_slots:
            continue
        val = _accept(v)
        if not val:
            continue
        if state.slot_display_values.get(key) != val:
            state.slot_display_values[key] = val
            changed = True
    return changed


def slots_missing_display(state: CallState, resolved_ids: set[str]) -> list[str]:
    """Resolved slots that still lack an operator-facing Known/ask summary."""
    missing: list[str] = []
    for sid in resolved_ids:
        if _accept(state.slot_display_values.get(sid)) is None:
            missing.append(sid)
    return missing


def prune_slot_display_values(state: CallState, allowed_slots: set[str]) -> bool:
    """Drop display values for slots that are no longer resolved."""
    changed = False
    for key in list(state.slot_display_values.keys()):
        if key not in allowed_slots:
            del state.slot_display_values[key]
            changed = True
    return changed
