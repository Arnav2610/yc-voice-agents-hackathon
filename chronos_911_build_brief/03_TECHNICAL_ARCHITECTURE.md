# Technical Architecture

## System overview

```text
Caller or Cekura simulated caller
  -> Twilio or SmallWebRTC
  -> Pipecat transport
  -> NVIDIA Nemotron ASR Streaming or fallback STT
  -> Chronos Interaction Kernel
  -> Supermemory retrieval and writes
  -> Nemotron 3 Super or fallback LLM
  -> Policy-guided response / copilot guidance
  -> Gradium TTS
  -> caller or demo UI

After call:
  Cekura eval result
  -> failure classifier
  -> similar failure retrieval from Supermemory
  -> patch generator
  -> policy update candidate
  -> regression rerun
  -> accepted patch + failure memory write
```

## Key design decision

Do not make one LLM call responsible for everything.

Split into specialized submodules:

1. **PartialTranscriptBuffer**: stores streaming partial transcripts and final turns.
2. **IncidentHypothesisTracker**: classifies likely incident type and confidence.
3. **SafetySentinel**: detects critical hazards and missing safety facts.
4. **MemoryRetrievalPlanner**: decides what memory to fetch and when.
5. **SOPChecklistGenerator**: turns SOP policy into active checklist.
6. **FloorController**: decides whether to speak, wait, interrupt, or backchannel.
7. **SpeculativeToolRunner**: prefetches reversible reads, never irreversible writes.
8. **CommitRollbackVerifier**: discards speculative branches if facts change.
9. **CekuraAdapter**: triggers runs, parses reports, stores eval summaries.
10. **ImprovementLoop**: classifies failures, patches policies, reruns regression.

## Runtime flow

### On partial transcript

```python
def on_partial_transcript(text: str, confidence: float, timestamp_ms: int):
    event_bus.emit(PartialTranscriptEvent(text, confidence, timestamp_ms))
    state.partial_buffer.append(text)

    incident_update = incident_tracker.update(text, state)
    safety_update = safety_sentinel.detect(text, state)

    retrieval_plan = memory_retrieval_planner.plan(
        partial_text=text,
        state=state,
        incident_update=incident_update,
        safety_update=safety_update,
    )
    memory_results = memory_client.search_many(retrieval_plan.queries)
    state.memory_context.update(memory_results)

    floor_action = floor_controller.decide(state, safety_update, incident_update)
    if floor_action.kind in ["interrupt", "backchannel"]:
        speaker.enqueue(floor_action.message)
```

### On final user turn

```python
def on_final_user_turn(text: str, timestamp_ms: int):
    state.turns.append(text)
    state.partial_buffer.clear()

    incident_tracker.commit_final_turn(text, state)
    sop_engine.update_checklist(state)
    speculative_tools.rollback_invalid_branches(state)

    llm_context = build_llm_context(state)
    response = nemotron_or_fallback.generate(llm_context)

    event_bus.emit(AgentGuidanceEvent(response))
    speaker.say(response.voice_message)
```

### After call

```python
def on_call_complete(call_summary, event_trace):
    memory_writer.ingest_call_summary(call_summary, event_trace)
    dashboard.persist_trace(event_trace)
```

### After Cekura eval

```python
def on_cekura_report(report):
    failures = failure_classifier.classify(report)
    for failure in failures:
        similar = memory_client.search_failure_memories(failure)
        patch = patch_generator.propose(failure, similar, current_policies)
        result = regression_runner.test_patch(patch)
        if result.accept:
            policy_store.apply(patch)
            memory_writer.write_failure_memory(failure, patch, result)
```

## Data flow objects

### Event

Create a unified event schema so the dashboard, evaluator, and improvement loop can all read the same trace.

```python
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass
class ChronosEvent:
    timestamp_ms: int
    event_type: str
    data: dict[str, Any]
    call_id: str
    scenario_id: str | None = None
```

Example event types:

```text
partial_transcript
final_transcript
incident_hypothesis
safety_signal
memory_query
memory_result
sop_checklist_update
floor_action
agent_guidance
tool_prefetch
tool_commit
tool_rollback
policy_violation_warning
cekura_failure
policy_patch_candidate
policy_patch_accepted
policy_patch_rejected
```

### Incident state

```python
@dataclass
class IncidentState:
    incident_type: str | None = None
    risk_level: str = "unknown"
    location_raw: str | None = None
    location_confidence: float = 0.0
    location_needs_confirmation: bool = True
    caller_safety: str = "unknown"
    third_party_risk: str = "unknown"
    hazards: list[str] = field(default_factory=list)
    required_slots: set[str] = field(default_factory=set)
    resolved_slots: set[str] = field(default_factory=set)
    escalation_required: bool = False
    escalation_reason: str | None = None
```

### Memory result

```python
@dataclass
class MemoryResult:
    id: str
    content: str
    score: float
    memory_type: str
    container_tags: list[str]
    metadata: dict[str, Any]
```

### Floor action

