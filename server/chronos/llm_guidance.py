"""LLM prompting for Chronos.

In the LIVE voice path, the Pipecat LLM (Nemotron) is the speaker. Chronos injects
a CHRONOS LIVE CONTEXT message each turn with policy-computed state, the next
question, dispatch status, and hard constraints.
"""

from __future__ import annotations

import json
import os
from typing import Any

CHRONOS_SYSTEM_PROMPT = """You are Chronos 911, a SIMULATED emergency-call copilot for training and \
evaluation. You are not a real dispatcher and must say so if asked. Every call is a simulation.

You will receive a CHRONOS LIVE CONTEXT block before each caller turn. It contains the \
policy-computed incident state, the single recommended next question, missing SOP slots, \
simulated unit dispatches, structured notes, and hard constraints. TREAT IT AS GROUND TRUTH.

Hard safety constraints (never violate):
- Never claim to be a real 911 dispatcher.
- Never provide medical diagnosis or instruct medication.
- Never give police tactical instructions.
- Never tell a caller to re-enter a dangerous building, approach fire/smoke/an active threat, or \
do risky mechanical repair.
- Never promise an ETA or outcome.
- Keep caller safety and third-party (someone-else-inside) safety as SEPARATE branches.

Call flow (follow strictly):
1. While MISSING SLOTS remain: ask the RECOMMENDED NEXT QUESTION — one short sentence, one question.
2. When the caller gives a landmark or business name (e.g. "Y Combinator office"), call resolve_location_geocode \
BEFORE dispatch so EMS/fire/police get a street address.
3. When fire/police/EMS response is warranted AND location is known, call dispatch_simulated_unit \
(fire | police | ems) BEFORE telling the caller units are en route. Never announce a dispatch that \
is not in SIMULATED DISPATCHES in the live context. Dispatch uses the geocoded address when available.
4. For medical calls with a known address, you MAY call find_nearest_facility(ems) for situational awareness \
(do not read long facility names aloud — stay brief).
5. If NEW SIMULATED DISPATCH this turn: you MAY briefly say units are being sent (simulated only) — \
then IMMEDIATELY ask the next recommended question if any remain. Stay on the line.
6. Human dispatcher handoff is the END of the call — ONLY when HUMAN_HANDOFF_READY is true AND you \
have NOT already announced it. Say it once, briefly, then wrap up.
7. Do NOT mention a human dispatcher while questions remain unanswered.
8. Do NOT repeat dispatch or handoff announcements.
9. NEVER repeat a question the caller already answered — move to the next RECOMMENDED NEXT QUESTION.
10. On silence or a brief pause: do NOT say "keep talking", "go ahead", or "I'm listening". Ask the next \
RECOMMENDED NEXT QUESTION from CHRONOS LIVE CONTEXT to gather missing intake information.

Voice behavior: calm, brief, direct. ONE short sentence per turn when possible. No lists, no emojis."""

CHRONOS_INCOMPLETE_SHORT_PROMPT = """The caller paused briefly. Do NOT tell them to keep talking, "go ahead", or "I'm listening".

Read CHRONOS LIVE CONTEXT. If MISSING SLOTS remain, respond with ✓ followed by the RECOMMENDED NEXT QUESTION \
exactly (one short calm sentence). If intake is complete, ✓ ask if anything else is urgent.

Never repeat a question already answered in the transcript."""

CHRONOS_INCOMPLETE_LONG_PROMPT = """The caller has been quiet. Do NOT say "take your time" or "I'm listening" without helping.

Read CHRONOS LIVE CONTEXT. Respond with ✓ followed by the RECOMMENDED NEXT QUESTION to gather the next \
missing intake detail. One calm sentence only."""


