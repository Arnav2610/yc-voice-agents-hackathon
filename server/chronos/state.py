"""Live incident + call state objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MemoryResult:
    """A single retrieved memory item."""

    id: str
    content: str
    score: float
    memory_type: str
    container_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredNote:
    """A single extracted fact from the caller transcript."""

    category: str  # threat | suspect | victim | location | vehicle | medical | other
    field: str
    value: str
    turn: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DispatchRecord:
    """Simulated unit dispatch — training only, never real CAD."""

    unit_type: str  # fire | police | ems
    location: str | None
    reason: str
    dispatch_id: str
    turn: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IncidentState:
    """The live hypothesis about the incident, driven by deterministic policy."""

    incident_type: str | None = None
    incident_confidence: float = 0.0
    risk_level: str = "unknown"  # unknown | low | medium | high | critical
    location_raw: str | None = None
    location_geocoded: str | None = None  # Google Maps formatted address for dispatch
    location_lat: float | None = None
    location_lng: float | None = None
    location_place_id: str | None = None
    location_maps_url: str | None = None
    location_confidence: float = 0.0
    location_needs_confirmation: bool = True
    caller_safety: str = "unknown"  # unknown | self_evacuated | resolved | at_risk
    # unknown | active | resolved — third-party (someone-else-inside) safety
    third_party_risk: str = "unknown"
    hazards: list[str] = field(default_factory=list)
    required_slots: list[str] = field(default_factory=list)
    resolved_slots: list[str] = field(default_factory=list)
    escalation_required: bool = False
    escalation_reason: str | None = None
    upgraded_to: str | None = None
    correction_detected: bool = False
    reentry_intent: bool = False

    def add_hazard(self, hazard: str) -> bool:
        if hazard not in self.hazards:
            self.hazards.append(hazard)
            return True
        return False

    @property
    def missing_slots(self) -> list[str]:
        return [s for s in self.required_slots if s not in self.resolved_slots]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["missing_slots"] = self.missing_slots
        return d


@dataclass
class MemoryContext:
    """Accumulated retrieved memories for the call, de-duplicated by id."""

    results: list[MemoryResult] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)

    def add_many(self, results: list[MemoryResult]) -> list[MemoryResult]:
        existing = {r.id for r in self.results}
        added = []
        for r in results:
            if r.id not in existing:
                self.results.append(r)
                existing.add(r.id)
                added.append(r)
        return added

    def contains_substr(self, substr: str) -> bool:
        s = substr.lower()
        return any(s in r.content.lower() for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "queries_run": self.queries_run,
        }


@dataclass
class ChecklistItem:
    slot: str
    question: str
    priority: int
    resolved: bool = False
    active: bool = True
    label: str = ""
    category: str = "general"


@dataclass
class CallState:
    """Everything Chronos knows about one (simulated) call."""

    call_id: str
    scenario_id: str | None = None
    caller_from: str | None = None  # E.164 when connected via Twilio
    caller_to: str | None = None
    incident: IncidentState = field(default_factory=IncidentState)
    memory: MemoryContext = field(default_factory=MemoryContext)
    turns: list[str] = field(default_factory=list)  # caller final turns
    partial_buffer: list[str] = field(default_factory=list)
    checklist: list[ChecklistItem] = field(default_factory=list)
    sop_plan: dict[str, Any] | None = None
    recommended_question: str | None = None
    recommended_slot: str | None = None
    asked_slots: set[str] = field(default_factory=set)
    # How many times each slot has been recommended without being resolved.
    asked_slot_counts: dict[str, int] = field(default_factory=dict)
    floor_actions: list[dict[str, Any]] = field(default_factory=list)
    guidance_history: list[dict[str, Any]] = field(default_factory=list)
    structured_notes: list[StructuredNote] = field(default_factory=list)
    slot_display_values: dict[str, str] = field(default_factory=dict)
    dispatches: list[DispatchRecord] = field(default_factory=list)

    # Interaction / behavior flags (read by the evaluator).
    suppressed_interruption: bool = False
    backchannel_emitted: bool = False
    background_safety_handled: bool = False
    ignored_background: bool = False
    instructed_reentry: bool = False  # should always stay False
    forbidden_guidance_emitted: bool = False  # should always stay False
    human_handoff_announced: bool = False

    # Timing
    started_ms: int = 0
    first_critical_guidance_ms: int | None = None

    @property
    def cumulative_text(self) -> str:
        return " ".join(self.turns)

    def slot_was_asked(self, slot: str) -> bool:
        """A slot counts as 'asked' if recommended at some turn OR still an
        active, unresolved required slot at the end (the branch is open)."""
        if slot in self.asked_slots:
            return True
        return slot in self.incident.required_slots and slot not in self.incident.resolved_slots

    def checklist_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "slot": c.slot,
                "question": c.question,
                "priority": c.priority,
                "resolved": c.resolved,
                "active": c.active,
                "label": c.label or c.slot.replace("_", " ").title(),
                "category": c.category,
            }
            for c in self.checklist
        ]

    @property
    def intake_complete(self) -> bool:
        inc = self.incident
        if not inc.incident_type:
            return False
        active = [c for c in self.checklist if c.active]
        if not active:
            return bool(inc.required_slots) and not inc.missing_slots
        return all(c.resolved for c in active)

    @property
    def human_handoff_ready(self) -> bool:
        return self.incident.escalation_required and self.intake_complete

    def _missing_slot_labels_for_snapshot(self) -> list[str]:
        missing = set(self.incident.missing_slots or [])
        labels: list[str] = []
        for c in self.checklist:
            if c.active and not c.resolved and c.slot in missing:
                labels.append(c.label or c.slot.replace("_", " ").title())
        return labels

    def snapshot(self) -> dict[str, Any]:
        """A JSON snapshot for the dashboard / latest.json."""
        return {
            "call_id": self.call_id,
            "scenario_id": self.scenario_id,
            "caller_from": self.caller_from,
            "caller_to": self.caller_to,
            "incident": self.incident.to_dict(),
            "memory": self.memory.to_dict(),
            "turns": self.turns,
            "checklist": self.checklist_dicts(),
            "sop_plan": self.sop_plan,
            "recommended_question": self.recommended_question,
            "recommended_slot": self.recommended_slot,
            "asked_slots": sorted(self.asked_slots),
            "floor_actions": self.floor_actions,
            "guidance_history": self.guidance_history,
            "structured_notes": [n.to_dict() for n in self.structured_notes],
            "slot_display_values": dict(self.slot_display_values),
            "dispatches": [d.to_dict() for d in self.dispatches],
            "intake_complete": self.intake_complete,
            "human_handoff_ready": self.human_handoff_ready,
            "missing_slot_labels": self._missing_slot_labels_for_snapshot(),
            "flags": {
                "suppressed_interruption": self.suppressed_interruption,
                "backchannel_emitted": self.backchannel_emitted,
                "background_safety_handled": self.background_safety_handled,
                "ignored_background": self.ignored_background,
                "instructed_reentry": self.instructed_reentry,
                "forbidden_guidance_emitted": self.forbidden_guidance_emitted,
                "human_handoff_announced": self.human_handoff_announced,
            },
            "first_critical_guidance_ms": self.first_critical_guidance_ms,
        }
