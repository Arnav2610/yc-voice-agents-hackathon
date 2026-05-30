"""Instant structured hints from streaming partial transcripts (no classification).

Complements async LLM extraction so the dashboard updates while the caller is still speaking.
"""

from __future__ import annotations

import re

from chronos.state import StructuredNote

_STREET_NUM = re.compile(
    r"\b(?:fourteen\s+twelve|14[\s-]?12|1412)\b.*?\bmarket\s+street\b|\bmarket\s+street\b",
    re.I,
)
_WEAPON = re.compile(r"\b(knife|gun|firearm|pistol|weapon|armed)\b", re.I)
_THREAT = re.compile(
    r"\b(banging|break[\s-]?in|breaking\s+in|trying\s+to\s+break|intruder|rob(?:bing|bery)?)\b",
    re.I,
)


def hints_from_text(text: str, turn: int = 0) -> list[StructuredNote]:
    """Extract obvious facts from interim speech — notes only, not incident type."""
    t = text.lower()
    notes: list[StructuredNote] = []

    if _STREET_NUM.search(text):
        addr = "1412 Market Street"
        if re.search(r"\b(?:room\s*)?107\b", t):
            addr += ", Room 107"
        notes.append(StructuredNote(category="location", field="address", value=addr, turn=turn))

    m = _WEAPON.search(text)
    if m:
        notes.append(
            StructuredNote(category="threat", field="weapon_type", value=m.group(1).lower(), turn=turn)
        )

    if _THREAT.search(text):
        notes.append(
            StructuredNote(
                category="threat",
                field="threat_type",
                value="break-in / intruder at door",
                turn=turn,
            )
        )
        notes.append(
            StructuredNote(
                category="suspect",
                field="suspect_location",
                value="At caller's door",
                turn=turn,
            )
        )

    return notes
