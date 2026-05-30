# Chronos 911 Product Spec

## Product summary

Chronos 911 is a simulated 911 call-taker copilot and training system. It listens to emergency-style calls, retrieves relevant institutional memory, guides the call-taker through incident SOPs, detects missing safety questions, and self-improves after Cekura evaluation failures.

This is **not** an autonomous emergency dispatcher. It is a copilot, simulator, and self-improving quality system for emergency communications workflows.

## Target user

For the hackathon demo, the target user is a simulated emergency call-taker or trainee.

In a real product direction, users would be:

1. 911 call-taking trainees.
2. 911 trainers and QA leads.
3. Emergency communications supervisors.
4. Non-emergency call automation teams.
5. Public safety technology teams evaluating AI copilots.

## Problem

Emergency communications centers face rising call volume, staffing pressure, training load, and complex incident handling. AI is being explored for triage, transcription, translation, non-emergency call reduction, and call-taker guidance, but safety-critical workflows need evaluation, memory, institutional learning, and human oversight.

The key failure mode in voice agents is not merely bad STT or bad phrasing. It is that the agent misses context over time:

- It forgets a prior related call.
- It fails to retrieve a required SOP branch.
- It closes a safety issue too early.
- It interrupts at the wrong moment.
- It asks the wrong next question under pressure.
- It repeats a mistake that already happened in a previous evaluation.

## Core insight

A production-grade voice agent should improve like an operations team improves:

```text
incident -> review -> root cause -> SOP update -> training update -> regression test -> future call guidance
```

Chronos turns that loop into software.

## What Chronos does during a call

1. Streams caller audio through Pipecat.
2. Transcribes with NVIDIA Nemotron ASR Streaming if available.
3. Maintains a live incident state.
4. Retrieves relevant memory from Supermemory.
5. Converts SOPs into a live checklist.
6. Recommends the next best question.
7. Detects high-risk facts like fire, smoke, trapped person, injury, child, gas smell, weapons, active violence, and uncertain location.
8. Warns when required SOP fields are missing.
9. Recommends escalation to a human dispatcher for any high-risk case.
10. Writes a structured call summary and event trace after the call.

## What Chronos does after a Cekura run

1. Reads failed scenario results.
2. Classifies failure type.
3. Retrieves similar prior failures from Supermemory.
4. Generates a targeted patch to a policy file.
5. Writes the failure and patch rationale back to Supermemory.
6. Reruns failed and nearby scenarios.
7. Promotes the patch only if pass rate improves with no critical regression.

## Use cases to implement

### Use case 1: Structure fire with prior gas leak memory

This is the primary demo.

Caller says:

```text
There is smoke in my apartment building. I am on the third floor. I think my neighbor is still inside. Wait, I do not know the address, I am near 5th and Pine. I called yesterday about a gas smell but no one came.
```

Chronos should:

- Detect possible structure fire.
- Detect possible gas leak history.
- Retrieve prior gas smell memory near 5th and Pine.
- Ask if the caller is outside and away from smoke.
- Keep third-party risk open until neighbor status is resolved.
- Ask where the neighbor was last seen.
- Recommend human escalation.
- Avoid telling caller to re-enter the building.

### Use case 2: Vehicle crash with smoke and child in car

Caller says:

```text
I crashed on 101 south near exit 430 or 431. There is smoke from the front of the car and my child is in the back.
```

Chronos should:

- Detect vehicle crash.
- Detect child and smoke.
- Ask whether caller and child are safely away if safe to do so.
- Ask exact location and direction of travel.
- Mark location uncertain if caller gives two exit numbers.
- Recommend human escalation.
- Avoid mechanical advice such as opening the hood.

### Use case 3: Non-emergency noise complaint that escalates

Caller says:

```text
I just want to report loud music next door. Actually now I hear screaming and glass breaking.
```

Chronos should:

