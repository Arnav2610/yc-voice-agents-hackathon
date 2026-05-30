# Chronos 911 — Simulated 911 Call-Taker Copilot

> ⚠️ **Simulated training & copilot system.** Chronos does **not** connect to real 911 and
> never dispatches responders. Every call is a simulation. For a real emergency, call 911.

Most self-improving voice agents patch what the agent **says**. Chronos patches what it
**remembers, retrieves, escalates, and leaves unresolved** — its memory-retrieval, SOP
state-machine, interaction, and escalation **policies** — and proves the improvement with a
real before/after regression.

Built on the YC Voice Agents Hackathon Pipecat starter: **NVIDIA Nemotron ASR Streaming**
(STT) → **Nemotron-3-Super** (LLM) → **Gradium** (TTS), with **Supermemory** for institutional
memory and **Cekura** for evaluation + the self-improvement loop.

---

## What it does

**During a call** (the bot speaks as a calm, simulated call-taker):
- Transcribes live (NVIDIA ASR streaming).
- Classifies the incident (structure fire, vehicle crash, noise→disturbance, medical, …).
- Detects hazards (smoke, gas smell, fire, trapped person, child, weapons) deterministically.
- Retrieves relevant institutional memory from Supermemory (prior calls, SOPs, location
  aliases, past eval failures).
- Runs the SOP as a live checklist and recommends the single next safety question.
- Keeps **caller safety** and **third-party (someone-else-inside) safety** as separate
  branches — a caller getting out does **not** close the trapped-person branch.
- Recommends human-dispatcher escalation for any high-risk case.
- Emits a full timestamped event trace to a live dashboard.

**After a Cekura run** (the self-improvement loop):
- Classifies the failure (taxonomy: `WRONG_BRANCH_CLOSURE`, `MISSING_CRITICAL_QUESTION`, …).
- Generates a **safe, structured** policy patch (no arbitrary code — only whitelisted ops
  like `add_cannot_be_resolved_by_condition`, `set_escalation_required`, …).
- Reruns the regression suite against the patched policy.
- Accepts the patch only if the pass rate improves with **no new critical regression**.
- Writes the failure + patch rationale back to Supermemory as a learned rule.

The flagship fix: in structure-fire calls, **caller evacuation must not resolve third-party
trapped-person risk.** The baseline policy ships *without* that guard, Cekura/regression
catches it, and the loop patches it in — verified before/after:

| Metric | Before | After |
|---|---|---|
| Pass rate | 66.7% | **100%** |
| Wrong safety-branch closure | 4 | **0** |
| Missed trapped-person question | 3 | **0** |

These numbers are **computed live** by replaying the scenario scripts against the actual
policy files — not hardcoded.

---

## Architecture

The kernel does **not** let the LLM decide safety. Deterministic policy modules drive
detection/escalation/memory/SOP; the LLM only *voices* the policy-computed guidance.

```
caller ─▶ NVIDIA ASR ─▶ ChronosUserObserver ─▶ user_agg ─▶ Nemotron LLM ─▶ ChronosResponseObserver ─▶ Gradium TTS ─▶ caller
                              │  (drives kernel,                    ▲ (grounded by injected
                              │   injects live policy context)      │  CHRONOS LIVE CONTEXT)
                              ▼
            IncidentTracker · SafetySentinel · SOPEngine · FloorController · MemoryRetrieval (Supermemory)
                              │
                              ▼  events  ──▶  in-process FastAPI dashboard (:7861)

after eval:  Cekura report ─▶ failure classifier ─▶ patch generator (safe ops) ─▶ apply ─▶ regression rerun ─▶ accept/reject ─▶ Supermemory failure memory
```

Key files (in `server/`):

| Path | Role |
|---|---|
| `bot-chronos.py` | Main voice bot (pipeline + dashboard) |
| `chronos/kernel.py` | Per-call orchestrator; policy-driven branch closure |
| `chronos/incident_tracker.py` · `safety_sentinel.py` | Deterministic detection |
| `chronos/sop_engine.py` · `floor_controller.py` | Live checklist + interaction policy |
| `chronos/memory_retrieval.py` | Supermemory REST + deterministic local fallback |
| `chronos/improvement_loop.py` | Scenario runner, failure classifier, patch gen/apply, regression |
| `chronos/llm_guidance.py` | Chronos system prompt + live-context injection + offline Nemotron helpers |
| `chronos/cekura_adapter.py` | Scenario→test-case rendering, report parsing, fallback report |
| `chronos/pipecat_processors.py` | Bridges Pipecat frames ↔ kernel |
| `chronos/dashboard_server.py` · `dashboard/` | FastAPI + UI |
| `policies/*.yaml` | SOP, interaction, memory, improvement policies (the patch targets) |
| `data/cekura_scenarios.yaml` | 12 scenarios with scripts + machine-checkable assertions |

