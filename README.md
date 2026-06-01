# Chronos 911

**Voice Agent for 911 Calls** 

Chronos listens to live callers, runs structured emergency intake, and guides a voice agent.

Chronos is a Greek word referring to the personification of time as an unrelenting measurable force. Emergency intake is time-sensitive, saving a few seconds of call-time can save thousands of lives.

> ⚠️  **Simulation only.**

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


