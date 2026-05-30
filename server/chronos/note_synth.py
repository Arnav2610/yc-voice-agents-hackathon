"""Derive structured dashboard notes from live incident state (complements LLM extraction)."""

from __future__ import annotations

from chronos.state import CallState, StructuredNote

_HAZARD_LABELS: dict[str, tuple[str, str]] = {
    "smoke": ("hazard", "Smoke reported"),
    "visible_fire": ("hazard", "Visible fire"),
    "fire": ("hazard", "Fire reported"),
    "gas_smell": ("hazard", "Gas / gasoline odor"),
    "trapped_person": ("hazard", "Person may be trapped"),
    "child": ("victim", "Child involved"),
    "child_in_vehicle": ("victim", "Child in vehicle"),
    "injury": ("medical", "Injuries reported"),
    "breathing": ("medical", "Breathing difficulty"),
    "weapon": ("threat", "Weapon mentioned"),
}


def derive_notes(state: CallState, turn: int = 0) -> list[StructuredNote]:
    """Build structured notes from policy state when LLM extraction is slow or sparse."""
    inc = state.incident
    out: list[StructuredNote] = []

    if inc.incident_type in ("active_threat", "possible_active_disturbance"):
        out.append(
            StructuredNote(
                category="incident",
                field="classification",
                value=inc.incident_type.replace("_", " "),
                turn=turn,
            )
        )
    elif inc.incident_type and inc.incident_type != "unknown":
        out.append(
            StructuredNote(
                category="incident",
                field="classification",
                value=inc.incident_type.replace("_", " "),
                turn=turn,
            )
        )
    if inc.location_raw:
        val = inc.location_raw
        if inc.location_needs_confirmation:
            val += " (approximate — confirm exact address)"
        out.append(StructuredNote(category="location", field="address_or_landmark", value=val, turn=turn))
    if inc.caller_safety == "self_evacuated":
        out.append(StructuredNote(category="safety", field="caller_status", value="Outside / evacuated", turn=turn))
    elif inc.caller_safety == "resolved":
        out.append(StructuredNote(category="safety", field="caller_status", value="Caller reports safe", turn=turn))
    elif inc.caller_safety == "at_risk":
        out.append(StructuredNote(category="safety", field="caller_status", value="Caller still at risk", turn=turn))

    if inc.third_party_risk == "active":
        out.append(
            StructuredNote(
                category="third_party",
                field="persons_at_risk",
                value="Someone may still be inside / unable to exit",
                turn=turn,
            )
        )

    for h in inc.hazards or []:
        cat, label = _HAZARD_LABELS.get(h, ("hazard", h.replace("_", " ")))
        out.append(StructuredNote(category=cat, field=h, value=label, turn=turn))

    if inc.upgraded_to:
        out.append(
            StructuredNote(
                category="incident",
                field="upgraded_to",
                value=inc.upgraded_to.replace("_", " "),
                turn=turn,
            )
        )

    return out


def merge_notes(existing: list[StructuredNote], derived: list[StructuredNote]) -> list[StructuredNote]:
    """Merge derived notes; LLM-sourced entries win on same (category, field)."""
    by_key: dict[tuple[str, str], StructuredNote] = {}
    for n in derived:
        by_key[(n.category, n.field)] = n
    for n in existing:
        by_key[(n.category, n.field)] = n
    order = list(dict.fromkeys((n.category, n.field) for n in existing + derived))
    merged = [by_key[k] for k in order if k in by_key]
    return dedupe_notes(merged)


# Collapse near-duplicate fields so the dashboard shows one canonical value per fact.
_FIELD_ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    ("location", "address_or_landmark"): ("location", "address"),
    ("location", "exact_location"): ("location", "address"),
    ("location", "landmark"): ("location", "address"),
    ("contact", "callback_phone"): ("contact", "callback_number"),
    ("contact", "phone"): ("contact", "callback_number"),
    ("contact", "phone_number"): ("contact", "callback_number"),
    ("contact", "name"): ("contact", "caller_name"),
    ("threat", "weapon"): ("threat", "weapon_type"),
    ("threat", "armed"): ("threat", "weapon_type"),
    ("threat", "description"): ("threat", "threat_type"),
    ("threat", "incident_description"): ("threat", "threat_type"),
    ("suspect", "intruder_location"): ("threat", "suspect_location"),
}


def _canonical_key(note: StructuredNote) -> tuple[str, str]:
    cat = (note.category or "other").strip().lower()
    field = (note.field or "").strip().lower()
    return _FIELD_ALIASES.get((cat, field), (cat, field))


def canonical_note_key(category: str, field: str) -> tuple[str, str]:
    """Public helper for canonical (category, field) used when merging notes."""
    return _canonical_key(StructuredNote(category=category, field=field, value="", turn=0))


def dedupe_notes(notes: list[StructuredNote]) -> list[StructuredNote]:
    """Keep the latest turn per canonical (category, field); drop stale duplicates."""
    best: dict[tuple[str, str], StructuredNote] = {}
    order: list[tuple[str, str]] = []
    for n in notes:
        key = _canonical_key(n)
        prev = best.get(key)
        if prev is None:
            order.append(key)
            best[key] = StructuredNote(category=key[0], field=key[1], value=n.value, turn=n.turn)
        elif n.turn >= prev.turn:
            best[key] = StructuredNote(category=key[0], field=key[1], value=n.value, turn=n.turn)
    return [best[k] for k in order if k in best]
