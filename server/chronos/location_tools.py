"""Location enrichment via Google Maps Platform (Geocoding + Places).

Uses MAPS_API_KEY from the environment. Falls back to deterministic mocks when
the key is missing or the API errors — safe for offline regression tests.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote_plus

import aiohttp

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

_STREET_ADDRESS = re.compile(
    r"\b\d+\s+[\w\s.'-]{2,40}\b(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|hwy|highway)\b",
    re.I,
)

_FACILITY_TYPES = {
    "ems": ("hospital", "Emergency hospital"),
    "fire": ("fire_station", "Fire station"),
    "police": ("police", "Police station"),
}


def maps_configured() -> bool:
    return bool(os.getenv("MAPS_API_KEY", "").strip())


def _mock_resolve(raw_location: str | None) -> dict[str, Any]:
    """Deterministic fallback when Maps is unavailable."""
    from chronos.mocks import resolve_location

    base = resolve_location(raw_location)
    return {
        "query": raw_location,
        "source": "mock",
        "formatted_address": base.get("canonical") or raw_location,
        "confidence": base.get("confidence", 0.5),
        "needs_confirmation": base.get("needs_confirmation", True),
        "lat": None,
        "lng": None,
        "place_id": None,
        "aliases": base.get("aliases") or [],
        "location_type": None,
        "partial_match": True,
        "simulated": True,
    }


async def geocode_location(
    query: str,
    *,
    region_bias: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Resolve a landmark or address string to a formatted street address + coordinates."""
    query = (query or "").strip()
    if not query:
        return _mock_resolve(None)

    api_key = os.getenv("MAPS_API_KEY", "").strip()
    if not api_key:
        return _mock_resolve(query)

    params: dict[str, str] = {"address": query, "key": api_key}
    if region_bias:
        params["region"] = region_bias

    close_session = session is None
    if session is None:
        session = aiohttp.ClientSession()

    try:
        async with session.get(_GEOCODE_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        out = _mock_resolve(query)
        out["error"] = str(exc)
        return out
    finally:
        if close_session:
            await session.close()

    status = data.get("status")
    if status != "OK" or not data.get("results"):
        out = _mock_resolve(query)
        out["maps_status"] = status
        return out

    best = data["results"][0]
    loc = best.get("geometry", {}).get("location") or {}
    loc_type = best.get("geometry", {}).get("location_type")
    types = best.get("types") or []
    partial = bool(best.get("partial_match"))
    formatted = best.get("formatted_address") or query

    precise = loc_type in ("ROOFTOP", "RANGE_INTERPOLATED") or any(
        t in types for t in ("street_address", "premise", "subpremise", "establishment")
    )
    needs_confirmation = partial or not precise
    if _STREET_ADDRESS.search(formatted) and not partial:
        needs_confirmation = False

    confidence = 0.92 if not needs_confirmation else 0.72 if precise else 0.55

    return {
        "query": query,
        "source": "google_maps",
        "formatted_address": formatted,
        "confidence": confidence,
        "needs_confirmation": needs_confirmation,
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "place_id": best.get("place_id"),
        "aliases": [],
        "location_type": loc_type,
        "types": types[:6],
        "partial_match": partial,
        "simulated": False,
    }


async def find_nearest_facility(
    lat: float,
    lng: float,
    facility: str = "ems",
    *,
    radius_m: int = 8000,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Find nearest hospital / fire station / police via Places Nearby Search."""
    facility = (facility or "ems").lower()
    place_type, label = _FACILITY_TYPES.get(facility, ("hospital", "Facility"))

    api_key = os.getenv("MAPS_API_KEY", "").strip()
    if not api_key:
        return {
            "facility": facility,
            "simulated": True,
            "note": "MAPS_API_KEY not set — illustrative result only",
            "name": f"Nearest {label} (simulated)",
            "address": "Unknown — geocoding disabled",
            "distance_m": None,
        }

    params = {
        "location": f"{lat},{lng}",
        "radius": str(radius_m),
        "type": place_type,
        "key": api_key,
    }

    close_session = session is None
    if session is None:
        session = aiohttp.ClientSession()

    try:
        async with session.get(_PLACES_NEARBY_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"facility": facility, "error": str(exc), "simulated": True}
    finally:
        if close_session:
            await session.close()

    if data.get("status") != "OK" or not data.get("results"):
        return {
            "facility": facility,
            "maps_status": data.get("status"),
            "simulated": True,
            "note": "No nearby facility found in Places API",
        }

    place = data["results"][0]
    return {
        "facility": facility,
        "name": place.get("name"),
        "address": place.get("vicinity") or place.get("formatted_address"),
        "place_id": place.get("place_id"),
        "lat": place.get("geometry", {}).get("location", {}).get("lat"),
        "lng": place.get("geometry", {}).get("location", {}).get("lng"),
        "distance_m": None,
        "simulated": False,
    }


def maps_navigation_url(lat: float | None, lng: float | None, label: str | None = None) -> str | None:
    """Return a Google Maps directions deep link for dispatch boards."""
    if lat is None or lng is None:
        return None
    q = quote_plus(label) if label else f"{lat},{lng}"
    return f"https://www.google.com/maps/search/?api=1&query={q}"
