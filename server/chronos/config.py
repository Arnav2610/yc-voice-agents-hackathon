"""Configuration, paths, and policy loading for Chronos 911."""

from __future__ import annotations

import os
import time
from functools import cache
from pathlib import Path
from typing import Any

import yaml

# server/ directory (parent of chronos/)
BASE_DIR = Path(__file__).resolve().parent.parent
POLICIES_DIR = BASE_DIR / "policies"
DATA_DIR = BASE_DIR / "data"
DASHBOARD_DIR = BASE_DIR / "dashboard"
RUNTIME_DIR = BASE_DIR / "chronos" / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _flag(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- Demo framing / behavior flags ------------------------------------------
AGENCY_TAG = os.getenv("CHRONOS_AGENCY_TAG", "agency:demo_psap")
CHRONOS_MODE = os.getenv("CHRONOS_MODE", "demo")
USE_SUPERMEMORY = _flag("CHRONOS_USE_SUPERMEMORY", True)
REQUIRE_HUMAN_ESCALATION = _flag("CHRONOS_REQUIRE_HUMAN_ESCALATION", True)
DASHBOARD_PORT = int(os.getenv("CHRONOS_DASHBOARD_PORT", "7861"))

SIMULATION_DISCLAIMER = (
    "This is a simulated emergency-call training and copilot system. It is not a "
    "real 911 service and does not dispatch responders. For a real emergency, call 911."
)

# Spoken disclaimer the bot says at the start of a simulated call.
SPOKEN_GREETING = (
    "This is 911, what's your emergency?"
)


def now_ms() -> int:
    """Wall-clock milliseconds (used for event timestamps)."""
    return int(time.time() * 1000)


@cache
def _load_yaml_cached(path_str: str, mtime: float) -> dict[str, Any]:
    with open(path_str) as f:
        return yaml.safe_load(f) or {}


def load_policy(name: str) -> dict[str, Any]:
    """Load a single policy YAML by file name (with or without .yaml).

    Cached on file mtime so a freshly patched policy is picked up immediately.
    """
    if not name.endswith(".yaml"):
        name = name + ".yaml"
    path = POLICIES_DIR / name
    if not path.exists():
        return {}
    return _load_yaml_cached(str(path), path.stat().st_mtime)


# Incident type -> policy file name.
INCIDENT_POLICY_FILES = {
    "structure_fire": "structure_fire.yaml",
    "vehicle_crash": "vehicle_crash.yaml",
    "non_emergency_noise": "non_emergency_noise.yaml",
    "possible_active_disturbance": "non_emergency_noise.yaml",
    "active_threat": "active_threat.yaml",
    "medical": "medical.yaml",
}


def load_all_policies() -> dict[str, dict[str, Any]]:
    """Load every incident/behavior policy keyed by file stem."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(POLICIES_DIR.glob("*.yaml")):
        out[path.stem] = load_policy(path.name)
    return out


def policy_for_incident(incident_type: str | None) -> dict[str, Any]:
    """Return the SOP policy dict for an incident type (empty if unknown)."""
    if not incident_type:
        return {}
    return load_policy(INCIDENT_POLICY_FILES.get(incident_type, ""))
