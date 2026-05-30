#!/usr/bin/env python
"""Seed Chronos institutional memory into Supermemory (and the local store).

Usage:  uv run python scripts/seed_memory.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from chronos.memory_retrieval import ChronosMemoryClient  # noqa: E402


def main() -> None:
    client = ChronosMemoryClient(api_key=os.getenv("SUPERMEMORY_API_KEY"))
    result = client.seed()
    print(f"Memory mode: {result['mode']}")
    print(f"Local records: {result['local_records']}")
    print(f"Pushed to Supermemory: {result['pushed_to_supermemory']}")
    if result["mode"] == "local":
        print("(No SUPERMEMORY_API_KEY — using local store only; demo still works.)")


if __name__ == "__main__":
    main()
