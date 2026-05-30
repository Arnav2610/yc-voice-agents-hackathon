"""Floor controller — when to speak, wait, interrupt, or backchannel.

Policy-driven (policies/interaction_policy.yaml). The two behaviors the eval
checks: suppress interruption while the caller is correcting the location, and
backchannel (don't leave dead air) during a slow memory/tool lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chronos import config
from chronos.incident_tracker import IncidentUpdate
from chronos.safety_sentinel import SafetySignal
from chronos.state import CallState

FloorKind = Literal["speak", "wait", "interrupt", "backchannel", "handoff", "none"]


@dataclass
class FloorAction:
    kind: FloorKind
    message: str | None
    reason: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
        }


class FloorController:
    def decide(
        self,
        state: CallState,
        signal: SafetySignal,
        incident: IncidentUpdate,
        slow_memory: bool = False,
    ) -> FloorAction:
        pol = config.load_policy("interaction_policy")
        interrupt_cfg = pol.get("interrupt", {})
        min_conf = float(interrupt_cfg.get("min_confidence", 0.72))

        # 1) Suppress interruption while the caller is correcting critical info.
        if incident.correction_detected:
            state.suppressed_interruption = True
            return FloorAction(
                kind="wait",
                message=None,
                reason="user_correcting_location — suppressing interruption",
                confidence=0.9,
            )

        # 2) Backchannel during a slow memory/tool lookup (no dead air).
        if slow_memory:
            state.backchannel_emitted = True
            msgs = (pol.get("backchannel", {}).get("messages") or ["One moment, I'm pulling up the relevant guidance."])
            return FloorAction(
                kind="backchannel",
                message=msgs[0],
                reason="memory_lookup_in_progress",
                confidence=0.8,
            )

        # 3) Active-danger barge-in. OFF by default: a call-taker shouldn't talk
        #    over a panicking caller, and the live voice path speaks at the turn
        #    boundary regardless. Enable with CHRONOS_ALLOW_BARGE_IN=true.
        danger = bool(signal.hazards) or signal.third_party_detected or signal.weapon_or_threat
        if config._flag("CHRONOS_ALLOW_BARGE_IN", False) and state.incident.escalation_required and danger:
            conf = max(min_conf, round(0.7 + 0.3 * state.incident.incident_confidence, 2))
            if conf >= min_conf:
                return FloorAction(
                    kind="interrupt",
                    message=state.recommended_question,
                    reason="active_danger_detected / human_escalation_required",
                    confidence=conf,
                )

        # 4) Default: take a normal speaking turn.
        return FloorAction(
            kind="speak",
            message=state.recommended_question,
            reason="normal_turn",
            confidence=0.6,
        )
