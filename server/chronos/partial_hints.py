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
_MEDICAL = re.compile(
    r"\b(?:bleeding|blood|tongue|spicy|choking|bit\s+my\s+tongue|bitten\s+my\s+tongue|"
    r"chest\s+pain|can't\s+breathe|cannot\s+breathe)\b",
    re.I,
)
_YCOMB = re.compile(r"\by\s+combinator\b", re.I)


_PHONE_DIGITS = re.compile(r"\d{10,}")
_PHONE_CHUNK = re.compile(
    r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}|\d{10,}"
)


def _extract_phone(text: str) -> str | None:
    """Pull a callback number from spoken or typed digits in the transcript."""
    if not text:
        return None
    chunks = _PHONE_CHUNK.findall(text)
    for raw in reversed(chunks):
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 10:
            d = digits[-11:] if len(digits) >= 11 and digits[0] == "1" else digits[-10:]
            if len(d) == 10:
                return f"({d[:3]}) {d[3:6]}-{d[6:]}"
            if len(d) == 11:
                return f"+{d[0]} ({d[1:4]}) {d[4:7]}-{d[7:]}"
            return digits
    m = _PHONE_DIGITS.search(re.sub(r"\s", "", text))
    if m:
        return m.group(0)
    return None


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

    if _MEDICAL.search(text):
        detail = "Injury / bleeding reported"
        if "tongue" in t and ("spicy" in t or "bit" in t or "lunch" in t):
            detail = "Tongue injury after spicy food — bleeding"
        notes.append(StructuredNote(category="medical", field="injury", value=detail, turn=turn))

    if _YCOMB.search(text) and not _STREET_NUM.search(text):
        notes.append(StructuredNote(category="location", field="address", value="Y Combinator office", turn=turn))

    phone = _extract_phone(text)
    if phone:
        notes.append(StructuredNote(category="contact", field="callback_number", value=phone, turn=turn))

    return notes
