"""Incident hypothesis tracker.

Deterministic, fast pattern logic over the cumulative transcript. Classifies the
likely incident type, tracks location + uncertainty, and detects self-correction.
This never decides escalation on its own — that goes through policy in the kernel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chronos import config

# Incident types we classify, each backed by a policy file.
_CANDIDATES = ["structure_fire", "vehicle_crash", "non_emergency_noise", "medical"]

# High-signal phrases that strongly imply a type (weighted above base triggers).
_STRONG = {
    "vehicle_crash": [
        "crash", "crashed", "accident", "collision", "rear-ended", "rear ended",
        "pile up", "pileup", "head-on", "fender bender",
    ],
    "structure_fire": [
        "apartment building", "building fire", "apartment", "my building",
        "our building", "in the building", "third floor", "second floor",
    ],
    "medical": [
        "chest pain", "not breathing", "unconscious", "seizure", "stroke",
        "overdose", "heart attack", "choking", "collapsed", "passed out",
    ],
    "non_emergency_noise": [
        "loud music", "noise complaint", "parked in front", "blocking my driveway",
        "parked in front of my driveway", "barking",
    ],
}

_CORRECTION_TERMS = [
    "actually", "no wait", "i mean", "sorry", "correction", "or maybe", "scratch that",
]

# Location patterns — ordered from most specific/confident to most vague.
_RE_STREET_ADDR = re.compile(
    r"\b(\d{1,5}\s+[A-Za-z][A-Za-z0-9\s]{2,30}?(?:\s+(?:street|st|avenue|ave|road|rd|drive|dr|blvd|boulevard|lane|ln|way|place|pl|court|ct|circle|loop)))\b",
    re.I,
)
_RE_CROSS_STREET = re.compile(
    r"\b(\d+(?:st|nd|rd|th)?\s+and\s+[A-Za-z]+|"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+and\s+[A-Za-z]+)",
    re.I,
)
_RE_EXIT = re.compile(r"\b(exit\s+\d+)", re.I)
_RE_HIGHWAY = re.compile(r"\b(\d{2,3}\s+(?:north|south|east|west))\b", re.I)
_RE_NEAR = re.compile(r"\bnear\s+([A-Za-z0-9][\w\s]{2,25}?)(?:\s+(?:i|I|but|and|or|the|a)\b|[.,;]|$)", re.I)
_RE_AT_ADDRESS = re.compile(
    r"\b(?:i(?:'m|\s+am)\s+at|my address is|address is|located\s+at)\s+(\d{1,5}\s+[A-Za-z][\w\s]{2,35}?)(?:[.,;]|$)",
    re.I,
)
# Phrases that signal a definitive (confirmed) address was given.
_DEFINITIVE_ADDR = re.compile(
    r"\b(?:my address is|the address is|i(?:'m|\s+am) at|located at)\s+\d",
    re.I,
)


@dataclass
class IncidentUpdate:
    incident_type: str | None
    confidence: float
    changed: bool
    location_raw: str | None
    location_uncertain: bool
    correction_detected: bool
    upgraded_to: str | None = None


def _trigger_phrases(incident_type: str) -> list[str]:
    pol = config.policy_for_incident(incident_type)
    return [str(p).lower() for p in pol.get("trigger_phrases", [])]


class IncidentTracker:
    def __init__(self) -> None:
        self._last_type: str | None = None

    def update(self, cumulative_text: str, prev_type: str | None) -> IncidentUpdate:
        text = cumulative_text.lower()

        scores: dict[str, float] = {c: 0.0 for c in _CANDIDATES}
        for c in _CANDIDATES:
            for phrase in _trigger_phrases(c):
                if phrase and phrase in text:
                    scores[c] += 1.0
            for phrase in _STRONG.get(c, []):
                if phrase in text:
                    scores[c] += 3.0

        best_type: str | None = None
        best_score = 0.0
        for c, s in scores.items():
            if s > best_score:
                best_score = s
                best_type = c

        # Noise -> disturbance/threat upgrade.
        upgraded_to: str | None = None
        if best_type == "non_emergency_noise" or "loud music" in text or "noise" in text:
            noise_pol = config.load_policy("non_emergency_noise")
            for trig, spec in (noise_pol.get("upgrade_triggers") or {}).items():
                if str(trig).lower() in text:
                    upgraded_to = spec.get("new_incident_type", "possible_active_disturbance")
            if upgraded_to:
                best_type = upgraded_to

        # Confidence: normalized margin, capped.
        total = sum(scores.values()) or 1.0
        confidence = min(0.99, round(best_score / total, 2)) if best_type else 0.0

        location_raw, location_uncertain = self._extract_location(text)
        correction = any(t in text for t in _CORRECTION_TERMS)

        changed = best_type is not None and best_type != prev_type
        self._last_type = best_type or prev_type
        return IncidentUpdate(
            incident_type=best_type or prev_type,
            confidence=confidence,
            changed=changed,
            location_raw=location_raw,
            location_uncertain=location_uncertain,
            correction_detected=correction,
            upgraded_to=upgraded_to,
        )

    def _extract_location(self, text: str) -> tuple[str | None, bool]:
        # 1) Definitive "at address" phrases → no confirmation needed.
        at = _RE_AT_ADDRESS.findall(text)
        if at:
            loc = at[0].strip().rstrip(".,;")
            definitive = bool(_DEFINITIVE_ADDR.search(text))
            uncertain = not definitive or "or " in text or "maybe" in text
            return loc, uncertain

        # 2) Street address number + street name (e.g. "512 Pine Street").
        streets = _RE_STREET_ADDR.findall(text)
        if streets:
            distinct = list(dict.fromkeys(s.strip() for s in streets))
            uncertain = len(distinct) > 1 or " or " in text or "maybe" in text
            return distinct[0], uncertain

        # 3) Cross-streets (e.g. "5th and Pine").
        cross = _RE_CROSS_STREET.findall(text)
        exits = _RE_EXIT.findall(text)
        highways = _RE_HIGHWAY.findall(text)
        near = _RE_NEAR.findall(text)

        candidates: list[str] = []
        candidates += [c.strip() for c in cross]
        if highways and exits:
            candidates.append(f"{highways[0].strip()} {exits[0].strip()}")
        elif exits:
            candidates += [e.strip() for e in exits]
        elif highways:
            candidates += [h.strip() for h in highways]
        if not candidates and near:
            candidates += [n.strip() for n in near]

        if not candidates:
            return None, False

        distinct = list(dict.fromkeys(candidates))
        uncertain = (
            len(distinct) > 1
            or len(exits) > 1
            or " or " in text
            or "maybe" in text
        )
        return distinct[0], uncertain