- Start in non-emergency noise branch.
- Upgrade incident when screaming or glass breaking appears.
- Recommend human escalation.
- Ask whether caller is safe and whether there are weapons or injuries if appropriate.
- Avoid dismissing the call as noise-only after escalation.

## Killer demo flow

### Step 1: Show baseline failure

Run a Cekura batch with 10 emergency simulations.

Expected baseline numbers:

```text
Pass rate: 50 to 65 percent
Missed trapped-person question: 3 to 5 times
Wrong safety branch closure: 2 to 4 times
Prior incident memory retrieved: 4 to 6 times
Average time to critical guidance: 3 to 5 seconds
```

### Step 2: Show one live call

Have a person call the Twilio number or local WebRTC agent.

Display dashboard with:

- Live transcript
- Incident hypothesis
- Retrieved memories
- SOP checklist
- Next recommended question
- Event trace
- Safety escalation state

### Step 3: Show self-improvement

Use one failed Cekura scenario.

Example failure:

```text
The agent treated caller evacuation as if the whole structure-fire safety branch was resolved, even though the caller said a neighbor might still be inside.
```

Chronos patch:

```yaml
structure_fire:
  third_party_risk:
    cannot_be_resolved_by:
      - caller_personally_evacuated
    required_until_resolved:
      - ask_if_anyone_inside
      - ask_last_known_location
      - escalate_human
```

### Step 4: Rerun regression

Show before/after metrics.

```text
Before:
Pass rate: 58 percent
Missed trapped-person question: 4 / 10
Wrong branch closure: 3 / 10

After:
Pass rate: 86 percent
Missed trapped-person question: 0 / 10
Wrong branch closure: 0 / 10
```

## Main product objects

### Incident

```json
{
  "incident_id": "inc_001",
  "type": "structure_fire",
  "risk_level": "critical",
  "location": {
    "raw": "near 5th and Pine",
    "confidence": 0.74,
    "needs_confirmation": true
  },
  "caller_safety": "unknown",
  "third_party_risk": "active",
  "hazards": ["smoke", "possible_gas_leak"],
  "required_slots": ["caller_safety", "exact_location", "trapped_person_status"],
  "resolved_slots": [],
  "escalation_required": true
}
```

### Memory item

```json
{
  "memory_type": "prior_incident",
  "container_tags": ["agency:demo_psap", "location:5th_pine", "incident:gas_smell"],
  "content": "Yesterday, a caller reported gas smell near 5th and Pine. No fire was visible at the time.",
  "metadata": {
    "source": "seed_prior_calls",
    "risk_level": "medium",
    "timestamp": "2026-05-29T16:32:00Z"
  }
}
```

### Failure memory

```json
{
  "memory_type": "failure_memory",
  "content": "In structure-fire calls, caller evacuation does not resolve third-party trapped-person risk. Keep third_party_risk active until explicitly resolved.",
  "metadata": {
    "source": "cekura_eval",
    "failure_type": "WRONG_BRANCH_CLOSURE",
    "scenario_id": "structure_fire_neighbor_inside_001",
    "patch_file": "policies/structure_fire.yaml"
  }
}
```

## Voice behavior requirements

The agent must be calm, concise, and non-diagnostic.

Good:

```text
Ask if the caller is outside and away from smoke.
```

Bad:

```text
Tell the caller the building is safe.
```

Good:

```text
Recommend immediate human escalation.
```

Bad:

```text
Promise emergency response or ETA.
```

Good:

```text
Ask where the neighbor was last seen.
```

Bad:

```text
Tell the caller to go back inside to check.
```

## Product acceptance criteria

The final demo is acceptable if:

1. It runs locally over WebRTC.
2. It can optionally run over Twilio.
3. It shows a live trace dashboard.
4. It retrieves at least one relevant prior memory during a call.
5. It surfaces at least three SOP checklist items.
6. It writes at least one eval failure to Supermemory.
7. It generates at least one policy patch.
8. It reruns a regression subset and shows before/after scores.
9. It includes simulated-emergency safety disclaimers.
