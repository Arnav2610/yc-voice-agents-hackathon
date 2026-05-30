"""Copilot tool helpers — location history, CAD logging, facility lookup."""

from __future__ import annotations

from typing import Any

from chronos.mocks import create_mock_cad_event, lookup_prior_incidents


def lookup_location_history(location: str | None, memory_hits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Combine mock prior-incident lookup with retrieved institutional memory."""
    loc = (location or "").strip()
    mock_rows = lookup_prior_incidents(loc)
    mem_rows: list[dict[str, Any]] = []
    for m in memory_hits or []:
        content = str(m.get("content") or "")
        if loc and loc.lower()[:8] in content.lower():
            mem_rows.append(
                {
                    "source": "supermemory",
                    "memory_type": m.get("memory_type"),
                    "snippet": content[:240],
                }
            )
    return {
        "location": loc or None,
        "prior_incidents": mock_rows,
        "memory_matches": mem_rows[:5],
        "count": len(mock_rows) + len(mem_rows),
        "simulated": True,
        "note": "Training lookup only — not live CAD history.",
    }


def log_simulated_cad(incident_state: dict[str, Any], *, dispatch_address: str | None = None) -> dict[str, Any]:
    """Create a simulated CAD event record before unit dispatch."""
    enriched = dict(incident_state)
    if dispatch_address:
        enriched["location_dispatch"] = dispatch_address
    cad = create_mock_cad_event(enriched)
    cad["dispatch_address"] = dispatch_address
    return cad
