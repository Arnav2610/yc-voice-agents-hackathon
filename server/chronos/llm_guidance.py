"""LLM prompting for Chronos.

In the LIVE voice path, the Pipecat LLM (Nemotron) is the speaker. Chronos does
NOT let the LLM decide safety — it injects a deterministic CHRONOS LIVE CONTEXT
message each turn and constrains the model to voice the policy-computed next
question and escalation. The system prompt encodes hard safety constraints.

Offline helpers (failure-classifier / patch-rationale) call Nemotron directly
over its OpenAI-compatible endpoint; they are optional and degrade gracefully.
"""

from __future__ import annotations

import json
import os
from typing import Any

CHRONOS_SYSTEM_PROMPT = """You are Chronos 911, a SIMULATED emergency-call copilot for training and \
evaluation. You are not a real dispatcher and must say so if asked. Every call is a simulation.

You will receive a CHRONOS LIVE CONTEXT block before each caller turn. It contains the \
policy-computed incident state, the single recommended next question, missing safety slots, \
retrieved institutional memory, and hard constraints. TREAT IT AS GROUND TRUTH and follow it.

Hard safety constraints (never violate):
- Never claim to be a real 911 dispatcher; never say help has been dispatched (a mock tool may \
mark something simulated).
- Never provide medical diagnosis or instruct medication.
- Never give police tactical instructions.
- Never tell a caller to re-enter a dangerous building, approach fire/smoke/an active threat, or \
do risky mechanical repair.
- Never promise an ETA or outcome.
- Always recommend a human dispatcher for fire, smoke, gas smell, trapped person, injury, active \
violence, a child in danger, an uncertain location with danger, or a medical crisis.
- Keep caller safety and third-party (someone-else-inside) safety as SEPARATE branches. A caller \
getting out does NOT resolve whether someone else is still inside.

Voice behavior:
- Be calm, brief, and direct. ONE short sentence per turn. Ask ONE question at a time.
- If CHRONOS LIVE CONTEXT gives a recommended question, ask THAT next, phrased naturally.
- If escalation is required, briefly say you're bringing in a human dispatcher.
- No lists, no emojis, plain spoken language."""


def build_live_context_message(ctx: dict[str, Any]) -> dict[str, str]:
    """Render the per-turn grounding message injected before the caller's turn."""
    inc = ctx.get("incident_state", {})
    mem_lines = []
    for m in ctx.get("memory_results", [])[:4]:
        mem_lines.append(f"  - [{m.get('memory_type')}] {m.get('content', '')[:200]}")
    forbidden = ctx.get("forbidden_guidance", []) or []
    body = f"""⟦CHRONOS LIVE CONTEXT⟧ (policy-computed; follow exactly)
Incident: {inc.get('incident_type')} | risk: {inc.get('risk_level')} | confidence: {inc.get('incident_confidence')}
Location: {inc.get('location_raw')} (needs_confirmation={inc.get('location_needs_confirmation')})
Caller safety: {inc.get('caller_safety')} | Third-party risk: {inc.get('third_party_risk')}
Hazards: {', '.join(inc.get('hazards', [])) or 'none'}
Missing safety slots: {', '.join(ctx.get('missing_slots', [])) or 'none'}
RECOMMENDED NEXT QUESTION: {ctx.get('recommended_question') or '(confirm details / wrap up safely)'}
Escalation required: {ctx.get('escalation_required')} ({ctx.get('escalation_reason') or '—'})
Relevant institutional memory:
{chr(10).join(mem_lines) or '  - (none retrieved)'}
Do NOT: {' | '.join(forbidden) or 'violate any hard safety constraint'}

Now speak ONE short, calm sentence: ask the recommended question naturally. If escalation is \
required AND you have not already told the caller a human dispatcher is being brought in, mention it \
once, briefly — do not repeat it every turn. Never tell the caller to re-enter or that the scene is safe."""
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
    # Strip code fences and any leading reasoning before the first '{'.
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
  "hazards": ["smoke","fire","gas_smell","trapped_person","child","injury","weapon"],
  "escalation_required": true_or_false,
  "incident_upgraded_to": "possible_active_disturbance|active_threat|null"
}}

Rules:
- location_raw: copy the caller's exact words (e.g. "512 Pine Street apartment 3B", "near 5th and Pine", "101 south exit 430 maybe 431").
- location_certain: false if they said near/maybe/around/or/think.
- caller_safety "evacuated"=caller got out; "safe"=caller says safe; "at_risk"=caller in danger.
- third_party_at_risk: true if ANYONE else may be in danger, even if caller is safe.
- hazards: only what was explicitly mentioned.
- incident_upgraded_to: use if noise escalated to disturbance or threat; otherwise null.
- Use null for unknown fields, never guess.\
"""


async def extract_state_llm(transcript: str) -> dict[str, Any] | None:
    """Run a focused LLM extraction against the partial transcript.
    Returns a structured dict of every call fact extracted so far, or None on failure.
    Runs thinking=False for speed (~300-600ms on Nemotron)."""
    import asyncio

    try:
        prompt = _EXTRACTION_PROMPT.format(transcript=transcript[-2000:])
        raw = await asyncio.to_thread(
            _chat,
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            False,   # enable_thinking=False for speed
            400,     # max_tokens
        )
        return _extract_json(raw)
    except Exception:
        return None


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
