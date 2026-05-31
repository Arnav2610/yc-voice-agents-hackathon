# Chronos 911

**Voice Agent for 911 Calls** 

Chronos listens to live callers, runs structured emergency intake, and guides a voice agent.

# Chronos 911 Submission

## 1. What is this?

Chronos 911 is a simulated 911 call-taker copilot for training, evaluation, and emergency operations research.

Callers speak naturally over voice through WebRTC or Twilio. Chronos listens in real time, classifies the incident, runs a live SOP checklist, retrieves institutional memory, geocodes landmarks for simulated dispatch, and recommends escalation through a streaming operator dashboard.

The core idea is **mid-turn action**.

Emergency callers do not speak in clean turns. They ramble, panic, repeat themselves, self-correct, and bury critical facts halfway through a sentence. Most voice agents wait for the user to stop talking before reasoning. Chronos does not. As partial speech streams in, Chronos continuously extracts incident type, location, weapons, trapped persons, injuries, and danger signals; prefetches memory and geocoding; updates risk; advances the SOP checklist; and can trigger  dispatch while the caller is still talking.

The metric we optimize is not “conversation quality.” It is **time to first correct action**: the time until the system identifies the right emergency path, asks the next critical question, recommends escalation, or begins  dispatch.

In real emergency response, seconds matter. Chronos is designed to reduce the delay between a caller saying something important and the system acting on it.

Our second major feature is **self-improvement at the policy level**. After Cekura evals, Chronos does not just rewrite a prompt. It turns failures into safe YAML policy patches, learned knowledge via Supermemory, reruns regression tests, and only accepts fixes that improve the agent without introducing new safety regressions.

The hackathon demo focuses on high-stakes 911-style scenarios like structure fires, active threats, trapped persons, medical emergencies, and location ambiguity. Chronos is not intended to replace human dispatchers. It is a copilot and training system that helps call-takers move faster, miss fewer critical questions, and learn from every evaluated failure.

## 2. Demo video

https://www.youtube.com/watch?v=WfdDy0HUsfM

## 3. How we used Cekura, Nemotron models, and Pipecat

We built Chronos on Pipecat as the real-time voice orchestration layer. The system supports WebRTC for local browser calls and Twilio for phone calls. Audio flows through the Pipecat pipeline into streaming transcription, a stateful Chronos kernel, Nemotron-based reasoning, Gradium voice output, and a live operator dashboard.

We used NVIDIA Nemotron Speech Streaming for live transcription and Nemotron-3-Super as the call-taker reasoning model. Instead of asking the LLM to manage the entire call from raw transcript alone, we inject structured live context every turn: current incident hypothesis, extracted slots, memory hits, SOP checklist state, location candidates, risk flags, prior tool results, and policy constraints. This made the model much more reliable, concise, and dispatcher-like.

The key technical feature is the **mid-turn Chronos kernel**. Partial transcripts flow into the kernel while the caller is still speaking. The kernel extracts emergency facts, updates incident classification, retrieves memory, starts geocoding, advances the SOP checklist, and triggers simulated dispatch actions before the final transcript is complete.

We used Cekura to evaluate Chronos across 12 emergency-call scenarios, including structure fires, trapped-person risk, active threats, ambiguous landmarks, caller self-corrections, and unsafe branch closure. On live Expected Outcome evals, Chronos passed 9/12 scenarios before improvement.

Cekura surfaced a critical failure: in one structure-fire scenario, the agent treated “the caller evacuated” as equivalent to “everyone is safe,” so it stopped asking about a neighbor who might still be trapped inside. We turned that failure into a YAML policy patch:

* caller safety and third-party safety are now separate branches
* “caller evacuated” no longer closes trapped-person risk
* trapped-person risk remains active until explicitly resolved
* escalation is required when a third party may still be inside

After applying the patch and rerunning regression, the suite improved from 62% to 94% on the relevant policy/eval set. The important part is that the improvement was not just a prompt change. Cekura generated the failure signal, Chronos converted it into a policy-level fix, and regression testing proved the fix before accepting it.

## 4. What we built during the hackathon

We started from the Pipecat hackathon flower-shop demo bot and built Chronos 911 during the hackathon.

New during the hackathon:

* a simulated 911 call-taker copilot
* live incident classification
* mid-turn extraction from partial transcripts
* SOP checklist engine
* safety and escalation policy kernel
* institutional memory retrieval
* geocoding and landmark resolution for simulated dispatch
* simulated unit dispatch
* Twilio phone calling
* live operator dashboard
* satellite map view
* Google Maps copilot tools
* 12 Cekura emergency eval scenarios
* self-improvement loop that patches YAML policies after eval failures
* regression-backed accept/reject for policy patches
* specialized handling for active threats, structure fires, trapped-person risk, and branch-closure mistakes

Borrowed or reused:

* the Pipecat starter pipeline skeleton
* hackathon-provided NVIDIA/Nemotron endpoints
* Gradium voice services
* Cekura MCP/evaluation workflow
* Supermemory for memory infrastructure

