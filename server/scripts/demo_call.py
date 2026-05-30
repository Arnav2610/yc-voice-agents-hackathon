#!/usr/bin/env python
"""Play a scripted scenario through the live kernel and onto the dashboard.

This is a reliable, mic-free way to demo the live-call panels: it drives the SAME
Chronos kernel the voice bot uses, registers it with the dashboard, and steps
through the caller turns with a delay so you can watch the trace build.

Usage:
  uv run python scripts/demo_call.py                              # default scenario
  uv run python scripts/demo_call.py structure_fire_neighbor_inside_001
  uv run python scripts/demo_call.py --delay 2.0
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from chronos import config  # noqa: E402
from chronos.dashboard_server import set_live_kernel, start_dashboard_in_thread  # noqa: E402
from chronos.events import STORE  # noqa: E402
from chronos.improvement_loop import get_scenario  # noqa: E402
from chronos.kernel import ChronosKernel  # noqa: E402
from chronos.memory_retrieval import ChronosMemoryClient  # noqa: E402


async def main() -> None:
    args = [a for a in sys.argv[1:]]
    delay = 3.0
    if "--delay" in args:
        i = args.index("--delay")
        delay = float(args[i + 1])
        del args[i : i + 2]
    scenario_id = args[0] if args else "structure_fire_prior_gas_001"

    scenario = get_scenario(scenario_id)
    if not scenario:
        print(f"Unknown scenario: {scenario_id}")
        return

    try:
        start_dashboard_in_thread(config.DASHBOARD_PORT)
        await asyncio.sleep(0.4)
    except Exception:
        pass
    print(f"Dashboard: http://localhost:{config.DASHBOARD_PORT}")
    print(f"Playing scenario: {scenario_id}  (delay {delay}s/turn)\n")

    mem = ChronosMemoryClient(api_key=os.getenv("SUPERMEMORY_API_KEY"))
    print(f"Memory mode: {mem.mode}\n")
    kernel = ChronosKernel(f"democall_{scenario_id}", scenario_id=scenario_id, memory_client=mem, event_store=STORE)
    set_live_kernel(kernel)

    for turn in scenario.get("turns", []):
        speaker = turn.get("speaker", "caller")
        text = turn.get("text", "")
        if speaker == "background":
            print(f"  [BACKGROUND] {text}")
            await kernel.process_background_speech(text)
        else:
            print(f"  [CALLER]     {text}")
            await kernel.process_caller_turn(text)
        inc = kernel.state.incident
        print(
            f"     -> incident={inc.incident_type} risk={inc.risk_level} "
            f"third_party={inc.third_party_risk} escalate={inc.escalation_required}"
        )
        print(f"        next question: {kernel.state.recommended_question}")
        mem_hits = [m.content[:50] for m in kernel.state.memory.results[:2]]
        if mem_hits:
            print(f"        memory: {mem_hits}")
        print()
        await asyncio.sleep(delay)

    await kernel.on_call_complete()
    print("Call complete. Dashboard stays live — Ctrl+C to exit.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
