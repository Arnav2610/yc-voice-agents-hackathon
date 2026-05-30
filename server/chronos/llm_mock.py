"""Deterministic LLM extraction mock for offline regression (tests only).

Production code never imports this except via CHRONOS_MOCK_LLM or test overrides.
Uses minimal transcript signals to return structured JSON — NOT used on live voice path.
"""

from __future__ import annotations

import re

from typing import Any


def mock_extract_call_state(transcript: str, *, partial: bool = True) -> dict[str, Any]:
    t = transcript.lower()
    out: dict[str, Any] = {
        "incident_confidence": 0.85,
        "location_raw": None,
        "location_certain": False,
        "caller_safety": "unknown",
        "third_party_at_risk": False,
        "third_party_resolved": False,
        "everyone_accounted_for": False,
        "hazards": [],
        "risk_level": "unknown",
        "escalation_required": False,
        "escalation_reason": None,
        "incident_upgraded_to": None,
        "correction_detected": any(x in t for x in ("no wait", "actually", "sorry", "i mean", "scratch that")),
        "reentry_intent": any(x in t for x in ("go back inside", "go back in", "re-enter", "head back in")),
        "resolved_slots": [],
        "structured_notes": [],
    }

    # --- incident type (ordered priority) ---
    if any(x in t for x in ("rob", "break in", "break-in", "breaking in", "banging on", "banging", "intruder", "knife")):
        out["incident_type"] = "active_threat"
        out["risk_level"] = "critical"
        out["escalation_required"] = True
        out["caller_safety"] = "at_risk"
        if "knife" in t or "gun" in t:
            out["hazards"].append("weapon")
        if any(x in t for x in ("banging", "break", "intruder", "rob")):
            out["structured_notes"].append({"category": "threat", "field": "threat_type", "value": "break-in at door"})
            out["resolved_slots"].extend(["threat_description", "suspect_location"])
        if "knife" in t:
            out["structured_notes"].append({"category": "threat", "field": "weapon_type", "value": "knife"})
            if "weapon_info" not in out["resolved_slots"]:
                out["resolved_slots"].append("weapon_info")
    if out.get("incident_type") is None and any(x in t for x in ("smoke", " fire", "fire ", "flames", "burning", "gas smell", "smell gas")):
        if "vehicle" not in t and "highway" not in t and "101 " not in t and "crashed" not in t:
            out["incident_type"] = "structure_fire"
    if out.get("incident_type") is None and any(
        x in t for x in ("neighbor", "still inside", "building", "apartment")
    ) and any(x in t for x in ("got out", "evacuated", "made it outside", "i'm out", "we're out", "smoke")):
        out["incident_type"] = "structure_fire"
    if out.get("incident_type") is None and any(
        x in t
        for x in (
            "can't breathe", "cannot breathe", "trouble breathing", "struggling to breathe",
            "chest pain", "choking", "not breathing", "bent my tongue", "bleeding",
            "tongue", "spicy", "got bit", "bit my tongue",
        )
    ):
        out["incident_type"] = "medical"
    if out.get("incident_type") is None and (
        "crashed" in t or ("101 " in t and "exit" in t) or ("highway" in t and "crash" in t)
    ):
        out["incident_type"] = "vehicle_crash"
    if out.get("incident_type") is None and any(x in t for x in ("loud music", "noise complaint", "parked in", "driveway", "parking")):
        out["incident_type"] = "non_emergency_noise"
    if out.get("incident_type") is None:
        out["incident_type"] = "unknown"

    if "fire" in t and "breathe" in t:
        out["incident_type"] = "structure_fire"

    # noise upgrade
    if any(x in t for x in ("screaming", "glass breaking", "fighting", "gun", "weapon", "threat")):
        out["incident_upgraded_to"] = "active_threat" if "gun" in t or "weapon" in t else "possible_active_disturbance"
        out["incident_type"] = out["incident_upgraded_to"]

    from chronos.incident_signals import medical_without_threat

    if re.search(r"\b(?:fire|smoke|flames|burning)\b", t):
        if "vehicle" not in t and "highway" not in t and "101 " not in t and "crashed" not in t:
            out["incident_type"] = "structure_fire"
    elif medical_without_threat(transcript):
        out["incident_type"] = "medical"
        out["incident_upgraded_to"] = None

    # --- location ---
    if "5th and pine" in t or "fifth and pine" in t:
        out["location_raw"] = "near 5th and Pine"
        out["location_certain"] = "maybe" not in t and " or " not in t and "6th" not in t
    if "101 south" in t:
        out["location_raw"] = "101 south near exit 430" if "430" in t else ("101 south near exit 431" if "431" in t else "101 south")
        out["location_certain"] = "maybe" not in t and " or " not in t and "no wait" not in t and "sorry" not in t
    if "y combinator" in t:
        out["location_raw"] = "Y Combinator office"
        out["location_certain"] = True
    if "market street" in t or "1412" in t or "fourteen twelve" in t:
        out["location_raw"] = "1412 Market Street"
        out["location_certain"] = True
        out["structured_notes"].append({"category": "location", "field": "address", "value": out["location_raw"]})
        if not partial:
            out["resolved_slots"].append("exact_location")
        elif "market street" in t or "1412" in t or "fourteen twelve" in t:
            out["resolved_slots"].append("exact_location")

    # --- hazards ---
    if "smoke" in t:
        out["hazards"].append("smoke_from_vehicle" if out["incident_type"] == "vehicle_crash" else "smoke")
    if "fire" in t:
        out["hazards"].append("fire_from_vehicle" if out["incident_type"] == "vehicle_crash" else "visible_fire")
    if "gas smell" in t or "smell gas" in t:
        out["hazards"].append("gas_smell")
    if "child" in t or "baby" in t:
        out["hazards"].append("child_in_vehicle" if out["incident_type"] == "vehicle_crash" else "child")
    if "injured" in t or "hurt" in t or "bleeding" in t:
        out["hazards"].append("injury")
    if "breathe" in t or "breathing" in t:
        out["hazards"].append("breathing")
    if "weapon" in t or "gun" in t:
        out["hazards"].append("weapon")
        if "gun" in t:
            out["structured_notes"].append({"category": "threat", "field": "weapon_type", "value": "gun"})
    out["hazards"] = list(dict.fromkeys(out["hazards"]))

    # --- third party / caller safety ---
    if any(x in t for x in (
        "neighbor", "still inside", "may still be inside", "someone inside", "trapped",
        "child is still", "might still be", "still be in", "don't see my daughter",
        "who's still inside", "who is still inside",
    )):
        out["third_party_at_risk"] = True
    if any(x in t for x in ("got out", "i'm out", "we're out", "made it out", "outside")):
        out["caller_safety"] = "evacuated"
        # Baseline bug: caller evacuation alone must NOT resolve third-party branch.
        if not partial and not out["third_party_at_risk"]:
            pass
    if any(x in t for x in ("i'm safe", "we are okay", "we're okay", "safe for now")):
        out["caller_safety"] = "safe"
    if any(x in t for x in ("everyone is out", "no one inside", "nobody inside")):
        out["everyone_accounted_for"] = True
        if not partial:
            out["third_party_resolved"] = True

    # --- risk / escalation ---
    if out["incident_type"] == "non_emergency_noise":
        out["risk_level"] = "low"
        out["escalation_required"] = False
        out["escalation_reason"] = None
    elif out["incident_type"] in ("structure_fire", "medical") or out["hazards"]:
        out["risk_level"] = "critical" if out["third_party_at_risk"] or "visible_fire" in out["hazards"] or "breathing" in out["hazards"] else "high"
        out["escalation_required"] = True
        out["escalation_reason"] = out["escalation_reason"] or f"{out['incident_type']} — high risk"
    if out["incident_type"] == "vehicle_crash" and ("smoke" in out["hazards"] or "child" in str(out["hazards"])):
        out["escalation_required"] = True
        out["risk_level"] = "critical"

    # resolved slots (final only)
    if not partial:
        if out["location_raw"] and out["location_certain"]:
            out["resolved_slots"].append("exact_location")
        if out["caller_safety"] in ("evacuated", "safe"):
            out["resolved_slots"].append("caller_safety")
        if out.get("incident_type") == "active_threat":
            for slot in ("threat_description", "suspect_location"):
                if slot not in out["resolved_slots"]:
                    out["resolved_slots"].append(slot)
        if "rnf" in t or "kumar" in t:
            out["structured_notes"].append({"category": "contact", "field": "caller_name", "value": "RNF Kumar"})
        from chronos.partial_hints import _extract_phone

        phone = _extract_phone(transcript)
        if phone:
            out["structured_notes"].append({"category": "contact", "field": "callback_number", "value": phone})
            if "callback_number" not in out["resolved_slots"]:
                out["resolved_slots"].append("callback_number")
        elif sum(c.isdigit() for c in t) >= 10:
            digits = re.sub(r"\D", "", t)
            if len(digits) >= 10:
                out["structured_notes"].append(
                    {"category": "contact", "field": "callback_number", "value": digits[-10:]}
                )
                if "callback_number" not in out["resolved_slots"]:
                    out["resolved_slots"].append("callback_number")

    out["resolved_slots"] = list(dict.fromkeys(out["resolved_slots"]))

    slot_values: dict[str, str] = {}
    if out.get("location_raw"):
        slot_values["exact_location"] = out["location_raw"]
    if out.get("caller_safety") == "safe":
        slot_values["caller_safety"] = "Caller reports safe for now"
    elif out.get("caller_safety") == "at_risk":
        slot_values["caller_safety"] = "Caller still at risk / imminent danger"
    elif out.get("caller_safety") == "evacuated":
        slot_values["caller_safety"] = "Caller evacuated / outside"
    for note in out["structured_notes"]:
        cat, fld, val = note.get("category"), note.get("field"), note.get("value")
        if not val:
            continue
        if fld in ("threat_type", "threat_description"):
            slot_values["threat_description"] = val
        elif fld in ("suspect_location", "intruder_location"):
            slot_values["suspect_location"] = val
        elif fld in ("weapon_type", "weapon"):
            slot_values["weapon_info"] = val
        elif fld == "callback_number":
            slot_values["callback_number"] = val
        elif fld == "caller_name" and slot_values.get("callback_number"):
            slot_values["callback_number"] = f"{val} · {slot_values['callback_number']}"
        elif cat == "medical" and fld == "injury":
            slot_values["injury_status"] = val
    if "rob" in t or "break" in t or "intruder" in t or "banging" in t:
        slot_values.setdefault(
            "threat_description",
            "Someone banging / trying to break in; robbery threatened",
        )
        slot_values.setdefault("suspect_location", "At caller's door")
    if "knife" in t or "gun" in t:
        slot_values["weapon_info"] = "knife" if "knife" in t else "gun"
    if "bleeding" in t and "tongue" in t:
        slot_values["injury_status"] = "Tongue injury — bleeding after spicy food"
    out["slot_values"] = slot_values
    return out