```python
@dataclass
class FloorAction:
    kind: Literal["speak", "wait", "interrupt", "backchannel", "handoff", "none"]
    message: str | None
    reason: str
    confidence: float
```

## Pipecat integration points

The exact Pipecat processor names may differ in the starter repo. The coding agent should inspect `bot-gpt.py`, `bot-nemotron.py`, and the Pipecat docs before modifying.

Target idea:

```python
pipeline = Pipeline([
    transport.input(),
    stt,
    chronos_partial_observer,
    context_aggregator.user(),
    chronos_memory_processor,
    llm,
    chronos_response_observer,
    tts,
    transport.output(),
    context_aggregator.assistant(),
])
```

If the Pipecat STT service exposes interim transcripts, wire those into `chronos_partial_observer`. If not, process finalized turns first and simulate partial traces for dashboard demo.

## Supermemory architecture

Use three operations:

1. Add seed documents and memories.
2. Search relevant memories during calls.
3. Write call summaries, failure memories, and patch rationales after calls/evals.

### Container tags

Use scoped tags. Do not dump all memories into one global namespace.

```text
agency:demo_psap
call:{call_id}
scenario:{scenario_id}
incident:structure_fire
incident:vehicle_crash
incident:noise_escalation
location:5th_pine
caller:synthetic_001
memory_type:sop
memory_type:prior_call
memory_type:eval_failure
memory_type:patch
```

### Seed content

Seed Supermemory with:

1. SOP documents.
2. Prior incident summaries.
3. Location aliases.
4. Known previous failure memories.
5. Evaluation criteria.

### Retrieval planner rules

```yaml
on_incident_hypothesis:
  structure_fire:
    queries:
      - "SOP checklist structure fire trapped persons evacuation smoke exact location"
      - "previous eval failures structure fire third party trapped person"

on_location_mentioned:
  queries:
    - "recent incidents near {location_raw}"
    - "location aliases and landmarks for {location_raw}"

on_hazard_detected:
  gas_smell:
    queries:
      - "prior gas smell calls near {location_raw}"
      - "SOP gas leak fire escalation"

on_failure_patch:
  queries:
    - "similar failures {failure_type} {incident_type}"
```

## Policy files

### `policies/structure_fire.yaml`

Controls SOP state and required questions.

### `policies/interaction_policy.yaml`

Controls wait, interrupt, and backchannel behavior.

### `policies/memory_retrieval_policy.yaml`

Controls what memory to retrieve and when.

### `policies/improvement_policy.yaml`

Controls patch acceptance and regression rules.

## Mock tools

Implement all tools as local functions. Keep deterministic behavior.

### `lookup_prior_incidents(location)`

Returns mock incident history.

### `resolve_location(raw_location)`

Returns canonical location with confidence and aliases.

### `create_mock_cad_event(incident_state)`

Returns a fake CAD event ID. Never dispatches anything real.

### `send_mock_sms(phone, summary)`

Logs a fake SMS. Does not send a real message unless explicitly configured for demo.

### `escalate_to_human(reason)`

Logs handoff recommendation. Does not call actual emergency services.

## LLM prompting architecture

Use the LLM for:

1. Summarizing partial state.
2. Generating concise copilot guidance.
3. Classifying failed Cekura calls.
4. Proposing policy patches.

Do not use the LLM as the sole source of truth for:

1. Whether a high-risk case escalates.
2. Which required SOP questions exist.
3. Whether a patch is accepted.
4. Whether to claim a call is safe.

Those decisions must go through policy checks.

## Dashboard architecture

Build the dashboard as a simple local static page or FastAPI route.

Recommended:

```text
FastAPI server
  /events/{call_id} returns event trace JSON
  /metrics returns latest eval before/after
  /policy returns current policy snapshot
  /memory returns latest retrieved memories
```

Dashboard panels:

1. Transcript timeline.
2. Incident state.
3. Memory results.
4. SOP checklist.
5. Floor actions.
6. Self-improvement before/after.
7. Patch diff.

## Deployment strategy

### Local first

```bash
cd server
uv sync
uv run bot-chronos.py
```

Open local WebRTC page from starter, usually `http://localhost:7860`.

### Cloud optional

```bash
pc cloud auth login
pc cloud secrets set chronos-secrets --file .env
pc cloud deploy
```

### Twilio optional

Use the TwiML structure from the hackathon README, replacing the service host with the Pipecat Cloud service host.

## Environment variables

```bash
OPENAI_API_KEY=
GRADIUM_API_KEY=
SUPERMEMORY_API_KEY=
CEKURA_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
NVIDIA_ASR_URL=ws://44.241.251.184:8080
NEMOTRON_LLM_URL=http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1
NEMOTRON_LLM_MODEL=nvidia/nemotron-3-super
CHRONOS_MODE=demo
CHRONOS_USE_SUPERMEMORY=true
CHRONOS_REQUIRE_HUMAN_ESCALATION=true
```

Use fallbacks if NVIDIA endpoints are unavailable.
