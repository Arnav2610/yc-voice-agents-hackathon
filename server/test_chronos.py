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
