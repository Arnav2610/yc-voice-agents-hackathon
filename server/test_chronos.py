"""Chronos invariants: the deterministic regression + self-improvement loop.

Runs fully offline (force-local memory). Verifies the baseline fails the
WRONG_BRANCH_CLOSURE scenarios and that the generated policy patch fixes them
with no regression. Reverts the policy file around every test.
"""

import asyncio

import pytest

from chronos.improvement_loop import (
    revert_policies_to_baseline,
    run_improvement,
    run_suite,
)


@pytest.fixture(autouse=True)
def _baseline():
    revert_policies_to_baseline()
    yield
    revert_policies_to_baseline()


def test_baseline_has_wrong_branch_closures():
    suite = asyncio.run(run_suite())
    s = suite.summary
    assert s["total"] == 12
    # Baseline must fail the structure-fire third-party scenarios.
    assert s["wrong_branch_closure"] == 4
    assert s["missed_trapped_person_question"] == 3
    assert s["pass_rate"] < 0.8
    failed = {r.id for r in suite.results if not r.passed}
    assert "structure_fire_neighbor_inside_001" in failed
    assert "structure_fire_prior_gas_001" in failed


def test_every_scenario_retrieves_relevant_memory():
    suite = asyncio.run(run_suite())
    # Every scenario should surface at least one institutional memory locally.
    for r in suite.results:
        assert r.snapshot["memory"]["results"], f"{r.id} retrieved no memory"


def test_improvement_loop_fixes_branch_closure():
    rep = asyncio.run(run_improvement())
    assert rep["status"] == "accepted"
    assert rep["before"]["pass_rate"] < rep["after"]["pass_rate"]
    assert rep["after"]["pass_rate"] == 1.0
    assert rep["after"]["wrong_branch_closure"] == 0
    assert rep["after"]["missed_trapped_person_question"] == 0
    assert "cannot_be_resolved_by" in rep["policy_diff"]
    assert rep["failure"]["failure_type"] == "WRONG_BRANCH_CLOSURE"


def test_patched_policy_keeps_third_party_active_after_evacuation():
    asyncio.run(run_improvement())  # applies the patch
    suite = asyncio.run(run_suite())
    by_id = {r.id: r for r in suite.results}
    inc = by_id["structure_fire_neighbor_inside_001"].snapshot["incident"]
    assert inc["third_party_risk"] == "active"
    assert by_id["structure_fire_neighbor_inside_001"].passed


def test_accidentally_does_not_classify_as_vehicle_crash():
    from chronos.llm_mock import mock_extract_call_state

    out = mock_extract_call_state(
        "i accidentally bent my tongue and now i'm struggling to breathe",
        partial=True,
    )
    assert out["incident_type"] == "medical"
    assert out["incident_type"] != "vehicle_crash"


def test_hackathon_medical_fire_scenario():
    """Spicy food / breathing distress + office fire; must NOT become vehicle crash."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    turns = [
        "Hello.",
        "I'm at the Y combinator office. They served lunch. It was super spicy and I accidentally bent my tongue and now I'm struggling to breathe.",
        "Yes, I can't breathe. It's hard to take breaths. Oh my god a fire started as well what should I do there's a fire",
        "um I'm with the other hackathon people and Gary Tan is here as well why does it keep going to vehicle crash",
    ]

    async def run():
        store = EventStore()
        k = ChronosKernel(
            "hackathon_med_fire",
            memory_client=ChronosMemoryClient(force_local=True),
            event_store=store,
            use_llm_extraction=False,
        )
        partial = ""
        for w in turns[1].split():
            partial = (partial + " " + w).strip()
            await k.observe_partial(partial)
        assert k.state.incident.incident_type == "medical"
        loc = (k.state.incident.location_raw or "").lower()
        assert "combinator" in loc

        for turn in turns:
            await k.process_caller_turn(turn)

        return k, store

    k, store = asyncio.run(run())
    inc = k.state.incident
    assert inc.incident_type == "structure_fire", f"expected structure_fire, got {inc.incident_type}"
    assert inc.incident_type != "vehicle_crash"
    assert inc.escalation_required is True
    assert any(e["event_type"] == "sop_checklist_update" for e in store.list(k.state.call_id))


def test_partials_update_state_without_a_final_turn():
    """Real-time path: streaming partials must drive detection BEFORE the turn
    finalizes (the 'act while you talk' behavior)."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    async def run():
        store = EventStore()
        k = ChronosKernel("partialtest", memory_client=ChronosMemoryClient(force_local=True), event_store=store, use_llm_extraction=False)
        partial = ""
        for w in "there is smoke in my building my neighbor is still inside".split():
            partial = (partial + " " + w).strip()
            await k.observe_partial(partial)
        return k

    k = asyncio.run(run())
    inc = k.state.incident
    assert inc.incident_type == "structure_fire"
    assert inc.third_party_risk == "active"      # activated mid-utterance
    assert inc.escalation_required is True
    assert k.state.turns == []                    # NO final turn committed yet


