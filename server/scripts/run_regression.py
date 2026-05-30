#!/usr/bin/env python
"""Run the Chronos scenario suite against the CURRENT policies (deterministic).

Writes runtime/metrics_before.json so the dashboard can show a baseline before
any improvement run.

Usage:  uv run python scripts/run_regression.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from chronos import config  # noqa: E402
from chronos.improvement_loop import run_suite  # noqa: E402


async def main() -> None:
    suite = await run_suite()
    print("=" * 64)
    print("CHRONOS REGRESSION (current policies)")
    print("=" * 64)
    print(json.dumps(suite.summary, indent=2))
    print("-" * 64)
    for r in suite.results:
        mark = "PASS" if r.passed else "FAIL"
        extra = "" if r.passed else f"   [{r.failure_type}] {[a.check for a in r.assertions if not a.ok]}"
        print(f"  [{mark}] {r.id}{extra}")
    with open(config.RUNTIME_DIR / "metrics_before.json", "w") as f:
        json.dump(suite.summary, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
