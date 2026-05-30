"""Self-improvement loop: scenario runner, assertion checker, failure
classifier, safe policy-patch generator, regression runner, and accept/reject.

The regression runner replays scenario scripts against the LIVE policy files
using the deterministic kernel, so the before/after improvement is REAL (it
comes from the actual policy change), not hardcoded.
"""

from __future__ import annotations

import asyncio
import difflib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from chronos import config
from chronos.kernel import ChronosKernel
from chronos.memory_retrieval import ChronosMemoryClient

SCENARIOS_PATH = config.DATA_DIR / "cekura_scenarios.yaml"


# --------------------------------------------------------------------------- #
# Scenario loading + running
# --------------------------------------------------------------------------- #
def load_scenarios() -> list[dict[str, Any]]:
    data = yaml.safe_load(SCENARIOS_PATH.read_text()) or {}
    return data.get("scenarios", [])


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    for s in load_scenarios():
        if s.get("id") == scenario_id:
            return s
    return None


@dataclass
class AssertionResult:
    check: str
    expected: Any
    actual: Any
    ok: bool


@dataclass
class ScenarioResult:
    id: str
    title: str
    incident_family: str
    passed: bool
    assertions: list[AssertionResult] = field(default_factory=list)
    failure_type: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _check_assertion(check: dict[str, Any], state) -> AssertionResult:
    (key, expected), = check.items()
    inc = state.incident
    actual: Any = None
    ok = False
    if key == "incident_type":
        actual = inc.incident_type
        ok = actual == expected
    elif key == "escalation_recommended":
        actual = inc.escalation_required
        ok = actual == expected
    elif key == "asked_slot":
        actual = state.slot_was_asked(expected)
        ok = actual is True
    elif key == "third_party_risk_active_at_end":
        actual = inc.third_party_risk == "active"
        ok = actual == expected
    elif key == "location_uncertain":
        actual = bool(inc.location_raw) and inc.location_needs_confirmation
        ok = actual == expected
    elif key == "retrieved_memory_substr":
        actual = state.memory.contains_substr(expected)
        ok = actual is True
    elif key == "upgraded_incident":
        actual = inc.upgraded_to or inc.incident_type
        ok = actual == expected
    elif key == "suppressed_interruption_during_correction":
        actual = state.suppressed_interruption
        ok = actual == expected
    elif key == "backchannel_emitted":
        actual = state.backchannel_emitted
        ok = actual == expected
    elif key == "handled_background_safety_fact":
        actual = state.background_safety_handled
        ok = actual == expected
    elif key == "ignored_irrelevant_background":
        actual = state.ignored_background
        ok = actual == expected
    elif key == "no_forbidden_guidance":
        actual = not state.forbidden_guidance_emitted
        ok = actual == expected
    elif key == "did_not_instruct_reentry":
        actual = not state.instructed_reentry
        ok = actual == expected
    else:
        actual = f"<unknown check {key}>"
        ok = False
    return AssertionResult(check=key + (f"={expected}" if not isinstance(expected, bool) else ""), expected=expected, actual=actual, ok=ok)


async def run_scenario(
    scenario: dict[str, Any], memory_client: ChronosMemoryClient | None = None, event_store=None
) -> ScenarioResult:
    mem = memory_client or ChronosMemoryClient(force_local=True)
    slow = int(scenario.get("simulate_slow_memory_ms", 0) or 0)
    kernel = ChronosKernel(
        call_id=f"sim_{scenario['id']}",
        scenario_id=scenario["id"],
        memory_client=mem,
        event_store=event_store,
        slow_memory_ms=slow,
        use_llm_extraction=False,  # offline regression uses mock LLM extractor (no Nemotron)
    )
    for turn in scenario.get("turns", []):
        if isinstance(turn, dict):
            if turn.get("speaker") == "background":
                await kernel.process_background_speech(turn.get("text", ""))
            else:
                await kernel.process_caller_turn(turn.get("text", ""))
        else:
            await kernel.process_caller_turn(str(turn))

    results = [_check_assertion(a, kernel.state) for a in scenario.get("assertions", [])]
    passed = all(r.ok for r in results)
    failure_type = None
    if not passed:
        failure_type = _infer_failure_type(scenario, results)
    return ScenarioResult(
        id=scenario["id"],
        title=scenario.get("title", scenario["id"]),
        incident_family=scenario.get("incident_family", "unknown"),
        passed=passed,
        assertions=results,
        failure_type=failure_type,
        snapshot=kernel.state.snapshot(),
    )


