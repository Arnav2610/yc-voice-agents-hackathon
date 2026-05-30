"""Safety sentinel — deterministic high-risk detection.

Reports raw safety SIGNALS (hazards, third-party-inside, caller evacuation,
reentry intent, injury, weapons). It does NOT decide branch closure or
escalation; the kernel applies policy to these signals. This separation is what
lets a policy patch change behavior without touching detection code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_VEHICLE_CONTEXT = re.compile(
    r"\b(?:car|vehicle|truck|highway|freeway|interstate|exit\s+\d+|shoulder|"
    r"front of the car|rear-ended|head-on|wreck|pileup|fender bender)\b",
    re.I,
)

_PERSON_TERMS = [
    "neighbor", "baby", "child", "kid", "daughter", "son", "mother", "father",
    "mom", "dad", "grandma", "grandpa", "grandmother", "grandfather", "elderly",
    "someone", "somebody", "person", "people", "roommate", "wife", "husband",
]
_INSIDE_TERMS = [
    "inside", "in there", "still in", "in the unit", "in the building", "in 3b",
    "cannot get out", "can't get out", "couldn't get out", "didn't get out",
    "didn't make it out", "trapped", "stuck", "still in there",
]
_EVAC_TERMS = [
    "i got out", "we got out", "i'm out", "we're out", "made it out",
    "made it outside", "i'm outside", "we are outside", "we're outside",
    "got to the parking lot", "out to the parking lot", "i am safe", "i'm safe",
    "we are okay", "we're okay", "we are fine", "we're fine", "i am out",
]
_SAFE_STATEMENTS = [
    "i'm safe", "i am safe", "we are okay", "we're okay", "we are fine",
    "we're fine", "i think we are okay", "i think we're okay",
]
_EVERYONE_OUT = [
    "everyone is out", "everyone got out", "we all got out", "no one is inside",
    "nobody is inside", "no one's inside", "everyone is safe", "everyone's out",
    "they got out", "no one inside", "nobody inside",
]
_REENTRY_TERMS = [
    "go back in", "go back inside", "should i go back", "re-enter", "reenter",
    "go in to check", "go inside to check", "head back in",
]
_INJURY_TERMS = ["injured", "hurt", "bleeding", "broken", "pinned", "wound", "unconscious"]
_NO_INJURY = ["nobody is hurt", "no one is hurt", "not hurt", "nobody hurt", "no injuries"]
_WEAPON_THREAT = ["gun", "knife", "weapon", "threat", "threatening", "shooting", "shot"]


@dataclass
class SafetySignal:
    hazards: list[str] = field(default_factory=list)
    third_party_detected: bool = False
    third_party_terms: list[str] = field(default_factory=list)
    caller_evacuated: bool = False
    caller_safe_statement: bool = False
    everyone_out: bool = False
    reentry_intent: bool = False
    injury: bool = False
    weapon_or_threat: bool = False
    escalation_signals: list[str] = field(default_factory=list)


def _contains_non_self_person(text: str) -> tuple[bool, list[str]]:
    hits = [t for t in _PERSON_TERMS if t in text]
    return (len(hits) > 0, hits)


class SafetySentinel:
    def detect(self, turn_text: str, cumulative_text: str) -> SafetySignal:
        """Detect signals. `turn_text` is the latest utterance; cumulative is the
        full transcript (used for state that persists across turns)."""
        t = turn_text.lower()
        cum = cumulative_text.lower()
        sig = SafetySignal()
        vehicle_ctx = bool(_VEHICLE_CONTEXT.search(cum))

        # --- Medical breathing (from latest turn) ---
        if re.search(
            r"\b(?:can't breathe|cannot breathe|cant breathe|trouble breathing|struggling to breathe|"
            r"hard to breathe|not breathing|difficulty breathing|shortness of breath|choking)\b",
            t,
        ):
            sig.hazards.append("breathing")
            sig.escalation_signals.append("breathing")

        # --- Hazards (from the latest turn) ---
        if "smoke" in t or "smoking" in t:
            sig.hazards.append("smoke_from_vehicle" if vehicle_ctx else "smoke")
        if any(w in t for w in ("on fire", "flames", "fire from", "burning")) or (
            "fire" in t and "fire department" not in t
        ):
            sig.hazards.append("fire_from_vehicle" if vehicle_ctx else "visible_fire")
        if any(w in t for w in ("gas smell", "smell gas", "smell of gas", "gas leak", "smelled gas")):
            sig.hazards.append("gas_smell")
        if "explosion" in t or "exploded" in t or "blew up" in t:
            sig.hazards.append("explosion")
        if any(w in t for w in ("fuel", "gasoline", "smell fuel", "gas spilling")) and vehicle_ctx:
            sig.hazards.append("fuel_smell")
        if vehicle_ctx and any(w in t for w in ("child", "baby", "kid", "daughter", "son", "infant", "toddler")):
            sig.hazards.append("child_in_vehicle")

        # --- Injury (with negation) ---
        if any(n in cum for n in _NO_INJURY):
            sig.injury = False
        elif any(w in t for w in _INJURY_TERMS):
            sig.injury = True
            sig.hazards.append("injury")

        # --- Weapons / threat ---
        if any(w in t for w in _WEAPON_THREAT):
            sig.weapon_or_threat = True

        # --- Third party inside (someone other than the caller) ---
        # Detected from the LATEST turn so activation tracks NEW information.
        # Persistence ("sticky until resolved") is handled by the kernel, which
        # does not re-detect from cumulative text and so won't auto-reopen a
        # branch that was (wrongly) closed — that's what the eval must catch.
        has_person, person_hits = _contains_non_self_person(t)
        inside_signal = any(term in t for term in _INSIDE_TERMS)
        if "trapped" in t or "stuck inside" in t:
            sig.third_party_detected = True
        if has_person and inside_signal:
            sig.third_party_detected = True
            sig.third_party_terms = person_hits

        # --- Caller evacuation / safety ---
        # Detected from the LATEST turn only: a caller saying "I'm out" this turn
        # is what may close a branch. A safety fact surfaced on a LATER turn (or
        # by a background speaker) must not be retroactively closed.
        sig.caller_evacuated = any(p in t for p in _EVAC_TERMS)
        sig.caller_safe_statement = any(p in t for p in _SAFE_STATEMENTS)
        # Explicit full resolution only counts if NO one is reported inside.
        sig.everyone_out = (not sig.third_party_detected) and any(p in t for p in _EVERYONE_OUT)

        # --- Reentry intent ---
        sig.reentry_intent = any(p in t for p in _REENTRY_TERMS)

        # --- Escalation signals (raw; kernel maps to policy) ---
        sig.escalation_signals = list(dict.fromkeys(sig.hazards))
        if sig.third_party_detected:
            sig.escalation_signals.append("third_party_risk_active")
        if sig.weapon_or_threat:
            sig.escalation_signals.append("weapon_or_threat")
        return sig
