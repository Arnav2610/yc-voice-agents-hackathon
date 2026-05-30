"""Infer which SOP slots are already answered from structured state + notes."""

from __future__ import annotations

import re

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

_FILLERS = frozenset({"uh", "um", "hmm", "er", "ah"})


def _substantive_turn(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    words = [w for w in re.split(r"\s+", t) if w and w not in _FILLERS]
    if len(words) >= 2:
        return True
    if len(words) == 1 and words[0] not in _FILLERS:
        return True
    return any(
        p in t
        for p in (
            "yes",
            "yeah",
            "yep",
            "no",
            "nope",
            "awake",
            "worse",
            "better",
            "heavy",
            "slowing",
            "bleeding",
            "help",
        )
    )


def infer_medical_slots_from_text(text: str) -> set[str]:
    """Resolve standard medical slots from caller wording."""
    t = (text or "").lower()
    resolved: set[str] = set()
    if re.search(r"\b(awake|responsive|conscious|i'?m awake|still awake)\b", t):
        resolved.add("consciousness")
    if re.search(r"\b(not breathing|can'?t breathe|trouble breathing|breathing normally|hard to breathe)\b", t):
        resolved.add("breathing")
    if re.search(
        r"\b(bleeding|blood|injured|hurt|pain|cut|burn|tongue|mouth)\b",
        t,
    ) or (
        re.search(r"\b(worse|better|slowing|flowing|heavy)\b", t)
        and re.search(r"\b(bleed|blood|hurt|injur|pain|tongue|mouth)\b", t)
    ):
        resolved.add("injury_status")
    return resolved


def slot_answered_by_turn(slot_id: str | None, turn_text: str, state: CallState) -> bool:
    """True when this caller turn substantively answers the slot the agent just asked."""
    if not slot_id or not _substantive_turn(turn_text):
        return False
    t = turn_text.lower()
    sid = slot_id.lower()

    if sid in ("consciousness",):
        return bool(
            re.search(r"\b(awake|responsive|conscious|passed out|unconscious|not awake|alert)\b", t)
        ) or (
            _substantive_turn(t) and bool(re.search(r"\b(yeah|yes|yep|no|nope|for now)\b", t))
        )
    if sid in ("breathing",):
        return bool(
            re.search(r"\b(breath|breathing|breathe|choking|gasp)\b", t)
        ) or _substantive_turn(t)
    if sid in ("injury_status",) or "bleed" in sid or "injur" in sid or "mouth" in sid:
        return bool(
            re.search(r"\b(bleed|blood|worse|better|slow|heavy|hurt|pain|injur|cut|burn|tongue|mouth)\b", t)
        )
    if sid in ("caller_safety",):
        return bool(re.search(r"\b(safe|outside|at risk|scared|trapped|help)\b", t)) or _substantive_turn(t)
    if sid in ("exact_location", "location"):
        return bool(state.incident.location_raw) or len(t.split()) >= 3
    if sid in ("callback_number",):
        return sum(c.isdigit() for c in t) >= 7 or bool(re.search(r"\bmy name is\b", t))
    if sid in ("weapon_info",):
        return bool(re.search(r"\b(knife|gun|weapon|armed|no weapon|don'?t see)\b", t))
    if sid in ("threat_description",):
        return len(t.split()) >= 4
    if sid in ("suspect_location",):
        return bool(re.search(r"\b(door|inside|outside|left|here|there|gone)\b", t))

    # Dynamic / LLM-tailored slots: any substantive reply counts as answered.
    return _substantive_turn(turn_text)


def infer_resolved_slots(state: CallState, *, allow_safety: bool = True) -> set[str]:
    """Return slot ids the caller has already substantively answered."""
    inc = state.incident
    resolved: set[str] = set()
    notes = state.structured_notes

    if inc.location_raw and not inc.location_needs_confirmation:
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

    resolved |= infer_medical_slots_from_text(state.cumulative_text)

    return resolved


def info_slots_only(slots: set[str]) -> set[str]:
    return {s for s in slots if s in _INFO_SLOTS}
