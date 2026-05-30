# Chronos 911 Build Packet: Start Here

This folder is the complete context packet for a coding agent to build the hackathon project end to end.

## Project name

**Chronos 911**

## One line

A simulated 911 call-taker copilot that remembers prior incidents, SOPs, evaluator failures, and human corrections, then self-improves its real-time guidance, interruption, escalation, and memory retrieval policies after Cekura test runs.

## What to build

Build a voice AI demo using the hackathon stack:

- Pipecat for real-time voice orchestration
- Twilio for phone calls
- NVIDIA Nemotron ASR Streaming for speech-to-text when available from the hackathon
- NVIDIA Nemotron 3 Super for reasoning when available from the hackathon
- Gradium for text-to-speech
- Supermemory for persistent institutional memory
- Cekura for scenario simulation, evaluation, and self-improvement loop

The project must be framed as a **simulated emergency-call training and copilot system**, not a real autonomous emergency dispatcher. The system must not connect to actual 911 or make real emergency dispatch decisions.

## Core hackathon thesis

Most teams will build a self-improving voice agent that patches its prompt after failed Cekura evals.

Chronos should self-improve at a deeper layer:

1. **Memory retrieval policy**: what prior calls, SOPs, location facts, and failure memories to retrieve.
2. **Interaction policy**: when to speak, stay silent, interrupt, backchannel, or ask clarification.
3. **Escalation policy**: when a human dispatcher must take over.
4. **SOP state machine**: which required questions and safety branches remain unresolved.
5. **Failure memory**: what mistake the system made, how it was fixed, and what future cases should retrieve that lesson.

## Winning demo in 90 seconds

1. A teammate calls the Twilio number and acts as a simulated 911 caller.
2. Caller says: “There’s smoke in my apartment building. I’m on the third floor. I think my neighbor is still inside. Wait, I don’t know the address, I’m near 5th and Pine, I called yesterday about a gas smell but no one came.”
3. Chronos transcribes live and surfaces:
   - Incident type: structure fire or gas leak
   - Risk: critical
   - Prior memory: gas smell at same location yesterday
   - Required SOP questions: caller safety, trapped persons, exact location, visible smoke/fire
   - Next recommended question: “Are you outside the building and away from the smoke?”
4. Cekura baseline shows the agent previously failed scenarios where the caller had evacuated but a third party might still be inside.
5. Chronos writes the failure into Supermemory, patches the SOP state machine, reruns regression scenarios, and shows score improvement.

## Demo must show these panels

### Panel 1: Live call trace

```text
Caller: "There is smoke in my building..."
Incident hypothesis: structure_fire
Risk level: critical
Missing required questions: trapped_person, exact_location, caller_safety
Recommended next prompt: Ask if the caller is outside and away from smoke.
```

### Panel 2: Memory retrieval

```text
Retrieved memory:
- Yesterday: gas smell report near 5th and Pine.
- SOP: structure fire requires trapped-person check.
- Prior eval failure: do not close third-party risk when caller evacuates.
```

### Panel 3: Self-improvement

```text
Before:
- Missed trapped-person question: 4 / 10
- Wrongly closed safety branch: 3 / 10
- Retrieved prior gas-smell memory: 5 / 10

Patch:
- third_party_risk remains active until explicitly resolved
- gas leak history increases escalation priority
- caller evacuation does not resolve neighbor-inside risk

After:
- Missed trapped-person question: 0 / 10
- Wrongly closed safety branch: 0 / 10
- Retrieved prior gas-smell memory: 9 / 10
```

## Non-negotiable safety framing

- The product is a **training and copilot demo**.
- The product must not claim to replace 911 call-takers.
- The product must not connect to an actual emergency number.
- The product must include UI and voice disclaimers that calls are simulated.
- The product must recommend human escalation for true emergencies, injury, fire, active violence, medical crisis, trapped persons, and uncertain high-risk cases.

## Repository strategy

Use the hackathon starter repo as the base:

https://github.com/pipecat-ai/yc-voice-agents-hackathon

