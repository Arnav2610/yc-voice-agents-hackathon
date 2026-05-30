# Master Prompt for Claude Code or Other Coding Agent

Copy this entire prompt into Claude Code after placing all markdown files in the project root.

```text
You are building a hackathon project called Chronos 911.

You have access to a folder of markdown specs. Read every file before coding:

- 00_START_HERE.md
- 01_PRODUCT_SPEC.md
- 02_RESEARCH_AND_DOCS.md
- 03_TECHNICAL_ARCHITECTURE.md
- 04_IMPLEMENTATION_PLAN.md
- 05_EVALS_AND_SELF_IMPROVEMENT.md
- 06_PROMPTS_POLICIES_AND_SEED_DATA.md
- 07_CLAUDE_CODE_MASTER_PROMPT.md

Goal:
Build a working end-to-end demo of Chronos 911, a simulated 911 call-taker copilot that uses Pipecat, Twilio or SmallWebRTC, NVIDIA Nemotron models when available, Gradium TTS, Supermemory, and Cekura. It must show a self-improving loop where failed Cekura scenarios create failure memories, generate targeted policy patches, and improve regression scores.

Important safety framing:
- This is a simulated emergency training and copilot system.
- Do not connect to real 911.
- Do not claim autonomous dispatch.
- Do not give medical, police, or rescue instructions beyond safe generic guidance.
- Always recommend human escalation for fire, smoke, gas smell, trapped person, injury, active violence, child in danger, uncertain location with danger, and medical crisis.

Use the hackathon starter repo:
https://github.com/pipecat-ai/yc-voice-agents-hackathon

First inspect the starter files and run the baseline bot locally. Then implement Chronos incrementally.

Build order:
1. Run baseline Pipecat bot locally.
2. Add chronos/ modules and event store.
3. Add policy YAML files.
4. Add incident tracker and safety sentinel.
5. Add Supermemory client with local JSON fallback.
6. Seed demo memory.
7. Add LLM context builder and response parser.
8. Add dashboard endpoints and simple frontend.
9. Add fake Cekura report runner.
10. Add real Cekura adapter if API/MCP is available.
11. Add self-improvement loop that patches YAML policies safely.
12. Add demo scripts and README.

Acceptance criteria:
- Local WebRTC demo works.
- Dashboard shows live transcript, incident state, memory hits, SOP checklist, and events.
- The main smoke/gas/neighbor-inside scenario retrieves prior gas smell memory.
- The system recommends required next questions.
- The system stores an eval failure memory.
- The system applies a policy patch for third-party risk.
- The before/after metrics visibly improve.
- Fallbacks exist for unavailable NVIDIA, Cekura, Supermemory, Twilio, or Pipecat Cloud.

Do not overbuild. Prefer deterministic working demo over perfect abstractions.

When uncertain, use the specs in 00_START_HERE.md and 04_IMPLEMENTATION_PLAN.md as source of truth.

After implementing, produce:
- Updated README.md with setup and demo commands.
- A `make demo` or equivalent script.
- A short `DEMO_SCRIPT.md` for presenters.
- Notes on what is mocked vs live.
```

## Optional command for Cekura MCP

If using Claude Code with Cekura MCP:

```bash
claude mcp add --transport http Cekura https://api.cekura.ai/mcp \
  --header "X-CEKURA-API-KEY:YOUR_API_KEY"
```

If using hackathon Cekura skills:

```text
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
/cekura-report
```

## Final demo script for presenter

```text
Everyone is building self-improving voice agents. Most of them improve by patching prompts. Chronos improves like an emergency operations team: it remembers prior incidents, SOPs, eval failures, and accepted policy fixes.

This is a simulated 911 training copilot. It does not replace dispatchers.

I am going to call in with a messy emergency scenario.

The caller reports smoke in an apartment building, a possible trapped neighbor, and a prior gas smell near the same location.

Watch the right side. Chronos retrieves prior incident memory, opens the structure-fire checklist, keeps third-party trapped-person risk active, and recommends the next critical question.

Now we show the self-improvement loop. Before, the agent failed this exact class of scenario because it closed the safety branch when the caller personally evacuated. Cekura caught that. Chronos wrote the failure into Supermemory, patched the SOP state machine, reran regression scenarios, and improved without a critical regression.

The core idea is simple: production voice agents should not only learn what to say. They should learn what not to forget.
```
