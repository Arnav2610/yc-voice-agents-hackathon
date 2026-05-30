# Build Checklist

Use this as the task tracker.

## Setup

- [ ] Clone hackathon starter repo.
- [ ] Add this markdown packet to project root or `/docs/chronos`.
- [ ] Create `.env` from starter `.env.example`.
- [ ] Add keys for OpenAI, Gradium, Supermemory, Cekura, Twilio, and NVIDIA endpoints if available.
- [ ] Run starter `bot-nemotron.py`.
- [ ] Run starter `bot-gpt.py` as fallback.

## Chronos core

- [ ] Create `chronos/events.py`.
- [ ] Create `chronos/state.py`.
- [ ] Create `chronos/kernel.py`.
- [ ] Create `chronos/incident_tracker.py`.
- [ ] Create `chronos/safety_sentinel.py`.
- [ ] Create `chronos/sop_engine.py`.
- [ ] Create `chronos/floor_controller.py`.
- [ ] Wire final transcript events into Chronos.
- [ ] Wire partial transcript events if available.
- [ ] Emit event trace for dashboard.

## Policies

- [ ] Add `policies/structure_fire.yaml`.
- [ ] Add `policies/vehicle_crash.yaml`.
- [ ] Add `policies/non_emergency_noise.yaml`.
- [ ] Add `policies/interaction_policy.yaml`.
- [ ] Add `policies/memory_retrieval_policy.yaml`.
- [ ] Add `policies/improvement_policy.yaml`.
- [ ] Load YAML policies at startup.
- [ ] Validate YAML policies with schema or basic checks.

## Supermemory

- [ ] Install `supermemory`.
- [ ] Install `supermemory-pipecat` if useful.
- [ ] Implement Supermemory client.
- [ ] Implement local JSON fallback.
- [ ] Seed SOP memories.
- [ ] Seed prior incident memories.
- [ ] Seed failure memories.
- [ ] Search memory during incident hypothesis changes.
- [ ] Search memory when location is mentioned.
- [ ] Write call summary after call.
- [ ] Write eval failure memory after failed scenario.

## Agent behavior

- [ ] Detect structure fire.
- [ ] Detect vehicle crash.
- [ ] Detect non-emergency noise complaint.
- [ ] Detect escalation from noise to violence.
- [ ] Detect trapped person risk.
- [ ] Detect gas smell.
- [ ] Detect smoke.
- [ ] Detect location correction.
- [ ] Keep caller safety and third-party safety separate.
- [ ] Ask next required SOP question.
- [ ] Recommend human escalation for critical cases.
- [ ] Avoid forbidden guidance.

## Dashboard

- [ ] Add `/chronos/events/{call_id}`.
- [ ] Add `/chronos/latest`.
- [ ] Add `/chronos/metrics`.
- [ ] Add `/chronos/policy-diff`.
- [ ] Build dashboard UI.
- [ ] Show transcript.
- [ ] Show incident state.
- [ ] Show memory hits.
- [ ] Show SOP checklist.
- [ ] Show self-improvement metrics.
- [ ] Show patch diff.

## Cekura and self-improvement

- [ ] Create 10 to 12 Cekura scenarios.
- [ ] Add fake Cekura report loader.
- [ ] Add failure classifier.
- [ ] Add patch generator.
- [ ] Restrict patch operations to safe YAML edits.
- [ ] Add regression runner with fake report mode.
- [ ] Add real Cekura API adapter if possible.
- [ ] Accept patch only when no critical regression appears.
- [ ] Store accepted patch rationale as memory.

## Demo

- [ ] Main script works: smoke, gas smell, neighbor inside.
- [ ] Vehicle crash script works.
- [ ] Noise complaint escalation script works.
- [ ] Before/after metrics load.
- [ ] Policy patch visibly changes behavior.
- [ ] `make demo` or equivalent works.
- [ ] README explains setup.
- [ ] `DEMO_SCRIPT.md` exists.
- [ ] All simulated-emergency disclaimers are visible.

## Fallbacks

- [ ] If Supermemory missing, local JSON memory still works.
- [ ] If Cekura missing, fake report still works.
- [ ] If Twilio missing, WebRTC still works.
- [ ] If NVIDIA missing, GPT fallback still works.
- [ ] If Gradium missing, console/text fallback still demonstrates logic.
