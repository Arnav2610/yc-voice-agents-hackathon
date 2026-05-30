# Implementation Plan for Coding Agent

This is the recommended end-to-end build sequence. Follow this order unless the starter repo structure forces changes.

## Phase 0: Setup and repo inspection

1. Clone the hackathon starter repo.

```bash
git clone https://github.com/pipecat-ai/yc-voice-agents-hackathon.git
cd yc-voice-agents-hackathon/server
```

2. Inspect these files:

```text
README.md
server/bot-gpt.py
server/bot-nemotron.py
server/pcc-deploy.toml
server/.env.example
```

3. Run the baseline bot locally.

```bash
cp .env.example .env
uv sync
uv run bot-nemotron.py
```

If Nemotron endpoint fails, run GPT fallback.

```bash
uv run bot-gpt.py
```

4. Confirm WebRTC works before adding Chronos.

## Phase 1: Add Chronos skeleton

Create:

```text
chronos/
  __init__.py
  config.py
  events.py
  state.py
  kernel.py
  incident_tracker.py
  safety_sentinel.py
  memory_retrieval.py
  sop_engine.py
  floor_controller.py
  improvement_loop.py
  cekura_adapter.py
```

### `events.py`

Implement:

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class ChronosEvent:
    timestamp_ms: int
    event_type: str
    data: dict[str, Any]
    call_id: str
    scenario_id: str | None = None
```

Add a simple in-memory event store:

```python
class EventStore:
    def __init__(self):
        self.events_by_call = {}

    def append(self, event: ChronosEvent):
        self.events_by_call.setdefault(event.call_id, []).append(event)

    def list(self, call_id: str):
        return self.events_by_call.get(call_id, [])
```

### `state.py`

Implement IncidentState, CallState, and MemoryContext dataclasses.

### `kernel.py`

Implement one orchestrator class:

```python
class ChronosKernel:
    def on_partial_transcript(self, text, confidence=1.0): ...
    def on_final_user_turn(self, text): ...
    def build_llm_context(self): ...
    def on_agent_response(self, text): ...
    def on_call_complete(self): ...
```

## Phase 2: Implement policies

Create `policies/structure_fire.yaml`:

```yaml
incident_type: structure_fire
risk_level_default: high
hazards:
  smoke:
    risk: high
    escalation_required: true
  gas_smell:
    risk: critical
    escalation_required: true
  trapped_person:
    risk: critical
    escalation_required: true
required_slots:
  - exact_location
  - caller_safety
  - visible_smoke_or_fire
  - trapped_person_status
  - callback_number
third_party_risk:
  active_if:
    - neighbor_inside
    - child_inside
    - person_trapped
    - someone_cannot_exit
  cannot_be_resolved_by:
    - caller_personally_evacuated
  required_questions:
    - "Is anyone still inside or unable to get out?"
    - "Where were they last seen?"
forbidden_guidance:
  - "Do not tell caller to re-enter building."
  - "Do not say the building is safe."
escalation:
  required_if_any:
    - smoke
    - gas_smell
    - trapped_person
    - location_uncertain_with_danger
```

Create `policies/vehicle_crash.yaml`:

```yaml
incident_type: vehicle_crash
risk_level_default: medium
hazards:
  smoke_from_vehicle:
    risk: critical
    escalation_required: true
  child_in_vehicle:
    risk: high
    escalation_required: true
  injury:
    risk: critical
    escalation_required: true
required_slots:
  - exact_location
  - direction_of_travel
  - caller_safety
  - injury_status
  - vehicle_location
forbidden_guidance:
  - "Do not give mechanical repair advice."
  - "Do not tell caller to open hood near smoke."
```

Create `policies/interaction_policy.yaml`:

```yaml
interrupt:
  allowed_if:
    - active_danger_detected
    - missed_critical_safety_branch
    - human_escalation_required
  suppress_if:
    - user_correcting_location
    - user_spelling_address
    - user_providing_callback_number
  min_confidence: 0.72
backchannel:
  after_silence_ms: 1100
  during_tool_wait_ms: 1800
  messages:
    - "I am checking the relevant guidance now."
    - "I am pulling up the checklist."