The main hackathon contribution was turning a normal voice agent into a **self-improving emergency interaction kernel**: a system that acts mid-turn, tracks emergency state, remembers prior context, and improves policy behavior after evaluated failures.

## 5. Feedback on the tools

### NVIDIA / Nemotron

Nemotron worked well for this project.

Nemotron Speech Streaming was good enough to drive live partial extraction, which was essential for the mid-turn action demo. Nemotron-3-Super performed well as the call-taker reasoning model when grounded with structured state rather than raw transcript alone. It stayed calm, brief, and policy-aware in high-stress emergency scenarios, and it handled JSON-style incident extraction and slot filling well enough to drive the dashboard and policy engine.

What worked especially well:

* strong reasoning when given structured incident context
* calm, concise call-taker responses
* useful classification and slot extraction from partial and final transcripts
* good performance when paired with deterministic policy guards
* ability to explain failures and propose policy changes

What could be better:

* partial-turn latency: faster extraction would make mid-ramble action even stronger
* classification stability: we saw occasional flips between incident types, such as robbery vs. structure fire, so we added policy hysteresis and deterministic guards
* JSON reliability: partial transcripts sometimes produced malformed or overconfident structured outputs, so we added schema repair and fallback hints
* confidence calibration: it would be useful if the model exposed more reliable uncertainty around partial-speech extraction
* tool-call discipline under stress: in safety-critical scenarios, the model benefited from explicit policy constraints and should not be left to infer dispatch logic from prompt alone

Overall, Nemotron was strongest when used as a reasoning layer inside a structured real-time system, not as a monolithic voice-agent brain.

### Cekura

Cekura was very useful for moving beyond “the demo sounded good” into actual scenario-based evaluation.

The biggest value was that Cekura found failures we would not have noticed from casual testing. The trapped-neighbor branch-closure bug was the clearest example: the agent sounded reasonable, but the eval exposed that it had incorrectly closed a safety-critical branch. That became the basis for our self-improvement loop.

What worked well:

* scenario-based testing matched the hackathon theme perfectly
* Expected Outcome evals were easy to reason about
* failure reports gave us concrete examples to patch
* Cekura made it natural to run before/after regression
* pairing evals with YAML policy patches made the self-improvement loop feel real, not cosmetic

What could be better:

* local Pipecat connection took extra glue, including a text WebSocket bridge, ngrok, and manual URL configuration
* separating policy failures from LLM voicing failures still required manual triage
* richer structured failure categories would make automated patching easier
* native support for policy-regression workflows would be powerful: “this failure maps to this policy file, rerun these related scenarios, reject if safety regresses”
* better visibility into transcript timing would help evaluate mid-turn systems like Chronos, where the main metric is time-to-first-correct-action rather than only final call outcome

We did not hit a major blocking bug, but the connection and evaluation loop would be much easier if Cekura had a first-class “local Pipecat dev mode” and structured regression hooks for self-improving agents.

### Pipecat

Pipecat was a strong foundation for this project. It made it straightforward to insert custom processors into the voice pipeline and bridge a stateful kernel into a live voice agent. The pipeline abstraction was especially useful because Chronos is not just an LLM wrapper; it has streaming ASR, partial transcript handling, policy state, memory retrieval, tool calls, and voice output all moving at once.

What worked well:

* clean voice-agent pipeline structure
* easy to extend with custom processors
* WebRTC local testing was fast for iteration
* Twilio integration made the phone demo feel real
* compatible with the hackathon stack

What could be better:

* more examples of stateful, multi-processor agents would help
* more built-in tracing for partial transcripts, model events, tool calls, and latency would be useful
* better docs around interruption, barge-in, and mid-turn action patterns would help teams building beyond turn-based agents

Pipecat was the right base layer for Chronos because it let us build a production-style voice architecture rather than a one-off demo script.

## 6. Live Demo
Call +1 (559) 354-3744
and view the live status dashboard at https://aircraft-numerical-bag-bargains.trycloudflare.com/live while you're talking

## The Idea

Most voice agents wait for the caller to finish speaking, then react turn-by-turn. Emergency intake is time-sensitive: seconds matter, callers ramble, and dispatchers need structured facts *while* the person is still talking.

Chronos treats a 911 call as a **continuous stream** Partial speech updates the dashboard immediately, LLM extraction runs mid-utterance, and the copilot always knows the **single best next question** to ask. A separate **live operator dashboard** shows incident state, Structured Operating Procedure (SOP) progress, memory hits, and simulated unit dispatch in real time.

---

## Innovations

### Continuous intake, not turn-based

- **Streaming ASR** (NVIDIA Nemotron Speech) feeds **partial transcripts** into the kernel while the caller is still speaking.
- **Debounced mid-utterance LLM extraction** classifies the incident and fills SOP slots without waiting for silence.
- Tuned **VAD / incomplete-turn handling** so silence prompts the *next SOP question*, not generic “keep talking” filler.