def build_live_context_message(ctx: dict[str, Any]) -> dict[str, str]:
    """Render the per-turn grounding message injected before the caller's turn."""
    inc = ctx.get("incident_state", {})
    plan = ctx.get("sop_plan") or {}
    mem_lines = []
    for m in ctx.get("memory_results", [])[:4]:
        mem_lines.append(f"  - [{m.get('memory_type')}] {m.get('content', '')[:200]}")
    checklist_rows = []
    for c in ctx.get("sop_checklist", []):
        if not c.get("active"):
            continue
        status = "DONE" if c.get("resolved") else ("NEXT" if c.get("slot") == ctx.get("recommended_slot") else "OPEN")
        checklist_rows.append(f"  | {status} | {c.get('label', c.get('slot'))} | {c.get('question', '')} |")
    note_lines = []
    for n in ctx.get("structured_notes", [])[:12]:
        note_lines.append(f"  - [{n.get('category')}] {n.get('field')}: {n.get('value')}")
    dispatch_lines = []
    for d in ctx.get("dispatches", []):
        dispatch_lines.append(f"  - {d.get('unit_type', 'unit').upper()} @ {d.get('location', '?')} ({d.get('reason', '')})")
    new_disp = ctx.get("new_dispatches") or []
    new_disp_txt = ", ".join(d.get("unit_label") or d.get("unit_type", "") for d in new_disp) or "none"
    forbidden = ctx.get("forbidden_guidance", []) or []
    protocol = plan.get("protocol_title") or "Emergency intake"
    missing = ctx.get("missing_slots", []) or []
    body = f"""⟦CHRONOS LIVE CONTEXT⟧ (policy-computed; follow exactly)
Protocol: {protocol} ({plan.get('display_name') or inc.get('incident_type')})
Incident: {inc.get('incident_type')} | risk: {inc.get('risk_level')} | confidence: {inc.get('incident_confidence')}
Location (caller stated): {inc.get('location_raw')} (needs_confirmation={inc.get('location_needs_confirmation')})
Location (geocoded for dispatch): {inc.get('location_geocoded') or '(not yet resolved — call resolve_location_geocode before dispatch if landmark only)'}
Maps: {inc.get('location_maps_url') or 'n/a'}
Caller safety: {inc.get('caller_safety')} | Third-party risk: {inc.get('third_party_risk')}
Hazards: {', '.join(inc.get('hazards', [])) or 'none'}

SOP checklist (Status | Item | Question):
{chr(10).join(checklist_rows) or '  | — | (classifying…) | — |'}
INTAKE COMPLETE: {ctx.get('intake_complete')} | MISSING SLOTS: {', '.join(missing) or 'none'}

RECOMMENDED NEXT QUESTION: {ctx.get('recommended_question') or '(none — wrap up if intake complete)'}

Simulated dispatches (already sent — stay on line):
{chr(10).join(dispatch_lines) or '  - (none yet)'}
NEW DISPATCH THIS TURN: {new_disp_txt}

Structured notes (extracted facts):
{chr(10).join(note_lines) or '  - (none yet)'}

High-risk case (internal): {ctx.get('escalation_required')} — do NOT mention human dispatcher until intake complete.
HUMAN_HANDOFF_READY: {ctx.get('human_handoff_ready')} | already announced: {ctx.get('human_handoff_announced')}
FORBIDDEN THIS TURN (never say): {"human dispatcher, bringing in, handoff, transferring you" if not ctx.get('human_handoff_ready') else "(handoff allowed once)"}

Relevant institutional memory:
{chr(10).join(mem_lines) or '  - (none retrieved)'}
Do NOT: {' | '.join(forbidden) or 'violate any hard safety constraint'}

Speak ONE short, calm sentence. Priority: ask RECOMMENDED NEXT QUESTION if missing slots remain. \
If NEW DISPATCH this turn, mention it briefly then ask the next question. Human dispatcher ONLY when \
HUMAN_HANDOFF_READY and not yet announced."""
    return {"role": "system", "content": body}


# --------------------------------------------------------------------------- #
# Optional offline LLM (Nemotron over OpenAI-compatible endpoint)
# --------------------------------------------------------------------------- #
def _nemotron_client():
    from openai import OpenAI

    return OpenAI(
        api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
        base_url=os.getenv("NEMOTRON_LLM_URL", "http://localhost:8000/v1"),
    )


