"""Chronos interaction kernel — the per-call orchestrator.

Wires together LLM extraction, SOP engine, floor controller, memory retrieval,
and mock tools. Incident classification, hazards, location, and safety branches
come ONLY from the LLM extractor (live Nemotron or offline mock for regression).

Policy YAML still governs escalation thresholds, third-party branch-closure
guards, and SOP slot scoping — but never keyword classification.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from chronos import config
from chronos.events import EventStore
from chronos.floor_controller import FloorController
from chronos.memory_retrieval import ChronosMemoryClient
from chronos.mocks import dispatch_unit, escalate_to_human, resolve_location
from chronos.sop_engine import SOPEngine
from chronos.state import CallState, DispatchRecord, StructuredNote

_RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_THREAT_TYPES = frozenset({"active_threat", "possible_active_disturbance"})
_NOMINAL_MS_PER_TURN = 1200

_FORBIDDEN_PATTERNS = [
    "go back inside", "go back in", "re-enter", "reenter", "head back in",
    "the scene is safe", "building is safe", "it is safe to go", "open the hood",
    "dispatched", "help is on the way", "ambulance will arrive",
]

_PREMATURE_HANDOFF_RE = re.compile(
    r"(?:\s*[,.\-–—]?\s*)?"
    r"(?:(?:i'?m\s+)?(?:also\s+)?(?:bringing\s+in|connecting\s+you\s+to|transferring\s+you\s+to)\s+"
    r"(?:a\s+)?human\s+dispatcher[^.!?]*[.!?]?|"
    r"(?:also\s+)?(?:bringing\s+in\s+)?(?:a\s+)?human\s+dispatcher[^.!?]*[.!?]?)",
    re.IGNORECASE,
)


@dataclass
class TurnResult:
    recommended_question: str | None
    recommended_slot: str | None
    escalation_required: bool
    escalation_reason: str | None
    guidance_text: str
    floor_action: dict[str, Any]
    memory_added: list[dict[str, Any]] = field(default_factory=list)


class ChronosKernel:
    def __init__(
        self,
        call_id: str,
        scenario_id: str | None = None,
        memory_client: ChronosMemoryClient | None = None,
        event_store: EventStore | None = None,
        slow_memory_ms: int = 0,
        use_llm_extraction: bool = True,
    ) -> None:
        self.state = CallState(call_id=call_id, scenario_id=scenario_id)
        self.state.started_ms = config.now_ms()
        self._t0 = time.monotonic()
        self.memory = memory_client or ChronosMemoryClient(force_local=True)
        self.events = event_store
        self.slow_memory_ms = slow_memory_ms
        # Live voice: async Nemotron extraction. Offline regression: sync mock LLM.
        self.use_llm_extraction = use_llm_extraction
        self._mock_extractor = not use_llm_extraction
        self.sop = SOPEngine()
        self.floor = FloorController()
        self._turn_index = 0
        # Real-time (partial-transcript) processing guards + debounce state.
        self._processing_final = False
        self._last_partial_sig: tuple | None = None
        self._last_prefetch_sig: tuple | None = None
        self._last_live_write_ms = 0
        # LLM extraction: debounced partials + chained re-runs (never cancel in-flight).
        self._extraction_seq = 0
        self._extraction_task: Any = None
        self._partial_debounce_task: Any = None
        self._pending_partial_transcript: str = ""
        self._last_extracted_transcript: str = ""
        self._last_extraction_result: dict[str, Any] | None = None
        self._extraction_requeue = False
        self._words_at_last_extraction = 0
        self._pending_extraction_is_final = False
        # Dynamic SOP plan generation when incident type is first classified.
        self._sop_plan_seq = 0
        self._sop_plan_task: Any = None
        self._sop_plan_for_type: str | None = None
        self._slot_resolve_task: Any = None
        self._new_dispatches_this_turn: list[dict[str, Any]] = []
        # Tell the SOP engine whether to apply the slot-skip heuristic (live only).
        self.state._use_slot_skip = use_llm_extraction  # type: ignore[attr-defined]
        self._emit("call_start", {"disclaimer": config.SIMULATION_DISCLAIMER})
        import asyncio as _asyncio
        _asyncio.create_task(self._retrieve_memory_on_start())

    # --- event helper -------------------------------------------------------
    # Event types that always flush live.json immediately (not throttled).
    _FORCE_LIVE = {"call_start", "final_transcript", "agent_guidance", "background_speech", "call_complete"}

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.emit(event_type, data, self.state.call_id, self.state.scenario_id)
            self._write_live(force=event_type in self._FORCE_LIVE)

    def _write_live(self, force: bool = False) -> None:
        """Continuously mirror this call's live state to runtime/live.json so a
        dashboard in ANY process (in-thread or standalone) can render it. The
        in-process dashboard prefers the live kernel; cross-process falls back
        to this file. Throttled to ~120ms unless forced."""
        if self.events is None:
            return
        now = config.now_ms()
        if not force and (now - self._last_live_write_ms) < 120:
            return
        self._last_live_write_ms = now
        try:
            payload = {
                "snapshot": self.state.snapshot(),
                "events": self.events.list(self.state.call_id)[-100:],
                "disclaimer": config.SIMULATION_DISCLAIMER,
                "ts": now,
            }
            tmp = config.RUNTIME_DIR / "live.json.tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            tmp.replace(config.RUNTIME_DIR / "live.json")
        except Exception:
            pass

    # --- main turn handler --------------------------------------------------
    async def process_caller_turn(self, text: str) -> TurnResult:
        self._processing_final = True
        try:
            return await self._process_caller_turn(text)
        finally:
            self._processing_final = False
            # Reset partial debounce so the next turn's partials re-fire cleanly.
            self._last_partial_sig = None
            self._last_prefetch_sig = None

    async def _process_caller_turn(self, text: str) -> TurnResult:
        import asyncio as _asyncio

        text = (text or "").strip()
        self._turn_index += 1
        self._new_dispatches_this_turn = []
        self.state.turns.append(text)
        self.state.partial_buffer.clear()
        self._emit("final_transcript", {"text": text, "turn": self._turn_index})

        inc = self.state.incident

        # 1) LLM extraction — sole source of incident state.
        full_transcript = self.state.cumulative_text
        if self._mock_extractor:
            from chronos.llm_mock import mock_extract_call_state

            extracted = mock_extract_call_state(full_transcript, partial=False)
            self._apply_extracted_state(extracted, partial=False)
        elif self.use_llm_extraction:
            await self._await_turn_extraction(full_transcript)

        # 2) Dynamic SOP plan + slot resolution (live path).
        if inc.incident_type:
            if self.use_llm_extraction:
                self._maybe_generate_sop_plan(inc.incident_type)
            else:
                self.sop.ensure_plan(self.state, inc.incident_type)

        # 3) SOP checklist (initial pass before slot resolution).
        prev_recommended = self.state.recommended_slot
        self.sop.update_checklist(self.state)
        slot = self.state.recommended_slot
        if slot:
            self.state.asked_slots.add(slot)
            self.state.asked_slot_counts[slot] = self.state.asked_slot_counts.get(slot, 0) + 1
        for s in inc.resolved_slots:
            if s in self.state.asked_slot_counts:
                del self.state.asked_slot_counts[s]
        if self.use_llm_extraction:
            import asyncio as _asyncio
            _asyncio.create_task(self._resolve_slots_async())
        self._sync_resolved_slots(allow_safety=True)
        self.sop.update_checklist(self.state)
        self._emit(
            "sop_checklist_update",
            {
                "checklist": self.state.checklist_dicts(),
                "sop_plan": self.state.sop_plan,
                "missing_slots": inc.missing_slots,
                "recommended_slot": self.state.recommended_slot,
                "recommended_question": self.state.recommended_question,
            },
        )

        # 4) Speculative location resolution (reversible read) + memory retrieval.
        if inc.location_raw:
            loc = resolve_location(inc.location_raw)
            self._emit("tool_prefetch", {"tool": "resolve_location", "result": loc})

        if len(self.state.memory.results) >= 3:
            memory_added = []
        else:
            import asyncio as _asyncio
            try:
                memory_added = await _asyncio.wait_for(self._retrieve_memory(), timeout=2.0)
            except TimeoutError:
                memory_added = []

        # 5) Simulated unit dispatch (while staying on line) + escalation policy.
        self._refresh_derived_notes()
        self._maybe_dispatch_units()
        self._decide_escalation()
        if self.state.human_handoff_ready and not self.state.human_handoff_announced:
            handoff = escalate_to_human(inc.escalation_reason or "intake complete — high-risk case")
            self._emit("human_handoff_ready", {"reason": inc.escalation_reason, "handoff": handoff})

        self._emit("safety_signal", {
            "hazards": inc.hazards,
            "third_party_risk": inc.third_party_risk,
            "caller_safety": inc.caller_safety,
            "risk_level": inc.risk_level,
        })

        # 6) Floor action.
        action = self.floor.decide(self.state, slow_memory=bool(self.slow_memory_ms))
        self.state.floor_actions.append(action.to_dict())
        self._emit("floor_action", action.to_dict())

        # 7) Timing for first critical guidance.
        if inc.escalation_required and self.state.first_critical_guidance_ms is None:
            self.state.first_critical_guidance_ms = self._turn_index * _NOMINAL_MS_PER_TURN

        # 8) Guidance text (safe fallback + what the simulator scores).
        guidance = self._guidance_text()
        result = TurnResult(
            recommended_question=self.state.recommended_question,
            recommended_slot=self.state.recommended_slot,
            escalation_required=inc.escalation_required,
            escalation_reason=inc.escalation_reason,
            guidance_text=guidance,
            floor_action=action.to_dict(),
            memory_added=[m.to_dict() for m in memory_added],
        )
        return result

    async def process_background_speech(self, text: str) -> None:
        """A third party (not the caller) is audible. Extract from that utterance only."""
        text = (text or "").strip()
        if self._mock_extractor:
            from chronos.llm_mock import mock_extract_call_state

            extracted = mock_extract_call_state(text, partial=False)
        else:
            from chronos.llm_extractor import extract_call_state

            extracted = await extract_call_state(text, partial=False)

        if not extracted:
            self.state.ignored_background = True
            self._emit("background_speech", {"text": text, "safety_critical": False})
            return

        safety_critical = bool(extracted.get("hazards")) or extracted.get("third_party_at_risk")
        self._emit("background_speech", {"text": text, "safety_critical": safety_critical})
        if not safety_critical:
            self.state.ignored_background = True
            return
        self.state.background_safety_handled = True
        for h in extracted.get("hazards") or []:
            self.state.incident.add_hazard(h)
        if extracted.get("third_party_at_risk"):
            self.state.incident.third_party_risk = "active"
        self.sop.update_checklist(self.state)
        if self.state.recommended_slot:
            self.state.asked_slots.add(self.state.recommended_slot)
        self._decide_escalation()
        self._emit(
            "safety_signal",
            {
                "source": "background",
                "hazards": self.state.incident.hazards,
                "third_party_risk": self.state.incident.third_party_risk,
                "risk_level": self.state.incident.risk_level,
            },
        )

    async def observe_partial(self, partial_text: str) -> None:
        """Real-time mid-utterance processing: instant hints + debounced LLM extraction.

        Never cancels an in-flight extraction — chains a follow-up when the caller keeps
        speaking so Nemotron results actually land while audio is still streaming.
        """
        if self._processing_final:
            return
        partial_text = (partial_text or "").strip()
        if len(partial_text) < 2:
            return

        provisional = (self.state.cumulative_text + " " + partial_text).strip()
        self._pending_partial_transcript = provisional

        if self._apply_partial_hints(provisional):
            self._emit_live_state(partial=True)

        if self._mock_extractor:
            from chronos.llm_mock import mock_extract_call_state

            extracted = mock_extract_call_state(provisional, partial=True)
            self._apply_extracted_state(extracted, partial=True)
            self._emit_live_state(partial=True)
            return

        if self.use_llm_extraction:
            self._schedule_debounced_extraction()

    def _schedule_debounced_extraction(self) -> None:
        import asyncio as _asyncio

        if self._partial_debounce_task and not self._partial_debounce_task.done():
            self._partial_debounce_task.cancel()
        self._partial_debounce_task = _asyncio.create_task(self._debounced_extraction())

    async def _debounced_extraction(self) -> None:
        import asyncio as _asyncio

        await _asyncio.sleep(0.18)
        if self._processing_final:
            return
        transcript = self._pending_partial_transcript
        if not transcript or transcript == self._last_extracted_transcript:
            return
        if self._extraction_task and not self._extraction_task.done():
            self._extraction_requeue = True
            return
        word_count = len(transcript.split())
        new_words = word_count - self._words_at_last_extraction
        if new_words < 2 and self._words_at_last_extraction > 0:
            return
        self._words_at_last_extraction = word_count
        seq = self._extraction_seq + 1
        self._extraction_seq = seq
        self._extraction_task = _asyncio.create_task(
            self._run_extraction_chain(transcript, seq, partial=True)
        )

    async def _run_extraction_chain(self, transcript: str, seq: int, *, partial: bool) -> None:
        try:
            await self._run_extraction(transcript, seq, partial=partial)
            self._last_extracted_transcript = transcript
        finally:
            if self._processing_final:
                return
            pending = self._pending_partial_transcript
            if self._extraction_requeue or (pending and pending != transcript):
                self._extraction_requeue = False
                self._words_at_last_extraction = max(0, self._words_at_last_extraction - 2)
                self._schedule_debounced_extraction()

    def _apply_partial_hints(self, text: str) -> bool:
        """Instant dashboard updates from streaming speech (before LLM returns)."""
        from chronos.partial_hints import hints_from_text

        notes = hints_from_text(text, self._turn_index)
        if not notes:
            return False
        before = (
            self.state.incident.incident_type,
            self.state.incident.location_raw,
            tuple(self.state.incident.hazards),
            len(self.state.structured_notes),
        )
        self._merge_structured_notes([n.to_dict() for n in notes], partial=True)
        if any(n.field == "weapon_type" for n in notes):
            self.state.incident.add_hazard("weapon")
        if any(n.category == "location" for n in notes):
            loc_note = next(n for n in notes if n.category == "location")
            if not self.state.incident.location_raw:
                self.state.incident.location_raw = loc_note.value
                self.state.incident.location_needs_confirmation = False
        self._sync_resolved_slots(allow_safety=False)
        if self.state.incident.incident_type is None and any(
            n.category == "threat" for n in notes
        ):
            # Hints alone do not classify — but seed policy plan once LLM confirms.
            pass
        self._refresh_derived_notes()
        after = (
            self.state.incident.incident_type,
            self.state.incident.location_raw,
            tuple(self.state.incident.hazards),
            len(self.state.structured_notes),
        )
        return before != after

    def _emit_live_state(self, *, partial: bool) -> None:
        """Push checklist + hypothesis to dashboard immediately."""
        inc = self.state.incident
        if inc.incident_type and not self.state.sop_plan:
            self.sop.ensure_plan(self.state, inc.incident_type)
        self.sop.update_checklist(self.state)
        self._emit("incident_hypothesis", {
            "incident_type": inc.incident_type,
            "risk_level": inc.risk_level,
            "location_raw": inc.location_raw,
            "third_party_risk": inc.third_party_risk,
            "recommended_slot": self.state.recommended_slot,
            "escalation_required": inc.escalation_required,
            "partial": partial,
        })
        self._emit("sop_checklist_update", {
            "checklist": self.state.checklist_dicts(),
            "sop_plan": self.state.sop_plan,
            "missing_slots": inc.missing_slots,
            "recommended_slot": self.state.recommended_slot,
            "recommended_question": self.state.recommended_question,
            "partial": partial,
        })
        self._write_live(force=True)

        prefetch_sig = (inc.incident_type, tuple(sorted(inc.hazards)))
        if inc.incident_type and prefetch_sig != self._last_prefetch_sig:
            self._last_prefetch_sig = prefetch_sig
            import asyncio as _asyncio
            _asyncio.create_task(self._retrieve_memory())
        if inc.location_raw:
            loc = resolve_location(inc.location_raw)
            self._emit("tool_prefetch", {"tool": "resolve_location", "result": loc, "partial": partial})

    async def _run_extraction(self, transcript: str, seq: int, partial: bool = True) -> None:
        """Background LLM extraction — updates state when it lands."""
        from chronos.llm_extractor import extract_call_state

        result = await extract_call_state(transcript, partial=partial)
        if result and seq == self._extraction_seq:
            self._last_extraction_result = result
            apply_partial = partial and not self._pending_extraction_is_final
            self._pending_extraction_is_final = False
            self._apply_extracted_state(result, partial=apply_partial)
            self._emit_live_state(partial=apply_partial)

    def _invalidate_sop_plan(self) -> None:
        self.state.sop_plan = None
        self._sop_plan_for_type = None

    def _maybe_generate_sop_plan(self, incident_type: str | None) -> None:
        """Fire-and-forget LLM SOP plan when incident type is first classified."""
        if not incident_type or self._mock_extractor:
            return
        if self._sop_plan_for_type == incident_type:
            return
        import asyncio as _asyncio
        seq = self._sop_plan_seq + 1
        self._sop_plan_seq = seq
        if self._sop_plan_task and not self._sop_plan_task.done():
            self._sop_plan_task.cancel()
        self._sop_plan_task = _asyncio.create_task(self._run_sop_plan_generation(incident_type, seq))

    async def _run_sop_plan_generation(self, incident_type: str, seq: int) -> None:
        from chronos.llm_guidance import generate_sop_plan_llm
        from chronos.sop_planner import merge_plans, parse_llm_plan, plan_from_policy

        inc = self.state.incident
        mem = [r.content for r in self.state.memory.results[:4]]
        base = plan_from_policy(incident_type)
        # Seed checklist immediately from policy while LLM plan loads.
        self.sop.ensure_plan(self.state, incident_type)
        self.sop.update_checklist(self.state)
        self._write_live(force=True)

        data = await generate_sop_plan_llm(
            incident_type,
            self.state.cumulative_text,
            inc.hazards,
            mem,
        )
        if seq != self._sop_plan_seq or not data:
            return
        dynamic = parse_llm_plan(data, incident_type)
        if not dynamic:
            return
        merged = merge_plans(base, dynamic)
        self.sop.apply_dynamic_plan(self.state, merged)
        self._sop_plan_for_type = incident_type
        self.sop.update_checklist(self.state)
        self._emit(
            "sop_plan_ready",
            {"sop_plan": self.state.sop_plan, "source": merged.source},
        )
        self._write_live(force=True)

    async def _resolve_slots_async(self) -> None:
        """Ask LLM which checklist slots are already answered in the transcript."""
        if not self.use_llm_extraction:
            return
        plan = self.state.sop_plan
        if not plan or not plan.get("slots"):
            return
        unresolved = [
            s["id"]
            for s in plan["slots"]
            if s["id"] not in (self.state.incident.resolved_slots or [])
        ]
        if not unresolved:
            return
        from chronos.llm_guidance import resolve_slots_llm
        import asyncio as _asyncio
        try:
            resolved = await _asyncio.wait_for(
                resolve_slots_llm(self.state.cumulative_text, unresolved[:10]),
                timeout=2.0,
            )
            if resolved:
                merged = set(self.sop._llm_resolved) | resolved
                self.sop.set_llm_resolved(merged)
                self.sop.update_checklist(self.state)
                self._emit_live_state(partial=False)
        except Exception:
            pass

    def _accept_incident_type(self, new_type: str) -> bool:
        """Prevent classifying a threat/robbery call back to structure fire."""
        inc = self.state.incident
        anchor = inc.incident_type
        if inc.upgraded_to in _THREAT_TYPES:
            anchor = inc.upgraded_to
        if anchor in _THREAT_TYPES and new_type not in _THREAT_TYPES:
            return False
        if anchor in _THREAT_TYPES and new_type == "structure_fire":
            return False
        return True

    def _sync_resolved_slots(self, allow_safety: bool = True) -> None:
        from chronos.slot_inference import infer_resolved_slots

        inferred = infer_resolved_slots(self.state, allow_safety=allow_safety)
        if inferred:
            merged = set(self.sop._llm_resolved) | inferred
            self.sop.set_llm_resolved(merged)

    def _apply_extracted_state(self, extracted: dict[str, Any], partial: bool = False) -> None:
        """Apply LLM-extracted structured state — the only classification path."""
        if not extracted:
            return
        inc = self.state.incident
        changed = False
        prev_type = inc.incident_type

        upgraded = extracted.get("incident_upgraded_to")
        if upgraded and upgraded not in ("null", None):
            if upgraded != inc.upgraded_to or inc.incident_type != upgraded:
                inc.upgraded_to = upgraded
                inc.incident_type = upgraded
                inc.incident_confidence = max(inc.incident_confidence, float(extracted.get("incident_confidence") or 0.9))
                changed = True
                self._invalidate_sop_plan()

        t = extracted.get("incident_type")
        if t and t not in ("unknown", "null") and self._accept_incident_type(t):
            if t != inc.incident_type:
                if prev_type and prev_type != t:
                    self._invalidate_sop_plan()
                inc.incident_type = t
                conf = float(extracted.get("incident_confidence") or 0.85)
                inc.incident_confidence = max(inc.incident_confidence, conf)
                changed = True

        loc = extracted.get("location_raw")
        if loc and loc not in ("null", None):
            certain = bool(extracted.get("location_certain"))
            if loc != inc.location_raw:
                inc.location_raw = loc
                changed = True
            inc.location_needs_confirmation = not certain
            inc.location_confidence = 0.9 if certain else 0.6

        for h in (extracted.get("hazards") or []):
            if h and inc.add_hazard(h):
                changed = True

        if extracted.get("third_party_at_risk"):
            if inc.third_party_risk != "active":
                inc.third_party_risk = "active"
                changed = True

        if extracted.get("correction_detected"):
            inc.correction_detected = True

        if extracted.get("reentry_intent"):
            inc.reentry_intent = True
            self._emit("policy_violation_warning", {"caller_unsafe_intent": "wants_to_reenter"})

        rl = extracted.get("risk_level")
        if rl and rl != "unknown":
            inc.risk_level = max_risk(inc.risk_level, rl)

        if partial:
            cs = extracted.get("caller_safety")
            if cs == "at_risk":
                inc.caller_safety = "at_risk"
                changed = True
            if extracted.get("escalation_required"):
                inc.escalation_required = True
                changed = True
            resolved = extracted.get("resolved_slots") or []
            if resolved:
                from chronos.slot_inference import info_slots_only

                info = info_slots_only({str(s) for s in resolved})
                if info:
                    merged = set(self.sop._llm_resolved) | info
                    self.sop.set_llm_resolved(merged)
                    changed = True
        else:
            cs = extracted.get("caller_safety")
            if cs and cs not in ("unknown", "null", None):
                mapping = {"evacuated": "self_evacuated", "safe": "resolved", "at_risk": "at_risk"}
                inc.caller_safety = mapping.get(cs, inc.caller_safety)
                changed = True

            if extracted.get("third_party_resolved") or extracted.get("everyone_accounted_for"):
                guard = self._third_party_guard()
                blocked = "caller_personally_evacuated" in guard and inc.caller_safety == "self_evacuated"
                blocked = blocked or (
                    "caller_says_i_am_safe" in guard and inc.caller_safety == "resolved"
                )
                if not blocked:
                    inc.third_party_risk = "resolved"
                    changed = True
            elif inc.third_party_risk == "active" and inc.caller_safety in ("self_evacuated", "resolved"):
                # Baseline branch-closure bug: caller safety can wrongly resolve third-party
                # risk unless policy guard blocks it (WRONG_BRANCH_CLOSURE patch target).
                guard = self._third_party_guard()
                blocked = "caller_personally_evacuated" in guard and inc.caller_safety == "self_evacuated"
                blocked = blocked or (
                    "caller_says_i_am_safe" in guard and inc.caller_safety == "resolved"
                )
                if not blocked:
                    inc.third_party_risk = "resolved"
                    changed = True

            resolved = extracted.get("resolved_slots") or []
            if resolved:
                merged = set(self.sop._llm_resolved) | {str(s) for s in resolved}
                self.sop.set_llm_resolved(merged)

        extracted_notes = extracted.get("structured_notes") or []
        self._merge_structured_notes(extracted_notes, partial=partial)
        self._sync_resolved_slots(allow_safety=not partial)

        if changed or not partial or extracted_notes or extracted.get("resolved_slots"):
            if inc.incident_type:
                self._maybe_generate_sop_plan(inc.incident_type)
                if not self.state.sop_plan:
                    self.sop.ensure_plan(self.state, inc.incident_type)
            self.sop.update_checklist(self.state)
            self._maybe_dispatch_units()
            self._decide_escalation(extracted)
            self._refresh_derived_notes()
            if not partial:
                self._emit("incident_hypothesis", {
                    "incident_type": inc.incident_type,
                    "risk_level": inc.risk_level,
                    "location_raw": inc.location_raw,
                    "location_certain": not inc.location_needs_confirmation,
                    "third_party_risk": inc.third_party_risk,
                    "escalation_required": inc.escalation_required,
                    "source": "llm_extraction",
                    "partial": partial,
                })
                self._write_live(force=True)

    # --- internals ----------------------------------------------------------
    async def _await_turn_extraction(self, transcript: str) -> None:
        """Wait for in-flight partial extraction, then finalize branch resolution."""
        import asyncio as _asyncio

        existing = self._extraction_task
        if existing and not existing.done():
            self._pending_extraction_is_final = True
            try:
                await _asyncio.wait_for(existing, timeout=2.0)
            except TimeoutError:
                pass
            if self._last_extracted_transcript == transcript:
                return

        if self._last_extraction_result and self._last_extracted_transcript == transcript:
            self._apply_extracted_state(self._last_extraction_result, partial=False)
            self._emit_live_state(partial=False)
            return

        seq = self._extraction_seq + 1
        self._extraction_seq = seq
        self._words_at_last_extraction = len(transcript.split())
        self._pending_extraction_is_final = False
        try:
            await _asyncio.wait_for(self._run_extraction(transcript, seq, partial=False), timeout=2.0)
            self._last_extracted_transcript = transcript
        except TimeoutError:
            pass

    def _refresh_derived_notes(self) -> None:
        from chronos.note_synth import derive_notes, merge_notes

        llm_notes = list(self.state.structured_notes)
        derived = derive_notes(self.state, self._turn_index)
        merged = merge_notes(llm_notes, derived)
        if merged != self.state.structured_notes:
            self.state.structured_notes = merged
            self._write_live(force=True)

    def sanitize_spoken_response(self, text: str) -> str:
        """Strip premature handoff / dispatch promises; fall back to recommended question."""
        out = (text or "").strip()
        allow_handoff = self.state.human_handoff_ready and not self.state.human_handoff_announced
        if not allow_handoff:
            out = _PREMATURE_HANDOFF_RE.sub("", out)
            for phrase in (
                "human dispatcher",
                "human dispatch",
                "dispatcher now",
                "help is on the way",
                "units are dispatched",
                "responders are on the way",
            ):
                if phrase in out.lower():
                    idx = out.lower().find(phrase)
                    # Remove clause containing the phrase
                    start = max(0, out.rfind(".", 0, idx) + 1, out.rfind("?", 0, idx) + 1)
                    end = out.find(".", idx)
                    if end == -1:
                        end = out.find("?", idx)
                    if end == -1:
                        end = len(out)
                    else:
                        end += 1
                    out = (out[:start] + out[end:]).strip()
            out = re.sub(r"\s{2,}", " ", out).strip(" ,.;")
            out = re.sub(r"\s+(?:i am|i'm|and)\s*$", "", out, flags=re.I).strip(" ,.;")
        if len(out) < 8 and self.state.recommended_question:
            out = self.state.recommended_question
        elif self.state.incident.incident_type in _THREAT_TYPES:
            low_out = out.lower()
            if any(w in low_out for w in ("smoke", "fire", "evacuated", "away from the danger")):
                out = self.state.recommended_question or out
        return self._guardrail(out)

    def _merge_structured_notes(self, notes: list[Any], partial: bool = False) -> None:
        """Add or update structured notes extracted by the LLM."""
        if not notes:
            return
        existing = {(n.category, n.field): i for i, n in enumerate(self.state.structured_notes)}
        added: list[dict[str, Any]] = []
        for raw in notes:
            if not isinstance(raw, dict):
                continue
            cat = str(raw.get("category") or "other").strip().lower()
            field = str(raw.get("field") or "").strip().lower()
            val = str(raw.get("value") or "").strip()
            if not field or not val or val.lower() in ("null", "none", "unknown"):
                continue
            note = StructuredNote(category=cat, field=field, value=val, turn=self._turn_index)
            key = (cat, field)
            if key in existing:
                idx = existing[key]
                if self.state.structured_notes[idx].value != val:
                    self.state.structured_notes[idx] = note
                    added.append(note.to_dict())
            else:
                self.state.structured_notes.append(note)
                existing[key] = len(self.state.structured_notes) - 1
                added.append(note.to_dict())
        if added:
            self._emit("structured_notes_update", {"notes": [n.to_dict() for n in self.state.structured_notes], "added": added})
        self._refresh_derived_notes()

    def _maybe_dispatch_units(self) -> None:
        """Simulate dispatching units when minimum info is available — stay on line."""
        inc = self.state.incident
        if not inc.incident_type or inc.incident_type == "unknown":
            return
        if not inc.location_raw:
            return
        if inc.incident_type == "non_emergency_noise" and inc.risk_level in ("unknown", "low"):
            return

        self._new_dispatches_this_turn = []
        dispatched = {d.unit_type for d in self.state.dispatches}
        hazards = set(inc.hazards or [])
        itype = inc.incident_type
        loc = inc.location_raw

        candidates: list[tuple[str, str]] = []
        if itype in ("active_threat", "possible_active_disturbance"):
            candidates.append(("police", "Active threat / disturbance reported"))
        if itype == "structure_fire" or hazards & {"smoke", "visible_fire", "fire", "gas_smell"}:
            candidates.append(("fire", "Structure fire / smoke reported"))
        if itype in ("active_threat", "possible_active_disturbance") or "weapon" in hazards:
            candidates.append(("police", "Active threat or weapon reported"))
        if itype == "medical" or hazards & {"breathing", "injury"}:
            candidates.append(("ems", "Medical emergency reported"))
        if itype == "vehicle_crash":
            candidates.append(("police", "Vehicle crash reported"))
            if hazards & {"injury", "breathing", "child_in_vehicle", "child"}:
                candidates.append(("ems", "Injuries reported at crash"))
            if hazards & {"smoke", "fire_from_vehicle", "visible_fire"}:
                candidates.append(("fire", "Vehicle fire / smoke reported"))

        for unit_type, reason in candidates:
            if unit_type in dispatched:
                continue
            result = dispatch_unit(unit_type, loc, reason, inc.to_dict())
            rec = DispatchRecord(
                unit_type=unit_type,
                location=loc,
                reason=reason,
                dispatch_id=result["dispatch_id"],
                turn=self._turn_index,
            )
            self.state.dispatches.append(rec)
            self._new_dispatches_this_turn.append(result)
            self._emit("unit_dispatched", result)
            dispatched.add(unit_type)

    def _decide_escalation(self, extracted: dict[str, Any] | None = None) -> None:
        inc = self.state.incident
        pol = config.policy_for_incident(inc.incident_type)
        req_any = {str(x).lower() for x in (pol.get("escalation", {}) or {}).get("required_if_any", [])}
        active: set[str] = set(inc.hazards)
        if inc.third_party_risk == "active":
            active.add("third_party_risk_active")
        if (inc.hazards or inc.third_party_risk == "active") and inc.location_raw and inc.location_needs_confirmation:
            active.add("location_uncertain_with_danger")

        escalate = bool(active & req_any)
        reason = None
        if escalate:
            reason = "Policy: " + ", ".join(sorted(active & req_any))
        if inc.upgraded_to in ("possible_active_disturbance", "active_threat"):
            escalate, reason = True, f"Incident upgraded to {inc.upgraded_to}"
        if "weapon" in inc.hazards:
            escalate, reason = True, "Weapon or threat indicated"
        if inc.incident_type == "medical":
            escalate, reason = True, "Medical crisis — human escalation required"
        if extracted and extracted.get("escalation_required"):
            escalate = True
            reason = extracted.get("escalation_reason") or reason or "LLM: high-risk case detected"

        if escalate and config.REQUIRE_HUMAN_ESCALATION:
            inc.escalation_required = True
            inc.escalation_reason = reason
            # Human handoff event fires only when intake is complete (_process_caller_turn).

        inc.risk_level = self._risk_level_from_state()

    async def _retrieve_memory_on_start(self) -> None:
        """Prefetch CCEC general call-taking SOP at call start."""
        mr = config.load_policy("memory_retrieval_policy")
        queries = list((mr.get("triggers", {}) or {}).get("call_start", {}).get("queries", []) or [])
        if not queries:
            return
        for q in queries:
            self._emit("memory_query", {"query": q})
        self.state.memory.queries_run.extend(queries)
        results = await self.memory.search_many(queries, container_tags=[config.AGENCY_TAG], limit=3)
        added = self.state.memory.add_many(results)
        if added:
            self._emit("memory_result", {"results": [r.to_dict() for r in added]})
            self._write_live(force=True)

    def _third_party_guard(self) -> list[str]:
        pol = config.policy_for_incident(self.state.incident.incident_type) or config.load_policy("structure_fire")
        tpr = pol.get("third_party_risk", {}) or {}
        return [str(x).lower() for x in (tpr.get("cannot_be_resolved_by") or [])]

    def _risk_level_from_state(self) -> str:
        inc = self.state.incident
        pol = config.policy_for_incident(inc.incident_type)
        hazards_cfg = pol.get("hazards", {}) or {}
        level = inc.risk_level if inc.risk_level != "unknown" else (pol.get("risk_level_default") or "unknown")
        for h in inc.hazards:
            r = (hazards_cfg.get(h, {}) or {}).get("risk")
            if r and _RISK_ORDER.get(r, 0) > _RISK_ORDER.get(level, 0):
                level = r
        if inc.third_party_risk == "active" and _RISK_ORDER["critical"] > _RISK_ORDER.get(level, 0):
            level = "critical"
        if inc.upgraded_to in ("possible_active_disturbance", "active_threat"):
            level = "critical" if inc.upgraded_to == "active_threat" else max_risk(level, "high")
        return level

    async def _retrieve_memory(self) -> list:
        inc = self.state.incident
        mr = config.load_policy("memory_retrieval_policy")
        triggers = mr.get("triggers", {}) or {}
        queries: list[str] = []

        if inc.incident_type:
            inc_q = (triggers.get("incident_hypothesis_changed", {}) or {}).get(inc.incident_type, {})
            queries += list(inc_q.get("queries", []) or [])
            # Always retrieve the active incident's SOP (covers types without an
            # explicit trigger entry, e.g. medical).
            queries.append(f"{inc.incident_type.replace('_', ' ')} SOP required checks guidance")
        if inc.location_raw:
            for q in (triggers.get("location_mentioned", {}) or {}).get("queries", []) or []:
                queries.append(q.replace("{location_raw}", inc.location_raw))
        if "gas_smell" in inc.hazards:
            for q in ((triggers.get("hazard_detected", {}) or {}).get("gas_smell", {}) or {}).get("queries", []) or []:
                queries.append(q.replace("{location_raw}", inc.location_raw or ""))
        # Structure-fire policy also declares required queries.
        spol = config.policy_for_incident(inc.incident_type)
        for q in (spol.get("memory_retrieval", {}) or {}).get("required_queries", []) or []:
            queries.append(q.replace("{location_raw}", inc.location_raw or ""))

        # Dedup, keep order.
        queries = list(dict.fromkeys(q for q in queries if q.strip()))
        if not queries:
            return []
        for q in queries:
            self._emit("memory_query", {"query": q})
        self.state.memory.queries_run.extend(queries)
        results = await self.memory.search_many(queries, container_tags=[config.AGENCY_TAG], limit=5)
        added = self.state.memory.add_many(results)
        if added:
            self._emit("memory_result", {"results": [r.to_dict() for r in added]})
        return added

    def _guidance_text(self) -> str:
        """Policy-safe guidance for the call-taker (and the safe spoken fallback)."""
        inc = self.state.incident
        parts: list[str] = []
        if inc.reentry_intent:
            parts.append("Do not go back inside. Stay away from the danger and let responders handle it.")
        for d in self._new_dispatches_this_turn:
            label = d.get("unit_label") or d.get("unit_type", "units")
            parts.append(f"(Simulated: {label} notified — stay on the line.)")
        if self.state.recommended_question:
            parts.append(self.state.recommended_question)
        if inc.third_party_risk == "active" and self.state.recommended_slot != "trapped_person_status":
            parts.append("Keep the trapped-person question open until it's confirmed no one is inside.")
        if self.state.human_handoff_ready and not self.state.human_handoff_announced:
            parts.append("I'm bringing in a human dispatcher now — stay on the line with me until they join.")
        text = " ".join(parts) or "Continue gathering location and safety details."
        return self._guardrail(text)

    def _guardrail(self, text: str) -> str:
        low = text.lower()
        for pat in _FORBIDDEN_PATTERNS:
            if pat in low and pat not in ("go back inside", "go back in"):  # our own "do not go back inside" is safe
                if "do not " + pat not in low and "don't " + pat not in low:
                    self.state.forbidden_guidance_emitted = True
        return text

    # --- LLM context (voice path) ------------------------------------------
    def build_llm_context(self) -> dict[str, Any]:
        inc = self.state.incident
        plan = self.state.sop_plan or {}
        return {
            "incident_state": inc.to_dict(),
            "sop_plan": plan,
            "sop_checklist": self.state.checklist_dicts(),
            "missing_slots": inc.missing_slots,
            "recommended_question": self.state.recommended_question,
            "memory_results": [r.to_dict() for r in self.state.memory.results[:5]],
            "recent_transcript": self.state.turns[-4:],
            "escalation_required": inc.escalation_required,
            "escalation_reason": inc.escalation_reason,
            "intake_complete": self.state.intake_complete,
            "human_handoff_ready": self.state.human_handoff_ready,
            "human_handoff_announced": self.state.human_handoff_announced,
            "dispatches": [d.to_dict() for d in self.state.dispatches],
            "new_dispatches": self._new_dispatches_this_turn,
            "structured_notes": [n.to_dict() for n in self.state.structured_notes],
            "missing_slot_labels": self.state._missing_slot_labels_for_snapshot(),
            "forbidden_guidance": (config.policy_for_incident(inc.incident_type).get("forbidden_guidance") or []),
        }

    def on_agent_response(self, text: str) -> None:
        sanitized = self.sanitize_spoken_response(text)
        low = sanitized.lower()
        if self.state.human_handoff_ready and ("human dispatcher" in low or "human dispatch" in low):
            self.state.human_handoff_announced = True
        self.state.guidance_history.append({"turn": self._turn_index, "agent": sanitized})
        if "go back inside" in sanitized.lower() and "do not go back inside" not in sanitized.lower():
            self.state.instructed_reentry = True
        self._emit("agent_guidance", {"text": sanitized})

    async def on_call_complete(self) -> None:
        snap = self.state.snapshot()
        self._emit("call_complete", {"snapshot_incident": snap["incident"], "flags": snap["flags"]})
        try:
            self.memory.write_call_summary(snap, [])
        except Exception:
            pass
        if self.events is not None:
            self.events.persist(self.state.call_id)
            _persist_latest(snap)


def max_risk(a: str, b: str) -> str:
    return a if _RISK_ORDER.get(a, 0) >= _RISK_ORDER.get(b, 0) else b


def _persist_latest(snapshot: dict[str, Any]) -> None:
    import json

    try:
        with open(config.RUNTIME_DIR / "latest.json", "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception:
        pass
