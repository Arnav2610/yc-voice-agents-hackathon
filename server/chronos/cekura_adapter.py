"""Cekura adapter.

Bridges Cekura voice-agent evaluations into the Chronos self-improvement loop:
  * scenarios_to_cekura_testcases(): render data/cekura_scenarios.yaml into
    Cekura-friendly test-case specs (persona, script, expected, pass/fail).
  * run_scenarios_via_rest(): kick off a Pipecat-v2 evaluator run (needs a key +
    a reachable agent).
  * parse_cekura_report(): normalize a Cekura report into the failure objects the
    improvement loop classifies and patches.
  * load_fake_report(): the seeded baseline report from the build spec, used when
    a live run isn't available so the demo never blocks.

The fastest live path in the hackathon is driving Cekura from Claude Code via the
Cekura MCP (`/cekura-report`); this module is the programmatic complement.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import yaml

from chronos import config

CEKURA_BASE = "https://api.cekura.ai"


def scenarios_to_cekura_testcases() -> list[dict[str, Any]]:
    """Render our scenarios as Cekura test-case specs (provider: Pipecat)."""
    data = yaml.safe_load((config.DATA_DIR / "cekura_scenarios.yaml").read_text()) or {}
    out = []
    for s in data.get("scenarios", []):
        caller_lines = [t["text"] for t in s.get("turns", []) if isinstance(t, dict) and t.get("speaker") == "caller"]
        bg_lines = [t["text"] for t in s.get("turns", []) if isinstance(t, dict) and t.get("speaker") == "background"]
        out.append(
            {
                "id": s["id"],
                "name": s.get("title", s["id"]),
                "persona": s.get("persona", ""),
                "caller_script": caller_lines,
                "background_speech": bg_lines,
                "expected_behavior": (s.get("expected") or "").strip(),
                "incident_family": s.get("incident_family"),
                "assertions": s.get("assertions", []),
            }
        )
    return out


def run_scenarios_via_rest(
    scenario_ids: list[int], name: str = "chronos-regression", frequency: int = 1, api_key: str | None = None
) -> dict[str, Any]:
    """Trigger a Cekura Pipecat-v2 evaluator run. `scenario_ids` are Cekura
    scenario IDs (created in the Cekura dashboard / via MCP)."""
    api_key = api_key or os.getenv("CEKURA_API_KEY")
    if not api_key:
        return {"ok": False, "error": "no CEKURA_API_KEY; use the Cekura MCP /cekura-report flow instead"}
    body = {"scenarios": [{"scenario": sid} for sid in scenario_ids], "name": name, "frequency": frequency}
    req = urllib.request.Request(
        f"{CEKURA_BASE}/test_framework/v1/scenarios/run_scenarios_pipecat_v2/",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-CEKURA-API-KEY": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"ok": True, "response": json.loads(resp.read().decode() or "{}")}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return {"ok": False, "error": str(e)}


def parse_cekura_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a Cekura report's failures into Chronos failure objects."""
    failures = []
    for f in report.get("failures", []) or []:
        failures.append(
            {
                "scenario_id": f.get("scenario_id") or f.get("scenario") or f.get("id"),
                "failure_type": f.get("failure_type", "SOP_VIOLATION"),
                "failed_assertions": f.get("failed_assertions", []),
                "transcript": f.get("transcript", ""),
                "expected": f.get("expected", ""),
                "incident_type": f.get("incident_type", "structure_fire"),
                "root_cause": f.get("root_cause", ""),
            }
        )
    return failures


def load_fake_report(kind: str = "baseline") -> dict[str, Any]:
    """The seeded report from the build spec (used when no live run is available)."""
    if kind == "after":
        return {
            "run_id": "cekura_demo_after_001",
            "summary": {
                "pass_rate": 1.0,
                "missed_trapped_person_question": 0,
                "wrong_branch_closure": 0,
                "prior_memory_retrieved": 11,
                "avg_time_to_critical_guidance_ms": 1440,
            },
            "failures": [],
        }
    return {
        "run_id": "cekura_demo_baseline_001",
        "summary": {
            "pass_rate": 0.58,
            "missed_trapped_person_question": 4,
            "wrong_branch_closure": 3,
            "prior_memory_retrieved": 5,
            "avg_time_to_critical_guidance_ms": 4100,
        },
        "failures": [
            {
                "scenario_id": "structure_fire_neighbor_inside_001",
                "failure_type": "WRONG_BRANCH_CLOSURE",
                "failed_assertions": [
                    "third_party_risk_closed_after_caller_evacuated",
                    "missed_neighbor_last_known_location",
                ],
                "transcript": "Caller said they got out but neighbor may still be inside. Agent marked caller safe and moved to wrap-up.",
                "expected": "Keep third-party risk active and ask where neighbor was last seen.",
                "incident_type": "structure_fire",
            }
        ],
    }


def load_cekura_ids() -> dict[str, Any]:
    """Load the live Cekura agent/scenario IDs created for this project."""
    path = config.DATA_DIR / "cekura_ids.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def cekura_scenario_ids() -> list[int]:
    """The numeric Cekura scenario IDs (for run_scenarios_pipecat_v2)."""
    return [s["id"] for s in load_cekura_ids().get("scenarios", [])]


def save_report(report: dict[str, Any], name: str) -> None:
    try:
        with open(config.RUNTIME_DIR / name, "w") as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass
