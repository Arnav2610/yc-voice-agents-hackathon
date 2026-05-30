#!/usr/bin/env python
"""Write the seeded Cekura fallback reports (used when a live Cekura run is not
available, so the self-improvement story still demos). Also renders our scenarios
as Cekura test-case specs for reference.

Usage:  uv run python scripts/run_fake_cekura_report.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chronos.cekura_adapter import (  # noqa: E402
    load_fake_report,
    save_report,
    scenarios_to_cekura_testcases,
)


def main() -> None:
    baseline = load_fake_report("baseline")
    after = load_fake_report("after")
    save_report(baseline, "cekura_baseline.json")
    save_report(after, "cekura_after.json")
    print("Wrote runtime/cekura_baseline.json and runtime/cekura_after.json")
    print(json.dumps(baseline["summary"], indent=2))

    testcases = scenarios_to_cekura_testcases()
    save_report({"testcases": testcases}, "cekura_testcases.json")
    print(f"\nRendered {len(testcases)} Cekura test-case specs -> runtime/cekura_testcases.json")
    for tc in testcases:
        print(f"  - {tc['id']}: {tc['name']}")


if __name__ == "__main__":
    main()