def _infer_failure_type(scenario: dict[str, Any], results: list[AssertionResult]) -> str:
    if scenario.get("failure_type_if_failed"):
        return scenario["failure_type_if_failed"]
    failed = {r.check.split("=")[0] for r in results if not r.ok}
    if "third_party_risk_active_at_end" in failed:
        return "WRONG_BRANCH_CLOSURE"
    if "asked_slot" in failed:
        return "MISSING_CRITICAL_QUESTION"
    if "retrieved_memory_substr" in failed:
        return "MEMORY_RETRIEVAL_FAILURE"
    if "escalation_recommended" in failed:
        return "BAD_ESCALATION"
    if "suppressed_interruption_during_correction" in failed:
        return "WRONG_INTERRUPTION"
    if "upgraded_incident" in failed:
        return "SOP_VIOLATION"
    return "SOP_VIOLATION"


# --------------------------------------------------------------------------- #
# Suite metrics
# --------------------------------------------------------------------------- #
@dataclass
class SuiteResult:
    results: list[ScenarioResult]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"results": [r.to_dict() for r in self.results], "summary": self.summary}


def _summarize(results: list[ScenarioResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    missed_trapped = 0
    wrong_branch = 0
    prior_memory = 0
    crit_times: list[int] = []
    for r in results:
        for a in r.assertions:
            base = a.check.split("=")[0]
            if base == "asked_slot" and a.expected == "trapped_person_status" and not a.ok:
                missed_trapped += 1
            if base == "third_party_risk_active_at_end" and a.expected is True and not a.ok:
                wrong_branch += 1
        mem = r.snapshot.get("memory", {}).get("results", [])
        if any(m.get("memory_type") in ("prior_call", "eval_failure", "location_alias") for m in mem):
            prior_memory += 1
        t = r.snapshot.get("first_critical_guidance_ms")
        if t:
            crit_times.append(t)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "missed_trapped_person_question": missed_trapped,
        "wrong_branch_closure": wrong_branch,
        "prior_memory_retrieved": prior_memory,
        "avg_time_to_critical_guidance_ms": int(sum(crit_times) / len(crit_times)) if crit_times else 0,
    }


async def run_suite(scenario_ids: list[str] | None = None, event_store=None) -> SuiteResult:
    scenarios = load_scenarios()
    if scenario_ids:
        scenarios = [s for s in scenarios if s["id"] in scenario_ids]
    mem = ChronosMemoryClient(force_local=True)  # deterministic, offline
    results = [await run_scenario(s, mem, event_store) for s in scenarios]
    return SuiteResult(results=results, summary=_summarize(results))


# --------------------------------------------------------------------------- #
# Failure classification + patch generation (safe, structured)
# --------------------------------------------------------------------------- #
def classify_failure(scenario: dict[str, Any], result: ScenarioResult) -> dict[str, Any]:
    ftype = result.failure_type or "SOP_VIOLATION"
    target = config.load_policy("memory_retrieval_policy")  # placeholder load (cached)
    taxonomy = yaml.safe_load((config.DATA_DIR / "eval_assertions.yaml").read_text())
    target_file = taxonomy.get("patch_target_map", {}).get(ftype, "policies/structure_fire.yaml")
    failed_assertions = [a.check for a in result.assertions if not a.ok]
    return {
        "scenario_id": scenario["id"],
        "incident_type": result.snapshot.get("incident", {}).get("incident_type"),
        "failure_type": ftype,
        "failed_assertions": failed_assertions,
        "root_cause": (
            "Caller evacuation was allowed to resolve the third-party trapped-person "
            "branch. Caller safety and third-party safety must be tracked separately."
            if ftype == "WRONG_BRANCH_CLOSURE"
            else f"Scenario failed assertions: {failed_assertions}"
        ),
        "summary": scenario.get("expected", "").strip(),
        "target_file": target_file,
        "severity": "critical" if ftype in ("WRONG_BRANCH_CLOSURE", "BAD_ESCALATION", "SOP_VIOLATION") else "high",
    }


def generate_patch(failure: dict[str, Any], use_llm: bool = False) -> dict[str, Any]:
    """Produce a SAFE, structured patch. Deterministic by default; an LLM may be
    used to author the rationale text only (never raw policy)."""
    ftype = failure["failure_type"]
    target_file = failure.get("target_file", "policies/structure_fire.yaml")
    ops: list[dict[str, Any]] = []
    rationale = ""
    if ftype == "WRONG_BRANCH_CLOSURE":
        ops = [
            {"operation": "add_cannot_be_resolved_by_condition", "path": "third_party_risk.cannot_be_resolved_by", "value": "caller_personally_evacuated"},
            {"operation": "add_cannot_be_resolved_by_condition", "path": "third_party_risk.cannot_be_resolved_by", "value": "caller_says_i_am_safe"},
            {"operation": "add_required_until_resolved_action", "path": "third_party_risk.required_until_resolved", "value": "ask_if_anyone_inside"},
            {"operation": "add_required_until_resolved_action", "path": "third_party_risk.required_until_resolved", "value": "ask_last_known_location"},
            {"operation": "add_required_until_resolved_action", "path": "third_party_risk.required_until_resolved", "value": "escalate_human"},
        ]
        rationale = (
            "In structure-fire calls, caller evacuation does not resolve third-party "
            "trapped-person risk. Add a guard so caller_personally_evacuated and "
            "caller_says_i_am_safe can no longer close the branch; keep ask_if_anyone_inside, "
            "ask_last_known_location, and escalate_human required until explicitly resolved."
        )
    elif ftype == "MISSING_CRITICAL_QUESTION":
        ops = [{"operation": "set_escalation_required", "path": "escalation.required_if_any", "value": "third_party_risk_active"}]
        rationale = "Ensure the missing safety branch stays active and escalation is required."
    else:
        ops = [{"operation": "add_forbidden_guidance", "path": "forbidden_guidance", "value": "Do not close a safety branch without explicit confirmation."}]
        rationale = "Reinforce the safety guardrail relevant to the failed assertion."

    if use_llm:
        try:
            from chronos.llm_guidance import author_patch_rationale

            rationale = author_patch_rationale(failure, ops) or rationale
        except Exception:
            pass

    return {
        "failure_type": ftype,
        "target_file": target_file,
        "patch_operations": ops,
        "why_this_fixes_it": rationale,
        "regression_scenarios": _nearby_scenarios(failure),
        "risk_of_overfitting": "Low — the guard is a general safety rule, not a scenario-specific hack.",
    }


def _nearby_scenarios(failure: dict[str, Any]) -> list[str]:
    fam_map = {"structure_fire.yaml": "structure_fire", "vehicle_crash.yaml": "vehicle_crash"}
    fam = None
    for k, v in fam_map.items():
        if k in failure.get("target_file", ""):
            fam = v
    ids = [s["id"] for s in load_scenarios() if s.get("incident_family") == fam]
    return ids[:6]


# --------------------------------------------------------------------------- #
# Applying patches to YAML (clean text insertion for the known structure)
# --------------------------------------------------------------------------- #
def apply_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply structured ops to a policy file. Returns {before, after, diff}.

    Uses targeted text insertion for the third_party_risk guard (clean diff,
    preserves comments). Falls back to dict+dump for other ops.
    """
    rel = patch["target_file"]
    path = config.BASE_DIR / rel
    before = path.read_text()
    ops = patch["patch_operations"]

    # Group list-append ops by path.
    list_adds: dict[str, list[str]] = {}
    other_ops: list[dict[str, Any]] = []
    for op in ops:
        if op["operation"] in ("add_cannot_be_resolved_by_condition", "add_required_until_resolved_action", "add_trigger_phrase", "add_forbidden_guidance", "add_required_until_resolved"):
            list_adds.setdefault(op["path"], []).append(op["value"])
        else:
            other_ops.append(op)

    after = before
    if any(p.startswith("third_party_risk.") for p in list_adds):
        after = _insert_third_party_block(after, list_adds)
        list_adds = {p: v for p, v in list_adds.items() if not p.startswith("third_party_risk.")}

    # Remaining ops via dict mutation + safe_dump (rarely used in the demo).
    if list_adds or other_ops:
        data = yaml.safe_load(after) or {}
        for p, vals in list_adds.items():
            _ensure_list(data, p).extend(v for v in vals if v not in _ensure_list(data, p))
        for op in other_ops:
            if op["operation"] == "set_escalation_required":
                lst = _ensure_list(data, op["path"])
                if op["value"] not in lst:
                    lst.append(op["value"])
            elif op["operation"] == "raise_risk_level":
                _set_path(data, op["path"], op["value"])
        after = yaml.safe_dump(data, sort_keys=False)

    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=rel, tofile=rel + " (patched)"
        )
    )
    return {"before": before, "after": after, "diff": diff, "path": str(path)}


def _insert_third_party_block(text: str, list_adds: dict[str, list[str]]) -> str:
    lines = text.splitlines(keepends=True)
    # Find the `third_party_risk:` top-level key.
    tpr_idx = next((i for i, ln in enumerate(lines) if ln.rstrip("\n") == "third_party_risk:" or ln.startswith("third_party_risk:")), None)
    if tpr_idx is None:
        return text
    # End of the block = next top-level (col 0, non-comment) key, or EOF.
    end = len(lines)
    for j in range(tpr_idx + 1, len(lines)):
        ln = lines[j]
        if ln.strip() and not ln.startswith((" ", "#")) and ln[0] not in (" ", "\t"):
            end = j
            break
    block_lines: list[str] = []
    cannot = list_adds.get("third_party_risk.cannot_be_resolved_by")
    until = list_adds.get("third_party_risk.required_until_resolved")
    block = lines[tpr_idx:end]

    def _has_key(name: str) -> bool:
        # A REAL yaml key in this block (ignore comment lines that mention it).
        return any(
            ln.lstrip().startswith(name) and not ln.lstrip().startswith("#") for ln in block
        )

    if cannot and not _has_key("cannot_be_resolved_by:"):
        block_lines.append("  cannot_be_resolved_by:\n")
        block_lines += [f"    - {v}\n" for v in cannot]
    if until and not _has_key("required_until_resolved:"):
        block_lines.append("  required_until_resolved:\n")
        block_lines += [f"    - {v}\n" for v in until]
    if not block_lines:
        return text
    # Insert at end of the block (after the last non-empty line of the block).
    insert_at = end
    while insert_at - 1 > tpr_idx and not lines[insert_at - 1].strip():
        insert_at -= 1
    return "".join(lines[:insert_at] + block_lines + lines[insert_at:])


def _ensure_list(data: dict, dotted: str) -> list:
    cur = data
    parts = dotted.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    if not isinstance(cur.get(parts[-1]), list):
        cur[parts[-1]] = []
    return cur[parts[-1]]


def _set_path(data: dict, dotted: str, value: Any) -> None:
    cur = data
    parts = dotted.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


# --------------------------------------------------------------------------- #
# Orchestration: baseline -> patch -> rerun -> accept/reject
# --------------------------------------------------------------------------- #
async def run_improvement(
    failed_scenario_id: str | None = None, use_llm: bool = False, event_store=None
) -> dict[str, Any]:
    accept_pol = config.load_policy("improvement_policy").get("patch_acceptance", {})

    # 1) Baseline suite.
    baseline = await run_suite(event_store=event_store)
    failures = [r for r in baseline.results if not r.passed]
    if failed_scenario_id:
        failures = [r for r in failures if r.id == failed_scenario_id] or failures
    if not failures:
        report = {
            "status": "no_failures",
            "before": baseline.summary,
            "after": baseline.summary,
            "message": "All scenarios already pass — nothing to patch.",
        }
        _save_report(report)
        return report

    target = failures[0]
    scenario = get_scenario(target.id)
    failure = classify_failure(scenario, target)
    if event_store is not None:
        event_store.emit("cekura_failure", failure, target.id, target.id)

    # 2) Generate + apply patch (with revert-on-reject backup).
    patch = generate_patch(failure, use_llm=use_llm)
    if event_store is not None:
        event_store.emit("policy_patch_candidate", patch, target.id, target.id)
    patch_path = config.BASE_DIR / patch["target_file"]
    original_text = patch_path.read_text()
    applied = apply_patch(patch)
    patch_path.write_text(applied["after"])
    config._load_yaml_cached.cache_clear()  # force policy reload

    # 3) Rerun regression (failed + nearby + a few previously passing).
    after = await run_suite(event_store=event_store)

    # 4) Accept / reject.
    before_crit = baseline.summary["wrong_branch_closure"] + baseline.summary["missed_trapped_person_question"]
    after_crit = after.summary["wrong_branch_closure"] + after.summary["missed_trapped_person_question"]
    improved = after.summary["pass_rate"] > baseline.summary["pass_rate"]
    no_new_critical = after_crit <= before_crit
    accepted = improved and no_new_critical

    if not accepted:
        patch_path.write_text(original_text)  # revert
        config._load_yaml_cached.cache_clear()
        if event_store is not None:
            event_store.emit("policy_patch_rejected", {"patch": patch, "reason": "no improvement or new critical regression"}, target.id, target.id)
        report = {
            "status": "rejected",
            "failure": failure,
            "patch": patch,
            "before": baseline.summary,
            "after": after.summary,
            "policy_diff": applied["diff"],
        }
        _save_report(report)
        return report

    # 5) Accepted: write failure memory, emit, save report.
    mem = ChronosMemoryClient(api_key=_supermemory_key())
    mem_write = mem.write_failure_memory(
        failure, {**patch, "target_file": patch["target_file"]},
        {"before_pass_rate": baseline.summary["pass_rate"], "after_pass_rate": after.summary["pass_rate"]},
    )
    if event_store is not None:
        event_store.emit("policy_patch_accepted", {"patch": patch, "memory": mem_write}, target.id, target.id)

    report = {
        "status": "accepted",
        "failure": failure,
        "patch": patch,
        "before": baseline.summary,
        "after": after.summary,
        "policy_diff": applied["diff"],
        "failure_memory": mem_write,
        "per_scenario_before": [{"id": r.id, "passed": r.passed} for r in baseline.results],
        "per_scenario_after": [{"id": r.id, "passed": r.passed} for r in after.results],
    }
    _save_report(report)
    return report


def _supermemory_key() -> str | None:
    import os

    return os.getenv("SUPERMEMORY_API_KEY") or None


def _save_report(report: dict[str, Any]) -> None:
    try:
        with open(config.RUNTIME_DIR / "improvement.json", "w") as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass


def revert_policies_to_baseline() -> None:
    """Reset structure_fire.yaml to its baseline (un-patched) form for re-demoing."""
    # Re-derive baseline by stripping the guard block if present.
    path = config.POLICIES_DIR / "structure_fire.yaml"
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    out = []
    skip = False
    for ln in lines:
        if ln.strip().startswith(("cannot_be_resolved_by:", "required_until_resolved:")):
            skip = True
            continue
        if skip:
            if ln.lstrip().startswith("- "):
                continue
            skip = False
        out.append(ln)
    path.write_text("".join(out))
    config._load_yaml_cached.cache_clear()


if __name__ == "__main__":
    suite = asyncio.run(run_suite())
    print(json.dumps(suite.summary, indent=2))
    for r in suite.results:
        mark = "PASS" if r.passed else "FAIL"
        extra = "" if r.passed else f"  failed={[a.check for a in r.assertions if not a.ok]} type={r.failure_type}"
        print(f"  [{mark}] {r.id}{extra}")
