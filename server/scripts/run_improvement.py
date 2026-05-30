#!/usr/bin/env python
"""Run the Chronos self-improvement loop.

Reads the baseline suite, classifies the first failure, generates a SAFE policy
patch, applies it, reruns regression, accepts only if it improves with no new
critical regression, and writes the failure memory back to Supermemory. Writes
runtime/improvement.json for the dashboard.

Usage:
  uv run python scripts/run_improvement.py            # deterministic loop
  uv run python scripts/run_improvement.py --llm      # author rationale w/ Nemotron
  uv run python scripts/run_improvement.py --revert   # reset policies to baseline
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from chronos.events import STORE  # noqa: E402
from chronos.improvement_loop import revert_policies_to_baseline, run_improvement  # noqa: E402


def _fmt(summary: dict) -> str:
    return (
        f"pass={summary.get('pass_rate')} "
        f"missed_trapped={summary.get('missed_trapped_person_question')} "
        f"wrong_branch={summary.get('wrong_branch_closure')} "
        f"prior_mem={summary.get('prior_memory_retrieved')}"
    )


async def main() -> None:
    if "--revert" in sys.argv:
        revert_policies_to_baseline()
        print("Reverted policies/structure_fire.yaml to baseline.")
        return

    use_llm = "--llm" in sys.argv
    rep = await run_improvement(use_llm=use_llm, event_store=STORE)
    print("=" * 64)
    print(f"SELF-IMPROVEMENT: {rep['status'].upper()}")
    print("=" * 64)
    if rep["status"] == "no_failures":
        print(rep["message"])
        return
    print(f"Failure: {rep['failure']['failure_type']} ({rep['failure']['scenario_id']})")
    print(f"Root cause: {rep['failure']['root_cause']}")
    print(f"Patch -> {rep['patch']['target_file']}")
    print(f"Why: {rep['patch']['why_this_fixes_it']}")
    print("-" * 64)
    print(f"BEFORE  {_fmt(rep['before'])}")
    print(f"AFTER   {_fmt(rep['after'])}")
    print("-" * 64)
    print("POLICY DIFF:")
    print(rep["policy_diff"])
    if rep.get("failure_memory"):
        print(f"Failure memory written: {json.dumps(rep['failure_memory'])}")


if __name__ == "__main__":
    asyncio.run(main())