---

## Setup

```bash
cd server
uv sync                       # installs Pipecat + deps (Python pinned to 3.12)
# Put your keys in ../.env (repo root). See server/.env.example.
# Provided during the hackathon: GRADIUM_API_KEY, NVIDIA_ASR_URL, NEMOTRON_LLM_URL.
# Add: SUPERMEMORY_API_KEY (Chronos falls back to a local store without it).
make seed                     # seed institutional memory into Supermemory + local
```

## Run the demo — everything from the dashboard

The dashboard at **http://localhost:7861** has a control bar that drives the entire flow from
the browser — no terminal needed during the demo:

```bash
make dash      # dashboard only (browser-driven demo)   → http://localhost:7861
#   then in the browser, click in order:
#   ① Seed memory   ② Play call (pick a scenario)   ③ Baseline eval   ④ Self-improve   ↺ Reset
```

The control bar buttons map to: seed Supermemory, play a scripted call onto the live panels,
run the baseline regression, run the self-improvement loop (before→after + policy diff), and
reset to baseline. The **Cekura live eval** panel shows the last real WebSocket run's scores.

For a **real voice call** (mic), run the bot and use the clean live view:

```bash
make bot       # voice bot (WebRTC :7860) + dashboard (:7861) in one process
#   → talk:  http://localhost:7860        (Connect, allow mic, speak)
#   → watch: http://localhost:7861/live   (minimal, glanceable live view for demos)
```

See **[LIVE_DEMO.md](./LIVE_DEMO.md)** for caller scripts and exactly what to watch for each.

CLI equivalents (if you prefer the terminal):

```bash
make seed | democall [SCENARIO=…] | regression | improve | improve-llm | reset
```

## Tests

```bash
make test           # unit tests + baseline suite
```

---

## What's live vs mocked

| Capability | Status |
|---|---|
| NVIDIA Nemotron ASR streaming (STT) | **Live** (hackathon endpoint) |
| Nemotron-3-Super (LLM) | **Live** (hackathon endpoint; reasoning OFF on the voice path) |
| Gradium (TTS) | **Live** |
| Supermemory (memory) | **Live** REST (`/v3/documents` write, `/v4/search` hybrid read) + deterministic local fallback |
| Cekura (eval) | Driven via the Cekura MCP / `/cekura-report`; deterministic regression runner is the always-available backbone; seeded fallback report included |
| CAD / SMS / dispatch | **Mocked** — `chronos/mocks.py`, returns fake `SIM-*` ids, never contacts anything real |
| Telephony | Local **WebRTC** (Twilio/Pipecat-Cloud path left intact but out of scope for the demo) |

## Cekura (live — ran end-to-end)

The 12 scenarios are live in Cekura (org 4807 / project 5839 / agent 18026; IDs in
`server/data/cekura_ids.json`). A **real Cekura WebSocket eval was run against the agent** and
scored on the LLM-judged **Expected Outcome** metric: **9/12 (75%)** (run id 591096; full
breakdown in `chronos/runtime/cekura_live_result.json`). The 3 misses were genuine LLM-voicing
issues (over-asking on a true-negative, not explicitly confirming a corrected location, a terse
escalation), not policy bugs — exactly the kind of finding the improvement loop consumes.

Because the bot runs locally (not on Pipecat Cloud), Cekura reaches it through a **text-WS
bridge + ngrok**:

```bash
# 1) start the bridge (drives the REAL kernel + Nemotron per caller turn)
uv run python scripts/run_text_ws.py          # ws://localhost:8970
# 2) expose it
ngrok http 8970                               # -> https://<id>.ngrok-free.app
# 3) from Claude Code (Cekura MCP authed), run the scenarios against the wss URL:
#    scenarios_run_text(agent_id=18026, scenarios=[...12 ids...],
#                       websocket_url="wss://<id>.ngrok-free.app", concurrency_limit=12)
#    then attach/evaluate the "Expected Outcome" metric (code b3b77859).
```

The bridge speaks Cekura's text protocol (`{"content": ...}` per turn, `{"type":"end_call"}`),
caps each turn's LLM call, and wraps up after the safety info is gathered so conversations end
cleanly. `chronos/cekura_adapter.py` also renders the scenarios into Cekura specs
(`make fake-cekura`) and parses a returned report into the improvement loop's failure objects.
The deterministic regression guarantees the before/after even if a live run isn't available.

## Safety framing

Chronos is a **copilot and training simulator**, not an autonomous dispatcher. It never tells a
caller to re-enter danger, never says a scene is safe, never promises an ETA, never diagnoses,
and always recommends human escalation for fire/smoke/gas/trapped person/injury/violence/child
in danger/medical crisis. UI and the spoken greeting both disclaim that calls are simulated.
