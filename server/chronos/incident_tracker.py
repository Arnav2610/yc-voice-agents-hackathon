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

# High-signal phrases (word-bounded) — weighted above YAML trigger_phrases.
_STRONG: dict[str, list[str]] = {
    "vehicle_crash": [
        "crashed", "rear-ended", "rear ended", "pile up", "pileup",
        "head-on", "fender bender", "car crash", "vehicle crash",
    ],
    "structure_fire": [
        "apartment building", "building fire", "my building", "our building",
        "in the building", "third floor", "second floor", "fire started",
        "there's a fire", "there is a fire", "office fire", "room is on fire",
    ],
    "medical": [
        "chest pain", "not breathing", "can't breathe", "cannot breathe",
        "cant breathe", "trouble breathing", "hard to breathe", "struggling to breathe",
        "difficulty breathing", "shortness of breath", "can't catch my breath",
        "unconscious", "seizure", "stroke", "overdose", "heart attack",
        "choking", "collapsed", "passed out", "allergic reaction",
    ],
    "non_emergency_noise": [
        "loud music", "noise complaint", "parked in front", "blocking my driveway",
        "parked in front of my driveway", "barking",
    ],
}

# Bare triggers that MUST NOT match as substrings inside other words (e.g. accident/accidentally).
_BOUNDARY_ONLY: dict[str, set[str]] = {
    "vehicle_crash": {"crash", "accident", "collision", "car", "vehicle", "highway", "freeway", "exit", "shoulder"},
}

# Meta-references to incident types (caller complaining about the copilot, not describing their emergency).
_META_TYPE_REF = re.compile(
    r"\b(?:going to|classified as|says|said|shows|showing|keeps? (?:going|switching)|why (?:does|is)|"
    r"wrong(?:ly)? (?:classified|labeled)|not a)\s+(?:vehicle crash|car crash|structure fire|medical)\b",
    re.I,
)

# Vehicle crash requires real vehicle/road context — not just the word "crash" or "accident".
_VEHICLE_CONTEXT = re.compile(
    r"\b(?:car|vehicle|truck|suv|van|motorcycle|highway|freeway|interstate|"
    r"exit\s+\d+|shoulder|rear-ended|head-on|wreck|pileup|pile up|"
    r"\d{2,3}\s+(?:north|south|east|west)|fender bender)\b",
    re.I,
)
_EXPLICIT_VEHICLE_CRASH = re.compile(
    r"\b(?:car crash(?:ed)?|vehicle crash(?:ed)?|crashed (?:my|the|a|our)|"
    r"i crashed|we crashed|got in (?:a|an) accident|in an accident)\b",
    re.I,
)

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
_RE_AT_LANDMARK = re.compile(
    r"\b(?:i(?:'m|\s+am)\s+at(?:\s+the)?|we(?:'re|\s+are)\s+at(?:\s+the)?|located at(?:\s+the)?)\s+"
    r"([A-Za-z0-9][\w\s]{2,45}?)(?:\s+(?:and|but|when|they|it|\.|,)|$)",
    re.I,
)
_DEFINITIVE_ADDR = re.compile(
    r"\b(?:my address is|the address is|i(?:'m|\s+am) at|located at)\s+\d",
    re.I,
)

# Priority when multiple types score (higher index = higher priority).
_TYPE_PRIORITY = ["non_emergency_noise", "vehicle_crash", "medical", "structure_fire"]


@dataclass
class IncidentUpdate:
    incident_type: str | None
    confidence: float
    changed: bool
    location_raw: str | None
    location_uncertain: bool
    correction_detected: bool
    upgraded_to: str | None = None
    secondary_types: list[str] | None = None


def _phrase_matches(phrase: str, text: str, incident_type: str) -> bool:
    """Word-bounded phrase match; blocks substring false positives like accident/accidentally."""
    if not phrase:
        return False
    p = phrase.lower().strip()
    if _META_TYPE_REF.search(text):
        # Ignore type-name tokens that appear only in meta-complaints about classification.
        stripped = _META_TYPE_REF.sub(" ", text)
        if p not in stripped:
            return False
        text = stripped
    if p in _BOUNDARY_ONLY.get(incident_type, set()):
        return bool(re.search(rf"\b{re.escape(p)}\b", text, re.I))
    return p in text


def _vehicle_crash_valid(text: str) -> bool:
    """True only when the caller is describing a real vehicle/road crash."""
    if _META_TYPE_REF.search(text) and not _EXPLICIT_VEHICLE_CRASH.search(text):
        return False
    if _EXPLICIT_VEHICLE_CRASH.search(text):
        return True
    # Highway + exit without an explicit "crash" word (caller still reporting a roadway incident).
    if re.search(r"\b\d{2,3}\s+(?:north|south|east|west)\b", text) and re.search(
        r"\bexit\s+\d+", text
    ):
        return True
    has_vehicle = bool(_VEHICLE_CONTEXT.search(text))
    has_crash_word = bool(re.search(r"\b(?:crashed|collision|wreck|pileup|pile up)\b", text, re.I))
    return has_vehicle and has_crash_word


