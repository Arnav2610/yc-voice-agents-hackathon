"""Dynamic SOP plan generation and generic slot resolution.

On the live voice path, when an incident is classified the planner can ask an LLM
to tailor the checklist to the situation (hazards, third-party risk, prior memory)
while preserving policy-mandated core slots for regression safety.

Offline / simulator runs use the YAML policy directly via `plan_from_policy`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from chronos import config

# Human-readable incident labels for the dashboard.
INCIDENT_LABELS: dict[str, str] = {
    "structure_fire": "Structure Fire",
    "vehicle_crash": "Vehicle Crash",
    "medical": "Medical Emergency",
    "non_emergency_noise": "Noise Complaint",
    "possible_active_disturbance": "Active Disturbance",
    "active_threat": "Active Threat",
}

# Category metadata used by the dashboard to group checklist items.
CATEGORY_META: dict[str, dict[str, str]] = {
    "location": {"label": "Location", "icon": "📍"},
    "safety": {"label": "Caller Safety", "icon": "🛡"},
    "third_party": {"label": "Third-Party Risk", "icon": "👥"},
    "medical": {"label": "Medical Status", "icon": "🩺"},
    "vehicle": {"label": "Vehicle / Road", "icon": "🚗"},
    "hazard": {"label": "Hazards", "icon": "⚠"},
    "contact": {"label": "Contact", "icon": "📞"},
    "general": {"label": "General", "icon": "☑"},
}

# Default category for well-known slot ids (YAML + LLM output).
_SLOT_CATEGORY: dict[str, str] = {
    "exact_location": "location",
    "location": "location",
    "caller_safety": "safety",
    "trapped_person_status": "third_party",
    "last_known_location": "third_party",
    "consciousness": "medical",
    "breathing": "medical",
    "injury_status": "medical",
    "direction_of_travel": "vehicle",
    "vehicle_hazard": "hazard",
    "callback_number": "contact",
}

# Generic resolution hints per category (substring / state checks).
_CATEGORY_RESOLVE: dict[str, list[str]] = {
    "location": ["street", "avenue", " exit ", "near ", " and ", "address", "highway", "freeway"],
    "safety": ["outside", "away from", "safe", "evacuated", "got out", "i'm out", "we're out"],
    "third_party": ["everyone is out", "no one inside", "nobody inside", "still inside", "can't get out"],
    "medical": ["awake", "responsive", "unconscious", "breathing", "not breathing", "passed out"],
    "vehicle": [" north", " south", " east", " west", "northbound", "southbound", "smoke", "no smoke", "fuel"],
    "contact": ["callback", "phone", "number"],
}


@dataclass
class SOPSlotSpec:
    id: str
    label: str
    question: str
    priority: int = 99
    category: str = "general"
    resolve_hints: list[str] = field(default_factory=list)
    scope_when: str | None = None  # e.g. "third_party_active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SOPPlan:
    incident_type: str
    display_name: str
    protocol_title: str
    slots: list[SOPSlotSpec] = field(default_factory=list)
    source: str = "policy"  # policy | llm | merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_type": self.incident_type,
            "display_name": self.display_name,
            "protocol_title": self.protocol_title,
            "source": self.source,
            "slots": [s.to_dict() for s in self.slots],
        }


def _protocol_title(incident_type: str) -> str:
    labels = {
        "structure_fire": "Fire & Smoke Protocol",
        "vehicle_crash": "Vehicle Crash Protocol",
        "medical": "Medical Triage Protocol",
        "non_emergency_noise": "Non-Emergency Intake",
        "possible_active_disturbance": "Disturbance Escalation Protocol",
        "active_threat": "Threat Response Protocol",
    }
    return labels.get(incident_type, "Emergency Intake Protocol")


def _infer_category(slot_id: str, spec: dict[str, Any]) -> str:
    if spec.get("category"):
        return str(spec["category"])
    return _SLOT_CATEGORY.get(slot_id, "general")


def plan_from_policy(incident_type: str) -> SOPPlan:
    """Build a structured plan from the YAML policy (deterministic baseline)."""
    pol = config.policy_for_incident(incident_type)
    required = pol.get("required_slots") or {}
    slots: list[SOPSlotSpec] = []
    ordered = sorted(required.items(), key=lambda kv: kv[1].get("priority", 99))
    for slot_id, spec in ordered:
        cat = _infer_category(slot_id, spec)
        hints = list(spec.get("resolve_hints") or [])
        hints.extend(_CATEGORY_RESOLVE.get(cat, []))
        slots.append(
            SOPSlotSpec(
                id=slot_id,
                label=spec.get("label") or slot_id.replace("_", " ").title(),
                question=spec.get("question", f"Ask about {slot_id.replace('_', ' ')}."),
                priority=int(spec.get("priority", 99)),
                category=cat,
                resolve_hints=list(dict.fromkeys(hints)),
                scope_when=spec.get("scope_when"),
            )
        )
    return SOPPlan(
        incident_type=incident_type,
        display_name=INCIDENT_LABELS.get(incident_type, incident_type.replace("_", " ").title()),
        protocol_title=_protocol_title(incident_type),
        slots=slots,
        source="policy",
    )


def merge_plans(base: SOPPlan, dynamic: SOPPlan) -> SOPPlan:
    """Merge LLM-generated slots into the policy baseline (policy slots win on id)."""
    by_id = {s.id: s for s in base.slots}
    for s in dynamic.slots:
        if s.id not in by_id:
            by_id[s.id] = s
    merged = sorted(by_id.values(), key=lambda s: s.priority)
    return SOPPlan(
        incident_type=base.incident_type,
        display_name=dynamic.display_name or base.display_name,
        protocol_title=dynamic.protocol_title or base.protocol_title,
        slots=merged,
        source="merged",
    )


def parse_llm_plan(data: dict[str, Any], incident_type: str) -> SOPPlan | None:
    """Parse LLM JSON into an SOPPlan."""
    slots_raw = data.get("slots") or data.get("required_slots")
    if not slots_raw:
        return None
    slots: list[SOPSlotSpec] = []
    if isinstance(slots_raw, dict):
        items = sorted(slots_raw.items(), key=lambda kv: (kv[1].get("priority", 99) if isinstance(kv[1], dict) else 99))
        for slot_id, spec in items:
            if not isinstance(spec, dict):
                continue
            cat = _infer_category(slot_id, spec)
            hints = list(spec.get("resolve_hints") or [])
            hints.extend(_CATEGORY_RESOLVE.get(cat, []))
            slots.append(
                SOPSlotSpec(
                    id=slot_id,
                    label=spec.get("label") or slot_id.replace("_", " ").title(),
                    question=spec.get("question", ""),
                    priority=int(spec.get("priority", 99)),
                    category=cat,
                    resolve_hints=list(dict.fromkeys(hints)),
                    scope_when=spec.get("scope_when"),
                )
            )
    elif isinstance(slots_raw, list):
        for i, spec in enumerate(slots_raw):
            if not isinstance(spec, dict):
                continue
            slot_id = spec.get("id") or spec.get("slot") or f"slot_{i}"
            cat = _infer_category(slot_id, spec)
            hints = list(spec.get("resolve_hints") or [])
            hints.extend(_CATEGORY_RESOLVE.get(cat, []))
            slots.append(
                SOPSlotSpec(
                    id=slot_id,
                    label=spec.get("label") or slot_id.replace("_", " ").title(),
                    question=spec.get("question", ""),
                    priority=int(spec.get("priority", i + 1)),
                    category=cat,
                    resolve_hints=list(dict.fromkeys(hints)),
                    scope_when=spec.get("scope_when"),
                )
            )
    if not slots:
        return None
    return SOPPlan(
        incident_type=incident_type,
        display_name=data.get("display_name") or INCIDENT_LABELS.get(incident_type, incident_type),
        protocol_title=data.get("protocol_title") or _protocol_title(incident_type),
        slots=sorted(slots, key=lambda s: s.priority),
        source="llm",
    )


def slot_in_scope(spec: SOPSlotSpec, state) -> bool:
    """Whether a slot is currently active given incident branch state."""
    inc = state.incident
    cond = (spec.scope_when or "").lower()
    if cond == "third_party_active":
        return inc.third_party_risk in ("active", "resolved")
    if spec.id == "last_known_location":
        return inc.third_party_risk in ("active", "resolved")
    if spec.category == "third_party" and spec.id == "trapped_person_status":
        return True
    return True


def slot_resolved(spec: SOPSlotSpec, state, cum: str, llm_resolved: set[str] | None = None) -> bool:
    """Resolution from LLM marks + policy branch state (no substring heuristics)."""
    if llm_resolved and spec.id in llm_resolved:
        return True

    inc = state.incident
    slot = spec.id

    if slot in ("exact_location", "location"):
        return bool(inc.location_raw or inc.location_geocoded) and not inc.location_needs_confirmation
    if slot == "caller_safety":
        return inc.caller_safety in ("resolved", "self_evacuated", "at_risk")
    if slot in ("trapped_person_status", "last_known_location"):
        return inc.third_party_risk == "resolved"

    return False