### Official 911 SOP–driven intake table

- Provided with **Cowley County Emergency Communications (CCEC) structured SOPs**.
- On SOP classification, Nemotron generates a call-specific checklist aligned to the active protocol using **real-world 911 dispatcher guidelines**.
- **Nemotron extracts structured states** reliably from partial and full transcripts: incident type, location, hazards, safety branches, resolved slots.

### Policy-grounded voice agent

- The speaking LLM does **not** freestyle safety decisions. Each turn gets a **live context block**: computed incident state, missing slots, recommended next question, forbidden phrases, and dispatch status.
- **Separate caller-safety vs. third-party-safety branches** — a caller evacuating does not auto-close “someone still inside” risk.
- **Intelligent reclassification** (e.g. tongue injury / bleeding → medical, not active threat).

### Simulated emergency dispatch

- When policy + location warrant it, the voice agent calls **`dispatch_simulated_unit`** (fire · police · EMS) via a Pipecat LLM tool.
- The dashboard shows a **prominent dispatch banner** and unit log. (fake, of course)

### Location resolution

- Vague caller descriptions (“near the old Safeway on 5th”, “Y Combinator office”) are passed through the Google Maps Geocoding API that returns canonical address, confidence, aliases, and confirmation flags.

### Institutional memory

- **[Supermemory](https://supermemory.ai)** stores prior calls, SOP excerpts, location aliases, and past eval failures; retrieved **live during the call** to ground intake and guidance.

### Self-improvement via Cekura

- **[Cekura](https://cekura.com)** runs automated voice/text scenarios against the Pipecat agent, scores transcripts, and surfaces failures.
- Chronos **classifies failures**, generates **safe structured policy patches** (YAML ops — not prompt hacks), reruns regression, and writes learned rules back to memory.
- Flagship fix: **caller evacuation no longer wrongly closes trapped-person risk** — caught by eval, patched in policy, verified before/after (62% → 94% pass rate on the regression suite).

### Live operator dashboard

- Dual views: **control dashboard** (seed memory, run scenarios, improvement loop) and **live call view** (transcript, SOP intake table, dispatch alert, memory panel).
- Cross-process sync via `runtime/live.json` so dashboard and bot can run separately.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| **Voice orchestration** | [Pipecat](https://pipecat.ai) by [Daily](https://daily.co) — pipeline, VAD, aggregators, Twilio serializer |
| **Telephony** | [Twilio](https://twilio.com) Media Streams (WebRTC locally, phone via ngrok / Pipecat Cloud) |
| **Speech-to-text** | [NVIDIA Nemotron Speech Streaming](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b) on **AWS** |
| **LLM** | [Nemotron 3 Super](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16) (vLLM, OpenAI-compatible) on **AWS** |
| **Text-to-speech** | [Gradium](https://gradium.ai) |
| **Memory** | Supermemory (REST + local fallback) |
| **Eval & improvement** | Cekura (MCP, text WebSocket bridge, 12 seeded scenarios) |
| **Dashboard** | FastAPI + vanilla JS |

---

## Architecture (one glance)

```
Caller ──▶ Nemotron ASR (streaming) ──▶ Chronos kernel ──▶ Live dashboard
              │ partial + final                    │  SOP engine · memory · policy
              ▼                              ▼
         LLM extraction              Nemotron voice LLM ◀── Supermemory Context (Cekura used to fine tune)
         (mid-utterance)                    │
                                            ▼
                                       Gradium TTS ──▶ Caller

Post-call: Cekura eval ──▶ failure classifier ──▶ policy patch ──▶ regression ──▶ Supermemory
```

---

## Quick start

```bash
cd server
uv sync
cp ../.env.example ../.env   # add GRADIUM_API_KEY, NVIDIA URLs, optional SUPERMEMORY / CEKURA keys
make seed                      # seed CCEC SOPs + prior calls into memory
make bot                       # voice bot :7860 + dashboard :7861
```

| URL | Purpose |
|-----|---------|
| http://localhost:7860 | WebRTC voice call (Connect → speak) |
| http://localhost:7861 | Operator dashboard |

**Twilio phone calls:** expose port 7860 (ngrok), then `make bot-twilio PROXY=your-subdomain.ngrok-free.app` and attach the printed TwiML to your number.

**Cekura text evals:** `make text-ws` (bridge on :8970) then run scenarios via the Cekura MCP / dashboard.

**Self-improvement demo:** `make regression` → `make improve` (before/after metrics on the dashboard).

---

## Project layout

```
server/
  bot-chronos.py          # Main Pipecat voice bot
  chronos/                # Kernel, SOP engine, LLM extraction, memory, Cekura adapter
  dashboard/              # Live operator UI
  policies/               # YAML safety & SOP policies (patch targets)
  data/                   # CCEC SOP seeds, Cekura scenarios, eval assertions
  Makefile                # seed · bot · dash · improve · test · …
```


