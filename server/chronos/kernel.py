"""Chronos interaction kernel — the per-call orchestrator.

Wires together the incident tracker, safety sentinel, SOP engine, floor
controller, memory retrieval, and mock tools. All escalation / branch-closure
decisions go through POLICY here (not the LLM), so a policy patch changes
behavior deterministically.

The kernel is async and is used identically by the live Pipecat voice path and
by the offline regression simulator (which passes a force-local memory client).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from chronos import config
from chronos.events import EventStore
from chronos.floor_controller import FloorController
from chronos.incident_tracker import IncidentTracker
from chronos.memory_retrieval import ChronosMemoryClient
from chronos.mocks import escalate_to_human, resolve_location
from chronos.safety_sentinel import SafetySentinel
from chronos.sop_engine import SOPEngine
from chronos.state import CallState

_RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_NOMINAL_MS_PER_TURN = 1200  # illustrative latency used for time-to-critical metric

_FORBIDDEN_PATTERNS = [
    "go back inside", "go back in", "re-enter", "reenter", "head back in",
    "the scene is safe", "building is safe", "it is safe to go", "open the hood",
    "dispatched", "help is on the way", "ambulance will arrive",
]


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
        # LLM extraction: enabled on the live voice path; disabled in the
        # offline regression simulator (deterministic, no Nemotron calls).
        self.use_llm_extraction = use_llm_extraction
        self.tracker = IncidentTracker()
        self.sentinel = SafetySentinel()
        self.sop = SOPEngine()
        self.floor = FloorController()
        self._turn_index = 0
        # Real-time (partial-transcript) processing guards + debounce state.
        self._processing_final = False
        self._partial_busy = False
        self._last_partial_sig: tuple | None = None
        self._last_prefetch_sig: tuple | None = None
        self._last_live_write_ms = 0
        # LLM extraction debounce: fires every ~6 new words while caller is speaking.
        self._extraction_seq = 0              # monotonic; discard stale results
        self._extraction_task: Any = None     # current in-flight asyncio.Task
        self._words_at_last_extraction = 0    # word count when last extraction was triggered
        self._pending_extraction_is_final = False  # upgrade reused task to non-partial on final turn
        # Tell the SOP engine whether to apply the slot-skip heuristic (live only).
        self.state._use_slot_skip = use_llm_extraction  # type: ignore[attr-defined]
        self._emit("call_start", {"disclaimer": config.SIMULATION_DISCLAIMER})

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
        self.state.turns.append(text)
        self.state.partial_buffer.clear()
        self._emit("final_transcript", {"text": text, "turn": self._turn_index})

        inc = self.state.incident

        # 1) LLM extraction — FIRE-AND-FORGET on the final turn.
        #
        # We do NOT await extraction before pushing the frame to the voice LLM.
        # The voice LLM has the full transcript and responds correctly regardless.
        # Awaiting here added 1-3s latency and silently failed under Nemotron load,
        # leaving the dashboard empty. Instead: let the existing in-flight partial
        # extraction complete (or start a fresh one if none running), and let it
        # update the dashboard asynchronously via _apply_extracted_state.
        if self.use_llm_extraction:
            full_transcript = self.state.cumulative_text
            existing = self._extraction_task
            if existing and not existing.done():
                # In-flight partial task: DON'T change _extraction_seq.
                # The task captured the old seq value; if we increment here its
                # result would be seq-mismatched and silently discarded — which is
                # exactly what was causing "nothing updated" in production.
                # Mark it so it applies as a final-turn (non-partial) result.
                self._pending_extraction_is_final = True
            else:
                # Nothing running; launch a fresh final-turn extraction.
                seq = self._extraction_seq + 1
                self._extraction_seq = seq
                self._words_at_last_extraction = len(full_transcript.split())
                self._pending_extraction_is_final = False
                self._extraction_task = _asyncio.create_task(
                    self._run_extraction(full_transcript, seq, partial=False)
                )

        # 2) Deterministic safety sentinel — always runs as a safety backstop.
        #    It catches things the LLM might miss (e.g. explicit weapon/reentry).
        #    When LLM extraction is off (simulator), it's also the primary classifier.
        upd = self.tracker.update(self.state.cumulative_text, inc.incident_type)
        if not inc.incident_type and upd.incident_type:
            inc.incident_type = upd.incident_type
        if upd.upgraded_to and not inc.upgraded_to:
            inc.upgraded_to = upd.upgraded_to
        if not self.use_llm_extraction and upd.location_raw:
            inc.location_raw = upd.location_raw
            inc.location_needs_confirmation = upd.location_uncertain or _approximate(self.state.cumulative_text)
            inc.location_confidence = 0.5 if upd.location_uncertain else 0.74

        sig = self.sentinel.detect(text, self.state.cumulative_text)
        self._apply_safety(sig)
        self._emit("safety_signal", {
            "hazards": inc.hazards,
            "third_party_risk": inc.third_party_risk,
            "caller_safety": inc.caller_safety,
            "risk_level": inc.risk_level,
            "signals": sig.escalation_signals,
        })

        # 3) SOP checklist.
        prev_recommended = self.state.recommended_slot
        self.sop.update_checklist(self.state)
        slot = self.state.recommended_slot
        if slot:
            self.state.asked_slots.add(slot)
            # Increment the ask-count so the SOP engine can skip a stuck slot.
            # Reset counts for any slot that just got resolved.
            self.state.asked_slot_counts[slot] = self.state.asked_slot_counts.get(slot, 0) + 1
        for s in inc.resolved_slots:
            if s in self.state.asked_slot_counts:
                del self.state.asked_slot_counts[s]
        self._emit(
            "sop_checklist_update",
            {
                "checklist": self.state.checklist_dicts(),
                "missing_slots": inc.missing_slots,
                "recommended_slot": self.state.recommended_slot,
                "recommended_question": self.state.recommended_question,
            },
        )

        # 4) Speculative location resolution (reversible read) + memory retrieval.
        if inc.location_raw:
            loc = resolve_location(inc.location_raw)
            self._emit("tool_prefetch", {"tool": "resolve_location", "result": loc})

        # Skip the memory re-fetch if partials already populated enough results
        # (they fire-and-forget speculatively; by the time the turn finalises the
        # cache is usually warm). This keeps the final-turn response fast.
        if len(self.state.memory.results) >= 3:
            memory_added = []  # already have context; nothing new to emit
        else:
            # Fetch with a shorter timeout so a cold start doesn't stall the LLM.
            import asyncio as _asyncio
            try:
                memory_added = await _asyncio.wait_for(self._retrieve_memory(upd), timeout=2.0)
            except TimeoutError:
                memory_added = []

        # 5) Escalation (policy-driven).
        self._decide_escalation(sig)

        # 6) Floor action.
        action = self.floor.decide(self.state, sig, upd, slow_memory=bool(self.slow_memory_ms))
        self.state.floor_actions.append(action.to_dict())
        self._emit("floor_action", action.to_dict())

        # 7) Timing for first critical guidance.
        if inc.escalation_required and self.state.first_critical_guidance_ms is None:
            self.state.first_critical_guidance_ms = self._turn_index * _NOMINAL_MS_PER_TURN

        # 8) Deterministic guidance text (safe fallback + what the simulator scores).
        guidance = self._guidance_text(sig)
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
        """A third party (not the caller) is audible. Adopt only safety-critical
        facts; ignore irrelevant chatter."""
        text = (text or "").strip()
        sig = self.sentinel.detect(text, self.state.cumulative_text + " " + text)
        safety_critical = bool(sig.hazards) or sig.third_party_detected or sig.weapon_or_threat
        self._emit("background_speech", {"text": text, "safety_critical": safety_critical})
        if not safety_critical:
            self.state.ignored_background = True
            return
        self.state.background_safety_handled = True
        # Apply only third-party + hazards (never caller evacuation/closure).
        for h in sig.hazards:
            self.state.incident.add_hazard(h)
        if sig.third_party_detected:
            self.state.incident.third_party_risk = "active"
        self.sop.update_checklist(self.state)
        if self.state.recommended_slot:
            self.state.asked_slots.add(self.state.recommended_slot)
        self._decide_escalation(sig)
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
        """Real-time mid-utterance processing.

        Two layers run in parallel:

        Layer 1 — INSTANT (this method, synchronous): cheap keyword detection
        fires on every partial to give the live UI immediate visual feedback —
        incident type, hazards, third-party activation.  ACTIVATION-ONLY: never
        resolves/closes a safety branch mid-sentence.

        Layer 2 — BACKGROUND LLM EXTRACTION (debounced): every ~6 new words we
        fire a focused Nemotron extraction call as a background asyncio.Task.
        When it lands it calls _apply_extracted_state(), which accurately updates
        location, caller safety, third-party description, etc.  A monotonic seq
        number discards stale results if multiple extractions overlap.
        """
        if self._processing_final or self._partial_busy:
            return
        partial_text = (partial_text or "").strip()
        if len(partial_text) < 3:
            return
        self._partial_busy = True
        try:
            import asyncio as _asyncio

            provisional = (self.state.cumulative_text + " " + partial_text).strip()
            inc = self.state.incident

            # --- Layer 1: instant keyword detection ---
            upd = self.tracker.update(provisional, inc.incident_type)
            if upd.incident_type:
                inc.incident_type = upd.incident_type
                inc.incident_confidence = upd.confidence
            if upd.upgraded_to:
                inc.upgraded_to = upd.upgraded_to
            # When LLM extraction is off (simulator), apply location from tracker.
            if not self.use_llm_extraction and upd.location_raw:
                inc.location_raw = upd.location_raw
                inc.location_needs_confirmation = upd.location_uncertain or _approximate(provisional)
                inc.location_confidence = 0.5 if upd.location_uncertain else 0.74

            sig = self.sentinel.detect(partial_text, provisional)
            for h in sig.hazards:
                inc.add_hazard(h)
            if sig.third_party_detected:
                inc.third_party_risk = "active"
            inc.risk_level = self._risk_level(sig)
            self.sop.update_checklist(self.state)
            self._decide_escalation(sig)

            state_sig = (
                inc.incident_type, inc.upgraded_to, tuple(inc.hazards),
                inc.third_party_risk, inc.risk_level, inc.location_raw,
                inc.escalation_required, self.state.recommended_slot,
            )
            if state_sig != self._last_partial_sig:
                self._last_partial_sig = state_sig
                self._emit("incident_hypothesis", {
                    "incident_type": inc.incident_type,
                    "risk_level": inc.risk_level,
                    "third_party_risk": inc.third_party_risk,
                    "recommended_slot": self.state.recommended_slot,
                    "escalation_required": inc.escalation_required,
                    "partial": True,
                })
            self._write_live()

            # --- Layer 2: debounced background LLM extraction (live path only) ---
            if self.use_llm_extraction:
                word_count = len(provisional.split())
                new_words = word_count - self._words_at_last_extraction
                if new_words >= 6:
                    self._words_at_last_extraction = word_count
                    seq = self._extraction_seq + 1
                    self._extraction_seq = seq
                    self._pending_extraction_is_final = False  # new partial task resets the flag
                    if self._extraction_task and not self._extraction_task.done():
                        self._extraction_task.cancel()
                    self._extraction_task = _asyncio.create_task(
                        self._run_extraction(provisional, seq)
                    )

            # Memory prefetch when incident type or key hazards change.
            prefetch_sig = (inc.incident_type, tuple(sorted(inc.hazards)))
            if inc.incident_type and prefetch_sig != self._last_prefetch_sig:
                self._last_prefetch_sig = prefetch_sig
                _asyncio.create_task(self._retrieve_memory(upd))

        except Exception:  # noqa: BLE001 — never let a partial break the call
            pass
        finally:
            self._partial_busy = False

    async def _run_extraction(self, transcript: str, seq: int, partial: bool = True) -> None:
        """Background LLM extraction task — updates state when it lands.
        partial=True for mid-ramble (activation only); False for final turn (full apply).
        If _pending_extraction_is_final was set while this task was in-flight, it is
        applied as non-partial so caller-safety and branch resolution are committed."""
        from chronos.llm_guidance import extract_state_llm
        result = await extract_state_llm(transcript)
        if result and seq == self._extraction_seq:
            apply_partial = partial and not self._pending_extraction_is_final
            self._pending_extraction_is_final = False
            self._apply_extracted_state(result, partial=apply_partial)

    def _apply_extracted_state(self, extracted: dict[str, Any], partial: bool = False) -> None:
        """Apply LLM-extracted structured state to the incident.

        On partials: ACTIVATION-ONLY (add hazards, set location, activate
        third-party) — never resolve/close a branch from a partial utterance.
        On final turn: full apply including caller safety and branch resolution.
        """
        inc = self.state.incident
        changed = False

        # Incident type (LLM wins over keyword classifier).
        t = extracted.get("incident_type")
        if t and t != "unknown" and t != inc.incident_type:
            inc.incident_type = t
            inc.incident_confidence = 0.9
            changed = True

        upgraded = extracted.get("incident_upgraded_to")
        if upgraded and upgraded != "null" and upgraded != inc.upgraded_to:
            inc.upgraded_to = upgraded
            inc.incident_type = upgraded
            changed = True

        # Location — LLM gives us a natural-language string from the caller's
        # own words.  Update on both partials and finals.
        loc = extracted.get("location_raw")
        if loc and loc != "null":
            certain = bool(extracted.get("location_certain"))
            if loc != inc.location_raw:
                inc.location_raw = loc
                changed = True
            inc.location_needs_confirmation = not certain
            inc.location_confidence = 0.9 if certain else 0.6

        # Hazards.
        for h in (extracted.get("hazards") or []):
            if inc.add_hazard(h):
                changed = True

        # Third-party risk — activate on partial; resolve only on final turn.
        if extracted.get("third_party_at_risk"):
            if inc.third_party_risk != "active":
                inc.third_party_risk = "active"
                changed = True

        if not partial:
            # Caller safety — only commit on a final turn (not a half-heard partial).
            cs = extracted.get("caller_safety")
            if cs and cs != "unknown":
                mapping = {"evacuated": "self_evacuated", "safe": "resolved", "at_risk": "at_risk"}
                inc.caller_safety = mapping.get(cs, inc.caller_safety)
                changed = True

            # Third-party resolution — only if nobody at risk per LLM AND no
            # prior active signals.
            if not extracted.get("third_party_at_risk") and inc.third_party_risk == "unknown":
                pass  # still unknown; leave it

        if changed:
            inc.risk_level = max_risk(inc.risk_level, "high" if inc.third_party_risk == "active" else inc.risk_level)
            self.sop.update_checklist(self.state)
            self._decide_escalation_from_extracted(extracted)
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

    def _decide_escalation_from_extracted(self, extracted: dict[str, Any]) -> None:
        if extracted.get("escalation_required") and not self.state.incident.escalation_required:
            from chronos.mocks import escalate_to_human
            reason = "LLM extraction: high-risk case detected"
            handoff = escalate_to_human(reason)
            self.state.incident.escalation_required = True
            self.state.incident.escalation_reason = reason
            self._emit("escalation_recommended", {"reason": reason, "handoff": handoff})

    # --- internals ----------------------------------------------------------
    def _apply_safety(self, sig) -> None:
        inc = self.state.incident
        for h in sig.hazards:
            inc.add_hazard(h)

        # Caller's own safety.
        if sig.caller_evacuated:
            inc.caller_safety = "self_evacuated"
        elif sig.caller_safe_statement and inc.caller_safety == "unknown":
            inc.caller_safety = "resolved"

        # Third-party (someone-else-inside) risk. A fresh detection re-activates.
        if sig.third_party_detected:
            inc.third_party_risk = "active"

        # Resolution of the third-party branch.
        if sig.everyone_out:
            inc.third_party_risk = "resolved"
        elif (sig.caller_evacuated or sig.caller_safe_statement) and inc.third_party_risk == "active":
            guard = self._third_party_guard()
            blocked = "caller_personally_evacuated" in guard or (
                sig.caller_safe_statement and "caller_says_i_am_safe" in guard
            )
            if not blocked:
                # BASELINE BUG: caller evacuation closes the whole branch.
                inc.third_party_risk = "resolved"

        if sig.reentry_intent:
            # The caller wants to do something unsafe; we never instruct reentry.
            self._emit("policy_violation_warning", {"caller_unsafe_intent": "wants_to_reenter"})

        # Risk level = max over detected hazards (per policy) + branch state.
        inc.risk_level = self._risk_level(sig)

    def _third_party_guard(self) -> list[str]:
        pol = config.policy_for_incident(self.state.incident.incident_type) or config.load_policy("structure_fire")
        tpr = pol.get("third_party_risk", {}) or {}
        return [str(x).lower() for x in (tpr.get("cannot_be_resolved_by") or [])]

    def _risk_level(self, sig) -> str:
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

    def _decide_escalation(self, sig) -> None:
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
        if sig.weapon_or_threat:
            escalate, reason = True, "Weapon or threat indicated"
        if inc.incident_type == "medical":
            escalate, reason = True, "Medical crisis — human escalation required"

        if escalate and config.REQUIRE_HUMAN_ESCALATION:
            if not inc.escalation_required:
                handoff = escalate_to_human(reason or "high-risk case")
                self._emit("escalation_recommended", {"reason": reason, "handoff": handoff})
            inc.escalation_required = True
            inc.escalation_reason = reason

    async def _retrieve_memory(self, upd) -> list:
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

    def _guidance_text(self, sig) -> str:
        """Deterministic, policy-safe guidance for the call-taker (and the safe
        spoken fallback). Never contains forbidden guidance."""
        inc = self.state.incident
        parts: list[str] = []
        if sig.reentry_intent:
            parts.append("Do not go back inside. Stay away from the danger and let responders handle it.")
        if self.state.recommended_question:
            parts.append(self.state.recommended_question)
        if inc.third_party_risk == "active" and self.state.recommended_slot != "trapped_person_status":
            parts.append("Keep the trapped-person question open until it's confirmed no one is inside.")
        if inc.escalation_required:
            parts.append("Recommend immediate human-dispatcher escalation.")
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
        return {
            "incident_state": inc.to_dict(),
            "sop_checklist": self.state.checklist_dicts(),
            "missing_slots": inc.missing_slots,
            "recommended_question": self.state.recommended_question,
            "memory_results": [r.to_dict() for r in self.state.memory.results[:5]],
            "recent_transcript": self.state.turns[-4:],
            "escalation_required": inc.escalation_required,
            "escalation_reason": inc.escalation_reason,
            "forbidden_guidance": (config.policy_for_incident(inc.incident_type).get("forbidden_guidance") or []),
        }

    def on_agent_response(self, text: str) -> None:
        sanitized = self._guardrail(text)
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


def _approximate(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ("near ", "around ", "somewhere", "i think", "not sure", "maybe", " or "))


def _persist_latest(snapshot: dict[str, Any]) -> None:
    import json

    try:
        with open(config.RUNTIME_DIR / "latest.json", "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception:
        pass
