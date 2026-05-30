"""SOP checklist engine.

Converts the active incident policy's `required_slots` into a live checklist,
resolves each slot from the current state, and recommends the single
highest-priority unresolved question.

Slot gating (the part the WRONG_BRANCH_CLOSURE patch toggles):
  * trapped_person_status is in scope for as long as third-party risk is not
    explicitly resolved.
  * last_known_location is in scope only once third-party risk is ACTIVE.
When a baseline policy lets caller-evacuation resolve third-party risk, both
slots flip to resolved and drop out of the "missing" set — exactly the failure
the improvement loop fixes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chronos import config
from chronos.state import CallState, ChecklistItem

_PHONE_RE = re.compile(r"\b\d{3}[\s.-]?\d{3,4}\b")
# Any street number + word string — looser than the tracker's pattern, for resolution checks.
_ADDR_RE = re.compile(r"\b\d{1,5}\s+[A-Za-z][\w\s]{2,30}(?:street|st|ave|avenue|road|rd|drive|dr|blvd|lane|ln|way|place|pl|court|ct|circle)\b", re.I)


@dataclass
class ChecklistResult:
    items: list[ChecklistItem]
    recommended_slot: str | None
    recommended_question: str | None
    missing: list[str]


def _resolved(slot: str, state: CallState, cum: str) -> bool:
    inc = state.incident
    tpr = inc.third_party_risk
    if slot in ("exact_location", "location"):
        # Resolved if: tracker extracted a confirmed location, OR the cumulative
        # text contains an explicit street address.
        if inc.location_raw and not inc.location_needs_confirmation:
            return True
        if _ADDR_RE.search(cum):
            return True
        return False
    if slot == "caller_safety":
        return inc.caller_safety in ("resolved", "self_evacuated")
    if slot == "trapped_person_status":
        return tpr == "resolved"
    if slot == "last_known_location":
        return tpr == "resolved"
    if slot == "callback_number":
        return bool(_PHONE_RE.search(cum)) or "callback" in cum
    if slot == "direction_of_travel":
        return any(d in cum for d in (" north", " south", " east", " west", "northbound", "southbound"))
    if slot == "injury_status":
        return any(w in cum for w in ("injured", "hurt", "bleeding", "no one is hurt", "nobody is hurt", "not hurt"))
    if slot == "vehicle_hazard":
        return any(w in cum for w in ("smoke", "fire", "fuel", "no smoke", "no fire"))
    if slot == "consciousness":
        return any(w in cum for w in ("awake", "responsive", "unconscious", "passed out", "not responding"))
    if slot == "breathing":
        return "breathing" in cum
    return False


def _in_scope(slot: str, state: CallState) -> bool:
    """Whether a slot is currently relevant (active) for this incident."""
    tpr = state.incident.third_party_risk
    if slot == "last_known_location":
        # Only ask where someone was last seen once we know someone's inside.
        return tpr in ("active", "resolved")
    # trapped_person_status and everything else are in scope by default.
    return True


class SOPEngine:
    def update_checklist(self, state: CallState) -> ChecklistResult:
        pol = config.policy_for_incident(state.incident.incident_type)
        required = pol.get("required_slots") or {}
        cum = state.cumulative_text.lower()

        items: list[ChecklistItem] = []
        scope_slots: list[str] = []
        resolved_slots: list[str] = []

        # Sort by declared priority for stable recommendation.
        ordered = sorted(required.items(), key=lambda kv: kv[1].get("priority", 99))
        for slot, spec in ordered:
            active = _in_scope(slot, state)
            resolved = _resolved(slot, state, cum)
            items.append(
                ChecklistItem(
                    slot=slot,
                    question=spec.get("question", f"Ask about {slot}."),
                    priority=int(spec.get("priority", 99)),
                    resolved=resolved,
                    active=active,
                )
            )
            if active:
                scope_slots.append(slot)
                if resolved:
                    resolved_slots.append(slot)

        # Recommended = lowest-priority in-scope unresolved slot.
        # On the live voice path (use_llm_extraction=True), skip a slot that's been
        # asked ≥2 times without resolution — move to the next urgent question
        # rather than repeating. In the simulator (use_llm_extraction=False) use
        # strict priority ordering so regression assertions are deterministic.
        ask_counts = getattr(state, "asked_slot_counts", {})
        use_skip = getattr(state, "_use_slot_skip", True)
        rec_slot = None
        rec_q = None
        skipped: list[ChecklistItem] = []
        for it in items:
            if it.active and not it.resolved:
                if use_skip and ask_counts.get(it.slot, 0) >= 2:
                    skipped.append(it)
                    continue
                rec_slot = it.slot
                rec_q = it.question
                break
        # If every unresolved slot has been asked ≥2 times, fall back to the first.
        if rec_slot is None and skipped:
            rec_slot = skipped[0].slot
            rec_q = skipped[0].question

        state.checklist = items
        state.incident.required_slots = scope_slots
        state.incident.resolved_slots = resolved_slots
        state.recommended_slot = rec_slot
        state.recommended_question = rec_q
        missing = [s for s in scope_slots if s not in resolved_slots]
        return ChecklistResult(items=items, recommended_slot=rec_slot, recommended_question=rec_q, missing=missing)
