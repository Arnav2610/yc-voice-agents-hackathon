"""Tests for Google Maps location enrichment (mocked HTTP)."""

from __future__ import annotations

import asyncio

from chronos.events import EventStore
from chronos.kernel import ChronosKernel
from chronos.location_tools import geocode_location
from chronos.memory_retrieval import ChronosMemoryClient


def test_geocode_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    result = asyncio.run(geocode_location("Y Combinator office"))
    assert result["source"] == "mock"
    assert result["simulated"] is True


def test_geocode_parses_google_response(monkeypatch):
    monkeypatch.setenv("MAPS_API_KEY", "test-key")

    class FakeResp:
        async def json(self):
            return {
                "status": "OK",
                "results": [
                    {
                        "formatted_address": "335 Pioneer Way, Mountain View, CA 94041, USA",
                        "geometry": {
                            "location": {"lat": 37.3947, "lng": -122.0744},
                            "location_type": "ROOFTOP",
                        },
                        "place_id": "ChIJtest",
                        "types": ["establishment", "point_of_interest"],
                    }
                ],
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            return FakeResp()

        async def close(self):
            return None

    async def run():
        return await geocode_location("Y Combinator office", session=FakeSession())

    result = asyncio.run(run())

    assert result["source"] == "google_maps"
    assert "Pioneer Way" in result["formatted_address"]
    assert result["lat"] == 37.3947
    assert result["needs_confirmation"] is False


def test_kernel_enrich_location_updates_dispatch_address(monkeypatch):
    monkeypatch.delenv("MAPS_API_KEY", raising=False)

    async def run():
        store = EventStore()
        k = ChronosKernel(
            "maps_test",
            memory_client=ChronosMemoryClient(force_local=True),
            event_store=store,
            use_llm_extraction=False,
        )
        k.state.incident.location_raw = "Y Combinator office"
        k._apply_geocode_result(
            {
                "query": "Y Combinator office",
                "source": "google_maps",
                "formatted_address": "335 Pioneer Way, Mountain View, CA 94041, USA",
                "confidence": 0.92,
                "needs_confirmation": False,
                "lat": 37.39,
                "lng": -122.07,
            }
        )
        k.state.incident.incident_type = "medical"
        k.state.incident.escalation_required = True
        sent = k.dispatch_simulated_units(["ems"], "Chest pain")
        assert sent
        assert "Pioneer Way" in sent[0]["location"]
        assert sent[0]["dispatch_address"] == k.dispatch_location()
        return k

    asyncio.run(run())