```

Create `policies/improvement_policy.yaml`:

```yaml
patch_acceptance:
  require_pass_rate_improvement: true
  max_new_critical_failures: 0
  max_wrong_interruption_increase: 1
  rerun_failed_scenario: true
  rerun_nearby_scenarios: 3
  rerun_previous_passing_scenarios: 3
patch_targets:
  - structure_fire.yaml
  - vehicle_crash.yaml
  - non_emergency_noise.yaml
  - interaction_policy.yaml
  - memory_retrieval_policy.yaml
```

## Phase 3: Incident tracker

Implement simple pattern and LLM hybrid logic.

### Fast pattern signals

```python
STRUCTURE_FIRE_TERMS = ["smoke", "fire", "burning", "gas smell", "building", "apartment"]
TRAPPED_TERMS = ["inside", "trapped", "cannot get out", "neighbor", "child", "still in there"]
VEHICLE_TERMS = ["crash", "accident", "car", "vehicle", "highway", "exit", "shoulder"]
CORRECTION_TERMS = ["actually", "no wait", "I mean", "sorry", "not", "correction"]
```

The tracker should update state and emit events.

## Phase 4: Safety sentinel

Implement deterministic high-risk detection first.

```python
if "smoke" in text and ("building" in context or "apartment" in context):
    mark_hazard("smoke")
    require_escalation("structure fire smoke")

if "neighbor" in text and "inside" in text:
    mark_third_party_risk("possible person inside")

if "gas" in text and "smell" in text:
    mark_hazard("gas_smell")
    require_escalation("possible gas leak")
```

Output should include:

```json
{
  "hazard": "gas_smell",
  "risk_level": "critical",
  "required_action": "escalate_human",
  "recommended_question": "Is everyone outside and away from the smell?"
}
```

## Phase 5: Supermemory integration

Install:

```bash
pip install supermemory
pip install supermemory-pipecat
```

Implement `memory_retrieval.py` with fallback in-memory mode if API key is missing.

```python
class ChronosMemoryClient:
    def __init__(self, api_key=None): ...
    def seed_demo_memory(self): ...
    def search(self, query, container_tags=None, limit=5): ...
    def write_call_summary(self, call_summary, event_trace): ...
    def write_failure_memory(self, failure, patch, regression_result): ...
```

Use Supermemory SDK if available:

```python
from supermemory import Supermemory
client = Supermemory(api_key=os.environ.get("SUPERMEMORY_API_KEY"))
client.add(content="...", container_tags=["agency:demo_psap", "memory_type:sop"])
response = client.search.documents(q="structure fire SOP trapped person", container_tags=["agency:demo_psap"])
```

Also support direct REST for conversation ingest if SDK shape differs:

```http
POST https://api.supermemory.ai/v3/conversations
Authorization: Bearer $SUPERMEMORY_API_KEY
Content-Type: application/json
```

Check current docs while implementing because endpoint naming may change.

## Phase 6: Seed memory

Create `data/seed_sops.md` with:

```markdown
# SOP: Structure Fire
Required checks:
1. Confirm exact location.
2. Ask whether caller is outside and away from smoke.
3. Ask if anyone is inside or unable to evacuate.
4. Ask visible smoke, fire, gas smell, or explosion.
5. Escalate immediately if trapped person, gas smell, active fire, or uncertain location with danger.
Never tell caller to re-enter.
Never say the scene is safe.

# SOP: Vehicle Crash
Required checks:
1. Confirm exact location and direction of travel.
2. Ask if caller is safely off the road.
3. Ask if anyone is injured.
4. Ask if there is smoke, fire, fuel smell, child, or trapped person.
5. Escalate if injury, smoke, fire, child in danger, or blocked roadway.
Never give mechanical repair advice.
```

Create `data/seed_prior_calls.json`:

```json
[
  {
    "id": "prior_001",
    "location_tag": "location:5th_pine",
    "incident_type": "gas_smell",
    "summary": "Caller reported gas smell near 5th and Pine yesterday. No visible fire reported.",
    "risk_level": "medium"
  },
  {
    "id": "prior_002",
    "location_tag": "location:101_exit_430",
    "incident_type": "vehicle_hazard",
    "summary": "Multiple shoulder incidents reported near US-101 south exit 430 during evening traffic.",
    "risk_level": "medium"
  }
]
```

Create `data/seed_failure_memories.json`:

```json
[
  {
    "failure_type": "WRONG_BRANCH_CLOSURE",
    "incident_type": "structure_fire",
    "summary": "The system previously closed third-party trapped-person risk after the caller personally evacuated. This is unsafe. Caller safety and third-party safety must be tracked separately."
  }
]
```

## Phase 7: LLM context builder

Build a structured LLM prompt like:

```text
You are Chronos 911, a simulated emergency call-taker copilot for training. You do not replace a dispatcher. You provide concise next-step guidance.