The README says the starter contains Pipecat versions using GPT-4.1 and Nemotron, with Gradium STT/TTS, NVIDIA ASR/LLM endpoints during the hackathon, SmallWebRTC for local dev, Twilio for production telephony, Pipecat Cloud deployment, and Cekura testing via MCP and `/cekura-report`.

## Recommended file structure to create

```text
server/
  bot-chronos.py
  chronos/
    __init__.py
    config.py
    events.py
    state.py
    kernel.py
    partial_observer.py
    incident_tracker.py
    safety_sentinel.py
    memory_retrieval.py
    sop_engine.py
    floor_controller.py
    speculative_tools.py
    improvement_loop.py
    cekura_adapter.py
    dashboard_server.py
  tools/
    mock_cad.py
    mock_sms.py
    mock_location.py
    mock_policy.py
  policies/
    structure_fire.yaml
    vehicle_crash.yaml
    non_emergency_noise.yaml
    interaction_policy.yaml
    memory_retrieval_policy.yaml
    improvement_policy.yaml
  data/
    seed_sops.md
    seed_prior_calls.json
    seed_location_memory.json
    cekura_scenarios.yaml
    eval_assertions.yaml
  dashboard/
    index.html
    app.js
    styles.css
```

## Build priority

### Must have

1. Working local Pipecat voice agent.
2. Simulated 911 call-taker copilot behavior.
3. Supermemory ingestion and retrieval for SOPs, prior calls, failures, and eval memories.
4. Chronos event trace with timestamps.
5. Policy-driven incident state machine.
6. Cekura scenario suite.
7. Failure classification.
8. Policy patching and regression rerun flow.
9. Simple web dashboard showing trace, memory, and before/after improvement.

### Nice to have

1. Twilio phone number connected.
2. Pipecat Cloud deployment.
3. Live Cekura run triggered from Claude Code MCP.
4. Real-time barge-in behavior.
5. Audio/noise stress tests.

### Skip

1. Real CAD integration.
2. Real PSAP integration.
3. Real emergency dispatch.
4. Medical diagnosis.
5. Police decision automation.
6. Long fine-tuning jobs.

## Key source links

- Hackathon starter: https://github.com/pipecat-ai/yc-voice-agents-hackathon
- Pipecat docs: https://docs.pipecat.ai/
- Pipecat Twilio WebSockets: https://docs.pipecat.ai/pipecat/telephony/twilio-websockets
- Pipecat Cloud Twilio transport: https://docs.pipecat.ai/pipecat-cloud/guides/telephony/twilio-websocket
- Cekura docs: https://docs.cekura.ai/
- Cekura Pipecat automated testing: https://docs.cekura.ai/documentation/integrations/pipecat/automated
- Cekura MCP: https://docs.cekura.ai/documentation/introduction
- Cekura self-improving loop: https://www.cekura.ai/blogs/self-improving-voice-agents-closing-eval-loop
- Supermemory docs: https://supermemory.ai/docs/intro
- Supermemory Pipecat integration: https://supermemory.ai/docs/integrations/pipecat
- Supermemory graph memory: https://supermemory.ai/docs/concepts/graph-memory
- NVIDIA Nemotron ASR Streaming: https://docs.nvidia.com/nim/speech/latest/asr/deploy-asr-models/nemotron-asr-streaming.html
- NVIDIA ASR NIM: https://docs.nvidia.com/nim/speech/latest/asr/index.html
- AWS Nemotron 3 Super on Bedrock: https://aws.amazon.com/blogs/machine-learning/run-nvidia-nemotron-3-super-on-amazon-bedrock/
- Thinking Machines interaction models: https://thinkingmachines.ai/blog/interaction-models/
- Inception real-time subagents: https://www.inceptionlabs.ai/blog/rise-of-realtime-subagents
- Tau Voice benchmark: https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks
- EVA voice-agent evaluation: https://huggingface.co/blog/ServiceNow-AI/eva
- NTIA AI in 911 operations: https://www.ntia.gov/other-publication/2025/ai-driven-transformation-9-1-1-operations
- Aurelian Cora 911 copilot: https://www.aurelian.com/cora
