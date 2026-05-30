"""SOP checklist engine.

Uses a structured SOP plan (from YAML policy or LLM-tailored plan) to build a
live checklist, resolve slots generically, and recommend the highest-priority
unresolved question.

Slot gating (the part the WRONG_BRANCH_CLOSURE patch toggles):
  * trapped_person_status is in scope for as long as third-party risk is not
    explicitly resolved.
  * last_known_location is in scope only once third-party risk is ACTIVE.
When a baseline policy lets caller-evacuation resolve third-party risk, both
slots flip to resolved and drop out of the "missing" set — exactly the failure
the improvement loop fixes.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronos import config
from chronos.sop_planner import SOPPlan, SOPSlotSpec, plan_from_policy, slot_in_scope, slot_resolved
from chronos.state import CallState, ChecklistItem


@dataclass
class ChecklistResult:
    items: list[ChecklistItem]
    recommended_slot: str | None
    recommended_question: str | None
    missing: list[str]


class SOPEngine:
    def __init__(self) -> None:
        self._llm_resolved: set[str] = set()

    def set_llm_resolved(self, slots: set[str] | None) -> None:
        self._llm_resolved = slots or set()

    def ensure_plan(self, state: CallState, incident_type: str | None) -> SOPPlan | None:
        """Ensure state has an SOP plan for the active incident type."""
        if not incident_type:
            return None
        existing = state.sop_plan
        if existing and existing.get("incident_type") == incident_type:
            return _plan_from_dict(existing)
        plan = plan_from_policy(incident_type)
        state.sop_plan = plan.to_dict()
        return plan

    def apply_dynamic_plan(self, state: CallState, plan: SOPPlan) -> None:
        state.sop_plan = plan.to_dict()

    def update_checklist(self, state: CallState) -> ChecklistResult:
        inc = state.incident
        plan = self.ensure_plan(state, inc.incident_type)
        if not plan or not plan.slots:
            return self._empty_result(state)

        cum = state.cumulative_text.lower()
        items: list[ChecklistItem] = []
        scope_slots: list[str] = []
        resolved_slots: list[str] = []

        for spec in plan.slots:
            active = slot_in_scope(spec, state)
            resolved = slot_resolved(spec, state, cum, self._llm_resolved)
            items.append(
                ChecklistItem(
                    slot=spec.id,
                    question=spec.question,
                    priority=spec.priority,
                    resolved=resolved,
                    active=active,
                    label=spec.label,
                    category=spec.category,
                )
            )
            if active:
                scope_slots.append(spec.id)
                if resolved:
                    resolved_slots.append(spec.id)

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
        if rec_slot is None and skipped:
            rec_slot = skipped[0].slot
            rec_q = skipped[0].question

        state.checklist = items
        inc.required_slots = scope_slots
        inc.resolved_slots = resolved_slots
        state.recommended_slot = rec_slot
        state.recommended_question = rec_q
        missing = [s for s in scope_slots if s not in resolved_slots]
        return ChecklistResult(items=items, recommended_slot=rec_slot, recommended_question=rec_q, missing=missing)

    def _empty_result(self, state: CallState) -> ChecklistResult:
        state.checklist = []
        state.recommended_slot = None
        state.recommended_question = None
        state.incident.required_slots = []
        state.incident.resolved_slots = []
        return ChecklistResult(items=[], recommended_slot=None, recommended_question=None, missing=[])


def _plan_from_dict(d: dict) -> SOPPlan:
    slots = [
        SOPSlotSpec(
            id=s["id"],
            label=s.get("label", s["id"]),
            question=s["question"],
            priority=int(s.get("priority", 99)),
            category=s.get("category", "general"),
            resolve_hints=s.get("resolve_hints") or [],
            scope_when=s.get("scope_when"),
        )
        for s in d.get("slots", [])
    ]
    return SOPPlan(
        incident_type=d["incident_type"],
        display_name=d.get("display_name", d["incident_type"]),
        protocol_title=d.get("protocol_title", "Emergency Protocol"),
        slots=slots,
        source=d.get("source", "policy"),
    )
