"""Deterministic threat vs medical signal detection for classification guardrails."""

from __future__ import annotations

import re

_THREAT_RE = re.compile(
    r"\b(?:break[\s-]?in|breaking\s+in|intruder|rob(?:bing|bery)?|banging\s+on|"
    r"at\s+(?:my|the|our)\s+door|knife|gun|firearm|pistol|weapon|armed|"
    r"home\s+invasion|someone\s+(?:trying|threatening)|threatening\s+me|"
    r"kill\s+me|hostage|active\s+shooter|stabbed\s+(?:me|by)|attacker|"
    r"forced\s+entry|they\s+will\s+rob|shooting|assault(?:ed|ing)?)\b",
    re.I,
)

_MEDICAL_RE = re.compile(
    r"\b(?:bleeding|blood|tongue|spicy|choking|chest\s+pain|not\s+breathing|"
    r"can't\s+breathe|cannot\s+breathe|trouble\s+breathing|unconscious|seizure|"
    r"stroke|overdose|heart\s+attack|allergic\s+reaction|passed\s+out|"
    r"bit\s+my\s+tongue|bitten\s+my\s+tongue|injured\s+(?:myself|me)|"
    r"cut\s+myself|burned?\s+my|nose\s?bleed|vomiting\s+blood|"
    r"bent\s+my\s+tongue|lunch\s+was|food\s+(?:was|is)|mouth\s+(?:hurt|injur))\b",
    re.I,
)

_GOT_BIT_MEDICAL_CTX = re.compile(r"\b(?:tongue|lip|mouth|myself|spicy|food|lunch)\b", re.I)

_THREAT_TYPES = frozenset({"active_threat", "possible_active_disturbance"})

_FIRE_RE = re.compile(r"\b(?:fire|smoke|flames|burning)\b", re.I)


def has_threat_signals(text: str) -> bool:
    if not text:
        return False
    return bool(_THREAT_RE.search(text))


def has_medical_signals(text: str) -> bool:
    if not text:
        return False
    if _MEDICAL_RE.search(text):
        return True
    t = text.lower()
    if re.search(r"\bgot\s+bit\b", t) and _GOT_BIT_MEDICAL_CTX.search(text):
        return True
    return False


def medical_without_threat(text: str) -> bool:
    return has_medical_signals(text) and not has_threat_signals(text)


def normalize_incident_type(
    incident_type: str | None,
    transcript: str,
    *,
    partial: bool,
) -> str | None:
    """Reject premature threat labels; remap obvious medical self-injury away from threat."""
    if not incident_type or incident_type in ("unknown", "null"):
        return None
    if incident_type not in _THREAT_TYPES:
        return incident_type
    if has_threat_signals(transcript):
        return incident_type
    if _FIRE_RE.search(transcript):
        return "structure_fire"
    if medical_without_threat(transcript):
        return "medical"
    if partial:
        return None
    return incident_type
