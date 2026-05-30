#!/usr/bin/env python
"""Reset the demo to its baseline (un-patched) state.

Reverts policies/structure_fire.yaml and clears the runtime improvement report so
the dashboard shows a clean baseline before the next self-improvement run.

Usage:  uv run python scripts/reset_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chronos import config  # noqa: E402
from chronos.improvement_loop import revert_policies_to_baseline  # noqa: E402


def main() -> None:
    revert_policies_to_baseline()
    print("Reverted policies/structure_fire.yaml to baseline.")
    for name in ("improvement.json", "latest.json"):
        p = config.RUNTIME_DIR / name
        if p.exists():
            p.unlink()
            print(f"Removed runtime/{name}")
    print("Demo reset complete.")


if __name__ == "__main__":
    main()