def test_realtime_partial_knife_break_in_before_turn_end():
    """Streaming partials must classify, note, and dispatch before the caller finishes."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    utterance = (
        "Hello, I'm at fourteen twelve Market Street and someone's banging on my door "
        "and trying to break in. They have a knife"
    )

    async def run():
        store = EventStore()
        k = ChronosKernel(
            "realtime_knife",
            memory_client=ChronosMemoryClient(force_local=True),
            event_store=store,
            use_llm_extraction=False,
        )
        partial = ""
        classified_at: int | None = None
        weapon_at: int | None = None
        for i, w in enumerate(utterance.split(), start=1):
            partial = (partial + " " + w).strip()
            await k.observe_partial(partial)
            if classified_at is None and k.state.incident.incident_type == "active_threat":
                classified_at = i
            if weapon_at is None and "weapon" in k.state.incident.hazards:
                weapon_at = i
        return k, store, classified_at, weapon_at, len(utterance.split())

    k, store, classified_at, weapon_at, total_words = asyncio.run(run())
    inc = k.state.incident
    assert k.state.turns == []
    assert inc.incident_type == "active_threat", f"got {inc.incident_type}"
    assert classified_at is not None and classified_at < total_words, "must classify before turn end"
    assert weapon_at is not None and weapon_at <= total_words
    assert inc.location_raw and "market" in inc.location_raw.lower()
    assert "weapon" in inc.hazards
    assert inc.escalation_required, "active threat with location should escalate"
    assert k.state.dispatches, "policy dispatch when escalation + location known"
    assert any(d.unit_type == "police" for d in k.state.dispatches)
    note_fields = {(n.category, n.field) for n in k.state.structured_notes}
    assert ("threat", "weapon_type") in note_fields or ("threat", "weapon") in note_fields
    assert any(e["event_type"] == "sop_checklist_update" for e in store.list(k.state.call_id))


def test_spicy_tongue_bleeding_is_medical_not_active_threat():
    """Food-related tongue injury must not classify as active threat or ask lock-the-door questions."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    utterance = (
        "Hello. I'm at the Y Combinator Office and we just had lunch and it was very spicy "
        "so my uh tongue uh got bit and I'm bleeding very aggressively now"
    )

    async def run():
        k = ChronosKernel(
            "spicy_tongue",
            memory_client=ChronosMemoryClient(force_local=True),
            event_store=EventStore(),
            use_llm_extraction=False,
        )
        partial = ""
        for w in utterance.split():
            partial = (partial + " " + w).strip()
            await k.observe_partial(partial)
        await k.process_caller_turn(utterance)
        return k

    k = asyncio.run(run())
    inc = k.state.incident
    assert inc.incident_type == "medical", f"expected medical, got {inc.incident_type}"
    assert inc.incident_type not in ("active_threat", "possible_active_disturbance")
    rec = (k.state.recommended_question or "").lower()
    assert "lock" not in rec
    assert "room with a lock" not in rec