def _score_types(text: str) -> dict[str, float]:
    scores: dict[str, float] = {c: 0.0 for c in _CANDIDATES}
    for c in _CANDIDATES:
        for phrase in _trigger_phrases(c):
            if _phrase_matches(phrase, text, c):
                scores[c] += 1.0
        for phrase in _STRONG.get(c, []):
            if _phrase_matches(phrase, text, c):
                scores[c] += 3.0

    # Fire/smoke: structure fire unless this is clearly a vehicle incident.
    if re.search(r"\b(?:fire|smoke|flames|burning)\b", text, re.I):
        if _vehicle_crash_valid(text):
            scores["vehicle_crash"] += 3.0
        else:
            scores["structure_fire"] += 4.0
    # Breathing problems strongly imply medical.
    if re.search(
        r"\b(?:can't breathe|cannot breathe|cant breathe|trouble breathing|struggling to breathe|"
        r"hard to breathe|not breathing|difficulty breathing|shortness of breath)\b",
        text,
        re.I,
    ):
        scores["medical"] += 4.0

    # Vehicle crash: gate — zero out unless genuinely a vehicle incident.
    if not _vehicle_crash_valid(text):
        scores["vehicle_crash"] = 0.0

    return scores


def _pick_best(scores: dict[str, float], prev_type: str | None, correction: bool, text: str) -> tuple[str | None, float]:
    """Choose incident type: highest score wins; priority breaks ties only."""
    ranked = sorted(
        ((c, s) for c, s in scores.items() if s > 0),
        key=lambda kv: (kv[1], _TYPE_PRIORITY.index(kv[0])),
        reverse=True,
    )
    if not ranked:
        return prev_type, 0.0

    best_type, best_score = ranked[0]
    # Combined medical + fire -> structure_fire (fire dispatch takes priority).
    if scores.get("medical", 0) > 0 and scores.get("structure_fire", 0) > 0:
        if re.search(r"\b(?:fire|smoke|flames|burning)\b", text, re.I):
            best_type, best_score = "structure_fire", scores["structure_fire"]

    # Sticky: don't flip to a lower-scoring type unless correction or clear upgrade.
    if prev_type and not correction and prev_type in scores and scores[prev_type] > 0:
        prev_score = scores[prev_type]
        prev_pri = _TYPE_PRIORITY.index(prev_type)
        best_pri = _TYPE_PRIORITY.index(best_type)
        if best_pri > prev_pri:
            pass  # allow upgrade to higher-priority type (e.g. medical -> structure_fire)
        elif best_type != prev_type and best_score < prev_score + 2:
            best_type, best_score = prev_type, prev_score

    total = sum(scores.values()) or 1.0
    confidence = min(0.99, round(best_score / total, 2))
    return best_type, confidence


def _trigger_phrases(incident_type: str) -> list[str]:
    pol = config.policy_for_incident(incident_type)
    return [str(p).lower() for p in pol.get("trigger_phrases", [])]


class IncidentTracker:
    def __init__(self) -> None:
        self._last_type: str | None = None

    def update(self, cumulative_text: str, prev_type: str | None) -> IncidentUpdate:
        text = cumulative_text.lower()

        scores = _score_types(text)
        correction = any(t in text for t in _CORRECTION_TERMS)
        best_type, confidence = _pick_best(scores, prev_type or self._last_type, correction, text)

        # Secondary types for combined emergencies (e.g. medical + fire).
        secondary = [
            c for c, s in scores.items()
            if s > 0 and c != best_type and c in ("medical", "structure_fire")
        ]

        # Noise -> disturbance/threat upgrade.
        upgraded_to: str | None = None
        if best_type == "non_emergency_noise" or "loud music" in text or "noise complaint" in text:
            noise_pol = config.load_policy("non_emergency_noise")
            for trig, spec in (noise_pol.get("upgrade_triggers") or {}).items():
                if str(trig).lower() in text:
                    upgraded_to = spec.get("new_incident_type", "possible_active_disturbance")
            if upgraded_to:
                best_type = upgraded_to

        location_raw, location_uncertain = self._extract_location(text)
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
            secondary_types=secondary or None,
        )

    def _extract_location(self, text: str) -> tuple[str | None, bool]:
        at = _RE_AT_ADDRESS.findall(text)
        if at:
            loc = at[0].strip().rstrip(".,;")
            definitive = bool(_DEFINITIVE_ADDR.search(text))
            uncertain = not definitive or " or " in text or "maybe" in text
            return loc, uncertain

        landmarks = _RE_AT_LANDMARK.findall(text)
        if landmarks:
            loc = landmarks[-1].strip().rstrip(".,;")
            if len(loc) > 3 and not loc.startswith(("the emergency", "the phone")):
                return loc, False

        streets = _RE_STREET_ADDR.findall(text)
        if streets:
            distinct = list(dict.fromkeys(s.strip() for s in streets))
            uncertain = len(distinct) > 1 or " or " in text or "maybe" in text
            return distinct[0], uncertain

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
        uncertain = len(distinct) > 1 or len(exits) > 1 or " or " in text or "maybe" in text
        return distinct[0], uncertain
