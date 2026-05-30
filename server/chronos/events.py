"""Unified Chronos event schema + in-memory event store.

Every subsystem (dashboard, evaluator, improvement loop) reads the same trace.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from chronos import config

# Canonical event types (documentation; emitting an unlisted type is allowed).
EVENT_TYPES = [
    "call_start",
    "partial_transcript",
    "final_transcript",
    "background_speech",
    "incident_hypothesis",
    "safety_signal",
    "memory_query",
    "memory_result",
    "sop_checklist_update",
    "floor_action",
    "agent_guidance",
    "tool_prefetch",
    "tool_commit",
    "tool_rollback",
    "policy_violation_warning",
    "escalation_recommended",
    "call_complete",
    "cekura_failure",
    "policy_patch_candidate",
    "policy_patch_accepted",
    "policy_patch_rejected",
]


@dataclass
class ChronosEvent:
    """A single timestamped event in a call (or eval) trace."""

    timestamp_ms: int
    event_type: str
    data: dict[str, Any]
    call_id: str
    scenario_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventStore:
    """Thread-safe in-memory store of events, grouped by call_id.

    Also tracks the most recent call_id so the dashboard can show "the live call"
    without being told which one. Optionally mirrors traces to disk so a
    separately-launched process (e.g. an eval script) can read them.
    """

    events_by_call: dict[str, list[ChronosEvent]] = field(default_factory=dict)
    latest_call_id: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append(self, event: ChronosEvent) -> ChronosEvent:
        with self._lock:
            self.events_by_call.setdefault(event.call_id, []).append(event)
            self.latest_call_id = event.call_id
        return event

    def emit(
        self,
        event_type: str,
        data: dict[str, Any],
        call_id: str,
        scenario_id: str | None = None,
    ) -> ChronosEvent:
        return self.append(
            ChronosEvent(
                timestamp_ms=config.now_ms(),
                event_type=event_type,
                data=data,
                call_id=call_id,
                scenario_id=scenario_id,
            )
        )

    def list(self, call_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self.events_by_call.get(call_id, [])]

    def list_latest(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.latest_call_id:
                return []
            return [e.to_dict() for e in self.events_by_call.get(self.latest_call_id, [])]

    def call_ids(self) -> list[str]:
        with self._lock:
            return list(self.events_by_call.keys())

    def clear(self) -> None:
        """Drop all stored events (used by demo reset for a clean slate)."""
        with self._lock:
            self.events_by_call.clear()
            self.latest_call_id = None

    def persist(self, call_id: str) -> None:
        """Best-effort: mirror a call's trace to runtime/events_<call_id>.json."""
        try:
            path = config.RUNTIME_DIR / f"events_{call_id}.json"
            with open(path, "w") as f:
                json.dump(self.list(call_id), f, indent=2)
        except Exception:
            pass


# Process-wide singleton shared by the bot and the in-process dashboard.
STORE = EventStore()