def test_mistaken_active_threat_reclassifies_to_medical():
    """If the LLM prematurely labels a tongue injury as active_threat, kernel must correct it."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.llm_extractor import set_extract_override
    from chronos.memory_retrieval import ChronosMemoryClient

    utterance = (
        "Y Combinator office, lunch was very spicy, my tongue got bit and I'm bleeding"
    )

    def fake_extract(_transcript: str, partial: bool) -> dict:
        return {
            "incident_type": "active_threat",
            "incident_confidence": 0.92,
            "location_raw": "Y Combinator office",
            "location_certain": True,
            "caller_safety": "unknown",
            "third_party_at_risk": False,
            "hazards": ["injury"],
            "risk_level": "critical",
            "escalation_required": True,
            "resolved_slots": ["exact_location"],
            "structured_notes": [],
        }

    async def run():
        set_extract_override(fake_extract)
        try:
            k = ChronosKernel(
                "reclass_medical",
                memory_client=ChronosMemoryClient(force_local=True),
                event_store=EventStore(),
                use_llm_extraction=True,
            )
            await k.process_caller_turn(utterance)
            return k
        finally:
            set_extract_override(None)

    k = asyncio.run(run())
    assert k.state.incident.incident_type == "medical"


def test_pending_slot_answer_marks_resolved_and_advances():
    """Answering the recommended question must resolve that slot and avoid repeats."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    async def run():
        k = ChronosKernel(
            "slot_answer",
            memory_client=ChronosMemoryClient(force_local=True),
            event_store=EventStore(),
            use_llm_extraction=False,
        )
        await k.process_caller_turn("Please help, I'm bleeding from my mouth at Y Combinator office")
        k.state.recommended_slot = "injury_status"
        k.state.recommended_question = "Is the bleeding slowing down or still flowing heavily?"
        k._awaiting_slot_answer = "injury_status"
        await k.process_caller_turn("it's becoming worse and worse")
        return k

    k = asyncio.run(run())
    assert "injury_status" in k.sop._llm_resolved
    resolved = set(k.sop._llm_resolved) | set(k.state.incident.resolved_slots or [])
    for key in k.state.slot_display_values:
        assert key in resolved, f"display value for unresolved slot {key}"


def test_unresolved_slots_pruned_from_display_values():
    from chronos.slot_display import prune_slot_display_values
    from chronos.state import CallState

    state = CallState(call_id="prune_test")
    state.slot_display_values = {"injury_status": "Bleeding from mouth", "breathing": "Normal"}
    assert prune_slot_display_values(state, {"injury_status"})
    assert state.slot_display_values == {"injury_status": "Bleeding from mouth"}


def test_robbery_classified_active_threat_not_structure_fire():
    """Home invasion / robbery must not flip to structure fire or ask smoke questions."""
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    turns = [
        "1412 Market Street apartment Accelerate Hacker Hotel Room 107 someone banging saying they will rob me",
        "No just me I'm safe for now but they might break my door soon",
        "My name is RNF Kumar and my number is 111111222222222222222",
    ]

    async def run():
        k = ChronosKernel(
            "robbery_test",
            memory_client=ChronosMemoryClient(force_local=True),
            event_store=EventStore(),
            use_llm_extraction=False,
        )
        for turn in turns:
            await k.process_caller_turn(turn)
        return k

    k = asyncio.run(run())
    inc = k.state.incident
    assert inc.incident_type == "active_threat", f"got {inc.incident_type}"
    assert inc.incident_type != "structure_fire"
    assert "exact_location" in inc.resolved_slots
    assert "caller_safety" in inc.resolved_slots
    assert "callback_number" in inc.resolved_slots
    rec = (k.state.recommended_question or "").lower()
    assert "smoke" not in rec
    handoff = k.sanitize_spoken_response(
        "Are you outside away from smoke? I'm also bringing in a human dispatcher."
    )
    assert "human dispatcher" not in handoff.lower()
    assert "smoke" not in handoff.lower()


def test_live_json_mirrors_state_for_cross_process_dashboard():
    """A dashboard in a different process (no in-process kernel) must still show
    the live call by reading runtime/live.json. Regression for the port-conflict
    bug where a stale dashboard served an empty view."""
    import json

    from fastapi.testclient import TestClient

    from chronos import config, dashboard_server
    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.memory_retrieval import ChronosMemoryClient

    lj = config.RUNTIME_DIR / "live.json"
    if lj.exists():
        lj.unlink()

    async def run():
        store = EventStore()
        k = ChronosKernel("xproc", memory_client=ChronosMemoryClient(force_local=True), event_store=store, use_llm_extraction=False)
        partial = ""
        for w in "there is smoke in my building my neighbor is still inside".split():
            partial = (partial + " " + w).strip()
            k._emit("partial_transcript", {"text": partial})
            await k.observe_partial(partial)
        k._write_live(force=True)

    asyncio.run(run())
    assert lj.exists(), "live.json must be written for cross-process viewing"

    dashboard_server.LIVE["kernel"] = None  # simulate a separate dashboard process
    client = TestClient(dashboard_server.app)
    d = client.get("/chronos/latest").json()
    inc = d["snapshot"]["incident"]
    assert inc["incident_type"] == "structure_fire"
    assert inc["third_party_risk"] == "active"
    assert any(e["event_type"] == "partial_transcript" for e in d["events"])
    assert d["snapshot"]["turns"] == []
    lj.unlink()