Current incident state:
{incident_state_json}

Relevant memories:
{memory_results}

Active SOP checklist:
{sop_checklist}

Safety constraints:
- Never tell caller to re-enter a dangerous scene.
- Never promise emergency response or ETA.
- Escalate all high-risk cases to a human dispatcher.
- Keep third-party risk active until explicitly resolved.

Return JSON:
{
  "guidance_for_call_taker": "...",
  "voice_message": "...",
  "missing_slots": [...],
  "escalation_required": true/false,
  "reasoning_summary": "short"
}
```

## Phase 8: Dashboard

Implement a minimal FastAPI route if starter already runs FastAPI.

```python
@app.get("/chronos/events/{call_id}")
def get_events(call_id: str):
    return event_store.list(call_id)

@app.get("/chronos/latest")
def get_latest():
    return latest_call_state
```

Frontend can poll every 500ms.

Dashboard must show:

1. Transcript.
2. Incident state.
3. Memory hits.
4. SOP checklist.
5. Floor actions.
6. Eval before/after.
7. Patch diff.

## Phase 9: Cekura scenarios

Create `data/cekura_scenarios.yaml` with at least 10 scenarios. See `05_EVALS_AND_SELF_IMPROVEMENT.md`.

Use Cekura MCP if available:

```text
/cekura-report
```

Or API:

```bash
curl --request POST \
  --url https://api.cekura.ai/test_framework/v1/scenarios/run_scenarios_pipecat_v2/ \
  --header 'Content-Type: application/json' \
  --header "X-CEKURA-API-KEY: $CEKURA_API_KEY" \
  --data '{"scenarios": [{"scenario": 123}], "name": "chronos-regression", "frequency": 1}'
```

## Phase 10: Self-improvement loop

Build a local simulation first, even before Cekura API is wired.

Input:

```json
{
  "scenario_id": "structure_fire_neighbor_inside_001",
  "passed": false,
  "failed_assertions": [
    "missed_trapped_person_question",
    "closed_third_party_risk_after_caller_evacuated"
  ],
  "transcript": "...",
  "event_trace": ["..."]
}
```

Output patch:

```yaml
third_party_risk:
  cannot_be_resolved_by:
    - caller_personally_evacuated
  required_until_resolved:
    - ask_if_anyone_inside
    - ask_last_known_location
    - escalate_human
```

Use LLM to propose patch, but apply only safe structured operations:

- Add required slot.
- Add trigger phrase.
- Raise risk level.
- Set escalation required.
- Add cannot_be_resolved_by guard.
- Adjust threshold within allowed range.

Do not allow arbitrary code patching from the LLM.

## Phase 11: Final demo polish

Create scripts:

```text
scripts/seed_memory.py
scripts/run_local_demo.py
scripts/run_fake_cekura_report.py
scripts/apply_demo_patch.py
scripts/show_before_after.py
```

Create a single command demo path:

```bash
make demo
```

Expected output:

```text
1. Seeds memory
2. Starts local bot
3. Starts dashboard
4. Loads baseline eval metrics
5. Waits for call
6. Writes failure memory
7. Applies policy patch
8. Shows after metrics
```

## Fallback plan if APIs fail

The hackathon environment can be flaky. Build fallbacks.

| Dependency | Fallback |
|---|---|
| NVIDIA ASR unavailable | Gradium STT or starter GPT bot path |
| Nemotron LLM unavailable | OpenAI GPT-4.1 from starter |
| Supermemory unavailable | Local JSON memory store with same interface |
| Cekura unavailable | Use seeded fake Cekura report and clearly say live Cekura integration is ready |
| Twilio unavailable | Use SmallWebRTC local browser demo |
| Pipecat Cloud unavailable | Local WebRTC only |

The final demo should still work locally without external failures.