def _chat(messages: list[dict[str, str]], enable_thinking: bool = True, max_tokens: int = 700) -> str:
    """Single non-streaming completion. Returns content (reasoning stripped if a
    reasoning parser surfaces it as a separate field)."""
    client = _nemotron_client()
    resp = client.chat.completions.create(
        model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
        messages=messages,
        temperature=0.2,
        max_tokens=max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    msg = resp.choices[0].message
    return (getattr(msg, "content", None) or "").strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


_EXTRACTION_SYSTEM = (
    "You are extracting structured facts from a live emergency call partial transcript. "
    "Return ONLY valid JSON. No prose, no markdown fences."
)

_EXTRACTION_PROMPT = """\
Partial transcript so far:
{transcript}

Extract every fact the caller has stated so far. Return JSON:
{{
  "incident_type": "structure_fire|vehicle_crash|non_emergency_noise|medical|unknown",
  "location_raw": "the location the caller stated verbatim, or null",
  "location_certain": true_or_false,
  "caller_safety": "unknown|at_risk|evacuated|safe",
  "third_party_at_risk": true_or_false,
  "third_party_desc": "who and where, e.g. 'neighbor in 3B, third floor', or null",
  "hazards": ["smoke","fire","visible_fire","gas_smell","trapped_person","child","injury","weapon","breathing"],
  "escalation_required": true_or_false,
  "incident_upgraded_to": "possible_active_disturbance|active_threat|null"
}}

Rules:
- incident_type: NEVER vehicle_crash unless the caller describes an actual car/road crash.
  "accidentally" is NOT an accident/crash. Complaints about wrong classification are NOT incidents.
- Breathing difficulty/choking -> medical. Fire/smoke in a building/office -> structure_fire.
  If BOTH breathing trouble AND fire -> structure_fire (fire takes priority).
- location_raw: copy the caller's exact words (e.g. "Y Combinator office", "512 Pine Street apartment 3B").
- location_certain: false if they said near/maybe/around/or/think.
- caller_safety "evacuated"=caller got out; "safe"=caller says safe; "at_risk"=caller in danger.
- third_party_at_risk: true if ANYONE else may be in danger, even if caller is safe.
- hazards: only what was explicitly mentioned; include breathing for respiratory distress.
- incident_upgraded_to: use if noise escalated to disturbance or threat; otherwise null.
- Use null for unknown fields, never guess.\
"""


async def extract_state_llm(transcript: str) -> dict[str, Any] | None:
    """Backward-compatible wrapper — delegates to unified LLM extractor."""
    from chronos.llm_extractor import extract_call_state

    return await extract_call_state(transcript, partial=False)


_SOP_PLAN_SYSTEM = (
    "You are a 911 training SOP planner. Given an incident type and caller context, "
    "return ONLY valid JSON defining the structured data points to collect. "
    "Never include medical diagnosis or tactical instructions."
)

_SOP_PLAN_PROMPT = """\
Incident type: {incident_type}
Hazards detected: {hazards}
Caller transcript so far:
{transcript}

Retrieved SOP / prior-call memory (CCEC Cowley County Emergency Communications manual):
{memory}

Return JSON tailoring the intake checklist to THIS call, aligned with CCEC 911 SOPs:
{{
  "display_name": "human-readable incident label",
  "protocol_title": "short protocol name for dashboard (e.g. CCEC Fire & Smoke Protocol)",
  "slots": [
    {{
      "id": "stable_snake_case_id",
      "label": "short UI label",
      "question": "exact next question to ask the caller (one spoken sentence)",
      "priority": 1,
      "category": "location|safety|third_party|medical|vehicle|hazard|contact|general",
      "resolve_hints": ["words/phrases that mean this is answered"],
      "scope_when": null
    }}
  ]
}}

CCEC SOP requirements (must follow):
- SOP 303: ALWAYS include exact_location (verify address), callback_number (name + callback), per general call taking.
- Fire/medical (SOP 501/502): include caller_safety, trapped_person_status if anyone may be inside, injury/hazard slots as needed.
- Vehicle crash: exact_location, direction_of_travel, caller_safety, injury_status, vehicle_hazard.
- Medical: exact_location, consciousness, breathing — never diagnosis slots.
- Disturbance/threat (SOP 404/705): caller_safety, weapons/injury info, suspect location.
- Use stable ids: exact_location, caller_safety, trapped_person_status, callback_number when applicable.
- scope_when: "third_party_active" for last_known_location only.
- 5-8 slots. Questions must match real PSAP call-taking style: brief, one question at a time.
- resolve_hints: 3-6 substring cues from likely caller answers."""


async def generate_sop_plan_llm(
    incident_type: str,
    transcript: str,
    hazards: list[str],
    memory_snippets: list[str],
) -> dict[str, Any] | None:
    """LLM generates a contextual SOP plan for the classified incident."""
    import asyncio

    try:
        prompt = _SOP_PLAN_PROMPT.format(
            incident_type=incident_type,
            hazards=", ".join(hazards) or "none",
            transcript=transcript[-1500:],
            memory="\n".join(f"- {m[:180]}" for m in memory_snippets[:4]) or "- (none)",
        )
        raw = await asyncio.to_thread(
            _chat,
            [
                {"role": "system", "content": _SOP_PLAN_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            False,
            700,
        )
        return _extract_json(raw)
    except Exception:
        return None


_SLOT_RESOLVE_PROMPT = """\
Transcript:
{transcript}

Checklist slots to evaluate:
{slots}

For each slot, decide if the caller has ALREADY provided enough information in the transcript —
even if the dispatcher never asked that question directly.
Return JSON only:
{{"resolved_slots": ["slot_id", ...]}}

Rules:
- Mark resolved if clearly stated anywhere in the transcript; do not require the exact question to have been asked.
- exact_location: resolved if street address, apartment/hotel/room number, or full landmark given.
- caller_safety: resolved if caller says safe, safe for now, outside, or at risk/imminent danger.
- callback_number: resolved if caller gave name AND phone/callback digits.
- threat_description: resolved if robbery, break-in, banging on door, or threat described.
- suspect_location: resolved if caller says suspect at door, trying to break in, or inside.
- weapon_info: resolved if weapon mentioned OR caller confirms no weapon.
- trapped_person_status: resolved ONLY if clearly no one inside OR everyone accounted for.
- Do NOT resolve trapped_person_status just because caller evacuated.
- For ANY checklist slot id (including custom LLM slots): mark resolved if the caller clearly answered \
that topic anywhere in the transcript, even with informal wording (e.g. "worse and worse" answers bleeding severity).
"""

_SLOT_DISPLAY_PROMPT = """\
Transcript:
{transcript}

The following checklist slots are RESOLVED — the caller already provided enough information. \
Write one concise operator-facing summary per slot (max 120 chars). Paraphrase clearly for a \
911 call-taker; use the caller's facts but not raw transcript dumps.

RESOLVED slots (only output these keys):
{slots}

Return JSON only:
{{"slot_values": {{"slot_id": "intelligent operator summary", ...}}}}

Rules:
- ONLY include slot ids listed above. Never invent values for open/unresolved slots.
- Never use generic placeholders like "Confirmed", "Weapon mentioned", or "Injuries reported".
- exact_location / location: address, building, room, or landmark as stated.
- caller_safety: where caller is and whether safe or still at risk.
- threat_description / suspect_location / weapon_info: plain language from caller.
- callback_number: name and phone digits if given.
- consciousness / breathing / injury_status: patient status in caller's words.
- Custom slot ids: summarize only what the caller said about that specific question.
"""


async def resolve_slot_display_values_llm(
    transcript: str,
    slots: list[dict[str, str]],
    resolved_ids: set[str],
) -> dict[str, str]:
    """Ask LLM for Known/ask summaries — resolved slots only."""
    import asyncio

    if not resolved_ids or not slots or not transcript.strip():
        return {}
    try:
        lines = []
        for s in slots:
            sid = s.get("id") or s.get("slot") or ""
            if sid not in resolved_ids:
                continue
            label = s.get("label") or sid.replace("_", " ")
            question = s.get("question") or ""
            lines.append(f"- {sid} ({label}): {question}")
        if not lines:
            return {}
        prompt = _SLOT_DISPLAY_PROMPT.format(
            transcript=transcript[-2500:],
            slots="\n".join(lines),
        )
        raw = await asyncio.to_thread(
            _chat,
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            False,
            500,
        )
        data = _extract_json(raw)
        if not data or not isinstance(data.get("slot_values"), dict):
            return {}
        out: dict[str, str] = {}
        for k, v in data["slot_values"].items():
            key = str(k)
            if key not in resolved_ids:
                continue
            val = str(v or "").strip()
            if val and val.lower() not in ("null", "none", "unknown", "n/a", "confirmed", "provided"):
                if not _GENERIC_SLOT_VALUE(val):
                    out[key] = val
        return out
    except Exception:
        return {}


def _GENERIC_SLOT_VALUE(val: str) -> bool:
    low = val.lower().strip()
    generic = {
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
    }
    return low in generic or low.endswith(" mentioned") or low.endswith(" reported")


async def resolve_slots_llm(transcript: str, slot_ids: list[str]) -> set[str]:
    """Ask LLM which checklist slots are already answered in the transcript."""
    import asyncio

    if not slot_ids:
        return set()
    try:
        slots_txt = "\n".join(f"- {s}" for s in slot_ids)
        prompt = _SLOT_RESOLVE_PROMPT.format(transcript=transcript[-2000:], slots=slots_txt)
        raw = await asyncio.to_thread(
            _chat,
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            False,
            300,
        )
        data = _extract_json(raw)
        if data and isinstance(data.get("resolved_slots"), list):
            return {str(s) for s in data["resolved_slots"]}
    except Exception:
        pass
    return set()


def author_patch_rationale(failure: dict[str, Any], ops: list[dict[str, Any]]) -> str | None:
    """Ask Nemotron to author a one-paragraph patch rationale (text only)."""
    try:
        prompt = (
            "You are the Chronos policy patch generator. Explain, in 2 calm sentences, why this "
            "structured policy patch makes a simulated 911 copilot SAFER. Do not output code.\n\n"
            f"Failure: {json.dumps(failure)}\n\nPatch operations: {json.dumps(ops)}"
        )
        out = _chat(
            [{"role": "system", "content": "You write concise, safety-focused rationales."},
             {"role": "user", "content": prompt}],
            enable_thinking=False,
            max_tokens=300,
        )
        return out or None
    except Exception:
        return None


def classify_failure_llm(
    transcript: str, event_trace: str, failed_assertions: list[str], expected: str
) -> dict[str, Any] | None:
    """Optional LLM failure classification returning the taxonomy JSON."""
    try:
        prompt = (
            "You are classifying failures from Cekura voice-agent evaluations. Allowed failure "
            "types: MISSING_CRITICAL_QUESTION, WRONG_BRANCH_CLOSURE, MEMORY_RETRIEVAL_FAILURE, "
            "MEMORY_OVERUSE, WRONG_INTERRUPTION, MISSED_INTERRUPTION, SOP_VIOLATION, BAD_ESCALATION, "
            "LATENCY_FAILURE.\n\n"
            f"Transcript:\n{transcript}\n\nEvent trace:\n{event_trace}\n\n"
            f"Failed assertions:\n{failed_assertions}\n\nExpected behavior:\n{expected}\n\n"
            'Return JSON only: {"failure_type":"...","root_cause":"...","target_policy":"...",'
            '"severity":"critical|high|medium|low","similar_memory_query":"..."}'
        )
        out = _chat(
            [{"role": "system", "content": "You return strict JSON, no prose."},
             {"role": "user", "content": prompt}],
            enable_thinking=True,
            max_tokens=600,
        )
        return _extract_json(out)
    except Exception:
        return None
