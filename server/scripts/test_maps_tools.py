#!/usr/bin/env python3
"""Live integration test for Google Maps + Chronos copilot tools.

Usage: cd server && uv run python scripts/test_maps_tools.py
Requires MAPS_API_KEY in ../.env
"""

from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"), override=True)


async def main() -> int:
    import aiohttp

    from chronos.events import EventStore
    from chronos.kernel import ChronosKernel
    from chronos.location_tools import find_nearest_facility, geocode_location, maps_configured
    from chronos.memory_retrieval import ChronosMemoryClient

    if not maps_configured():
        print("FAIL: MAPS_API_KEY not set in .env")
        return 1

    ok = True

    async with aiohttp.ClientSession() as session:
        for q in ("Y Combinator office", "1412 Market Street San Francisco"):
            r = await geocode_location(q, session=session)
            print(f"geocode({q!r}) -> {r.get('formatted_address')} [{r.get('source')}]")
            if r.get("source") != "google_maps":
                ok = False
                print("  FAIL:", r)

        base = await geocode_location("Y Combinator office", session=session)
        if base.get("lat") and base.get("lng"):
            ems = await find_nearest_facility(base["lat"], base["lng"], "ems", session=session)
            print(f"nearest EMS -> {ems.get('name')} @ {ems.get('address')}")
            if ems.get("simulated"):
                ok = False

    store = EventStore()
    k = ChronosKernel(
        "maps_live_test",
        memory_client=ChronosMemoryClient(force_local=True),
        event_store=store,
        use_llm_extraction=False,
    )
    k.state.incident.location_raw = "Y Combinator office"
    k.state.incident.incident_type = "medical"
    await k.enrich_location("Y Combinator office")
    print(f"kernel geocoded -> {k.state.incident.location_geocoded}")
    print(f"lat/lng -> {k.state.incident.location_lat}, {k.state.incident.location_lng}")
    if not k.state.incident.location_geocoded or not k.state.incident.location_lat:
        ok = False

    k.lookup_location_history()
    cad = k.log_simulated_cad()
    sent = k.dispatch_simulated_units(["ems"], "Live maps integration test")
    print(f"dispatch location -> {sent[0]['location'] if sent else 'NONE'}")
    print(f"CAD -> {cad.get('cad_event_id')}")

    events = [e["event_type"] for e in store.list(k.state.call_id)]
    if "tool_commit" not in events:
        ok = False
        print("FAIL: expected tool_commit events")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
