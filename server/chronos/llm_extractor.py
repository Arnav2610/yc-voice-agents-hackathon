"""Unified LLM call-state extraction — the ONLY source of incident classification,
location, hazards, safety branches, and escalation signals in production.

Mid-ramble: partial=True (activation-only — never close safety branches).
Final turn: partial=False (full apply including branch resolution).
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

_EXTRACTION_SYSTEM = (
    "You are a 911 call-state extractor for a training simulator. "
    "Return ONLY valid JSON matching the schema. No prose, no markdown fences."
)

_EXTRACTION_PROMPT_PARTIAL = """\
Live partial transcript (caller still speaking — activation only):
{transcript}

Return compact JSON only:
{{
  "incident_type": "structure_fire|vehicle_crash|non_emergency_noise|medical|possible_active_disturbance|active_threat|unknown",
  "incident_confidence": 0.0,
  "location_raw": "address/landmark or null",
  "location_certain": true_or_false,
  "caller_safety": "unknown|at_risk",
  "third_party_at_risk": true_or_false,
  "hazards": ["smoke","fire","weapon","injury","breathing"],
  "risk_level": "unknown|low|medium|high|critical",
  "escalation_required": true_or_false,
  "incident_upgraded_to": null,
  "resolved_slots": [],
  "structured_notes": [{{"category":"threat|location|suspect|medical|other","field":"key","value":"fact"}}]
}}

Rules: classify from caller words only. break-in/robbery/intruder/knife at door -> active_threat.
NEVER structure_fire without explicit smoke/fire. Extract location, weapon, threat facts immediately.
Mark resolved_slots for info already stated (exact_location, threat_description, weapon_info, suspect_location).
Do NOT set caller_safety to safe/evacuated on partials — only unknown or at_risk.
"""

_EXTRACTION_PROMPT_FINAL = """\
Transcript so far (final turn — full apply):
{transcript}

Return JSON:
{{
  "incident_type": "structure_fire|vehicle_crash|non_emergency_noise|medical|possible_active_disturbance|active_threat|unknown",
  "incident_confidence": 0.0,
  "location_raw": "verbatim location/landmark/address from caller, or null",
  "location_certain": true_or_false,
  "caller_safety": "unknown|at_risk|evacuated|safe",
  "third_party_at_risk": true_or_false,
  "third_party_resolved": false,
  "everyone_accounted_for": false,
  "hazards": ["smoke","fire","visible_fire","gas_smell","trapped_person","child","injury","weapon","breathing"],
  "risk_level": "unknown|low|medium|high|critical",
  "escalation_required": true_or_false,
  "escalation_reason": "short reason or null",
  "incident_upgraded_to": "possible_active_disturbance|active_threat|null",
  "correction_detected": false,
  "reentry_intent": false,
  "resolved_slots": [],
  "structured_notes": [
    {{"category": "threat|suspect|victim|location|vehicle|medical|other", "field": "snake_case_key", "value": "verbatim or normalized fact"}}
  ]
}}

Rules:
- Classify ONLY from what the CALLER describes about their emergency.
- Robbery, home invasion, intruder at door, break-in, someone threatening to come in -> active_threat.
  Use possible_active_disturbance for fights/disturbance WITHOUT direct threat to caller's safety.
- NEVER structure_fire unless caller explicitly mentions smoke, fire, flames, or burning.
  An apartment address or hotel name alone is NOT a fire. Do not infer fire from "scared" or noise.
- Once active_threat or possible_active_disturbance fits, do NOT return structure_fire.
- vehicle_crash ONLY for actual roadway/vehicle collisions. "accidentally" is not a crash.
- Breathing trouble -> medical. Fire/smoke in building -> structure_fire.
- third_party_at_risk: true if ANYONE else may be in danger.
- caller_safety: "safe" if caller says they are safe (even "safe for now"); "at_risk" if imminent danger at door.
- location_certain: true if street address AND unit/room/apartment number given.
- resolved_slots: mark EVERY checklist slot already answered in the transcript, including:
  exact_location, caller_safety, callback_number, threat_description, suspect_location, weapon_info,
  consciousness, breathing, trapped_person_status — even if the dispatcher has not asked yet.
- structured_notes: extract ALL facts — full address, room number, caller name, phone/callback,
  threat type (robbery/break-in), suspect at door, weapon mentions, etc.
- incident_upgraded_to: only when call STARTED as one type and escalated mid-call; otherwise set
  incident_type directly to active_threat/possible_active_disturbance and leave incident_upgraded_to null.
"""


# Optional override for unit/regression tests (returns JSON dict, not raw string).
_extract_override: Callable[[str, bool], Awaitable[dict[str, Any] | None] | dict[str, Any] | None] | None = None


def set_extract_override(
    fn: Callable[[str, bool], Awaitable[dict[str, Any] | None] | dict[str, Any] | None] | None,
) -> None:
    global _extract_override
    _extract_override = fn


async def extract_call_state(transcript: str, *, partial: bool = True) -> dict[str, Any] | None:
    """Extract structured call state from transcript via LLM (or test override)."""
    import asyncio

    if _extract_override is not None:
        result = _extract_override(transcript, partial)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    if os.getenv("CHRONOS_MOCK_LLM", "").lower() in ("1", "true", "yes"):
        from chronos.llm_mock import mock_extract_call_state

        return mock_extract_call_state(transcript, partial=partial)

    try:
        from chronos.llm_guidance import _chat, _extract_json

        template = _EXTRACTION_PROMPT_PARTIAL if partial else _EXTRACTION_PROMPT_FINAL
        tail = transcript[-1200:] if partial else transcript[-2500:]
        prompt = template.format(transcript=tail)
        raw = await asyncio.to_thread(
            _chat,
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            False,
            320 if partial else 500,
        )
        return _extract_json(raw)
    except Exception:
        return None
