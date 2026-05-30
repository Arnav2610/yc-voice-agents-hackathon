# Research and Documentation Notes

This file gives the coding agent the important research context and the official docs to use while building.

## Hackathon stack from README

Base repo:

https://github.com/pipecat-ai/yc-voice-agents-hackathon

Important README facts:

- Use Pipecat as the orchestration framework.
- Cekura should be used to evaluate and improve the agent.
- NVIDIA open models are encouraged.
- Starter includes a GPT-4.1 version and a NVIDIA/Nemotron version.
- Local testing uses SmallWebRTC.
- Production telephony uses Twilio.
- Deployment target is Pipecat Cloud.
- Cekura can be driven from Claude Code using their MCP server and skills.
- `/cekura-report` can create and run 10 to 20 evaluator test cases and return transcripts, scores, and failures.

## Pipecat

Official docs:

- Main docs: https://docs.pipecat.ai/
- Quickstart: https://docs.pipecat.ai/pipecat/get-started/quickstart
- GitHub: https://github.com/pipecat-ai/pipecat
- Twilio WebSocket integration: https://docs.pipecat.ai/pipecat/telephony/twilio-websockets
- Pipecat Cloud Twilio transport: https://docs.pipecat.ai/pipecat-cloud/guides/telephony/twilio-websocket
- Gradium TTS service in Pipecat: https://docs.pipecat.ai/api-reference/server/services/tts/gradium

Relevant notes:

- Pipecat is the real-time voice and multimodal agent orchestration layer.
- Pipecat supports audio pipelines, transport, STT, LLM, TTS, frame processors, and custom processors.
- Pipecat Cloud supports Twilio bidirectional Media Streams.
- The hackathon starter should be modified rather than rebuilt from scratch.

## Twilio

Official docs:

- Twilio Media Streams: https://www.twilio.com/docs/voice/media-streams
- Pipecat Twilio WebSocket guide: https://docs.pipecat.ai/pipecat/telephony/twilio-websockets

Relevant notes:

- Twilio Media Streams allow phone call audio to be streamed over WebSockets.
- For the demo, use a Twilio number connected to a Pipecat Cloud bot.
- Never configure the demo as a real emergency number.
- Twilio should only be used as a simulated emergency training line.

## NVIDIA Nemotron

Official docs and model pages:

- Nemotron ASR Streaming docs: https://docs.nvidia.com/nim/speech/latest/asr/deploy-asr-models/nemotron-asr-streaming.html
- NVIDIA ASR NIM overview: https://docs.nvidia.com/nim/speech/latest/asr/index.html
- NVIDIA Nemotron ASR Streaming model card: https://build.nvidia.com/nvidia/nemotron-asr-streaming/modelcard
- Nemotron 3 Super on AWS Bedrock: https://aws.amazon.com/blogs/machine-learning/run-nvidia-nemotron-3-super-on-amazon-bedrock/
- AWS model card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-nvidia-nemotron-super-3-120b.html
- Hugging Face model card: https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16

Relevant notes:

- Nemotron ASR Streaming supports streaming English speech-to-text and partial transcripts.
- NVIDIA ASR NIM supports streaming mode for live voice applications.
- Nemotron 3 Super is the intended high-reasoning layer when the hackathon endpoint is available.
- If hackathon endpoints fail, implement fallback adapters to GPT-4.1 or another available LLM so the demo is not blocked.

## Gradium

Docs and references:

- Gradium main site: https://gradium.ai/
- Pipecat Gradium service: https://docs.pipecat.ai/api-reference/server/services/tts/gradium
- Gradium and Pipecat guide: https://gradium.ai/content/audiobook-agent-gradium-pipecat

Relevant notes:

- The hackathon README uses Gradium for TTS.
- The starter may also use Gradium STT in GPT-4.1 mode.
- Keep TTS concise and low latency.

## Cekura

Official docs:

- Docs: https://docs.cekura.ai/
- Introduction and MCP setup: https://docs.cekura.ai/documentation/introduction
- Testing approach: https://docs.cekura.ai/documentation/guides/testing-agents/suggested-testing-approach
- Pipecat automated testing: https://docs.cekura.ai/documentation/integrations/pipecat/automated
- Pipecat run evaluator API: https://docs.cekura.ai/api-reference/test_framework/run-evaluator-pipecat-v2
- Cekura for agents MCP blog: https://www.cekura.ai/blogs/cekura-for-agents
- Self-improving voice agents blog: https://www.cekura.ai/blogs/self-improving-voice-agents-closing-eval-loop
- Claude skills GitHub: https://github.com/cekura-ai/claude-skills

Useful command from Cekura docs:

```bash
claude mcp add --transport http Cekura https://api.cekura.ai/mcp \
  --header "X-CEKURA-API-KEY:YOUR_API_KEY"
```

Hackathon README also recommends:

```text
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
/cekura-report
```

Relevant notes:

- Cekura recommends generating 10 diverse test cases, running them, reviewing failed calls, and using the refined tests as regression tests.
- Cekura supports automated Pipecat testing and can create/manage sessions.
- The self-improving Cekura loop diagnoses failures, proposes prompt/config edits, redeploys, and reruns validation.
- Chronos should extend this by patching interaction, memory, escalation, and SOP policies, not only system prompts.

## Supermemory

Official docs:

- Intro: https://supermemory.ai/docs/intro
- Quickstart: https://supermemory.ai/docs/quickstart
- Python SDK: https://supermemory.ai/docs/memory-api/sdks/python
- SDK overview: https://supermemory.ai/docs/integrations/supermemory-sdk
- Pipecat integration: https://supermemory.ai/docs/integrations/pipecat
- Graph memory: https://supermemory.ai/docs/concepts/graph-memory
- Add memories: https://supermemory.ai/docs/add-memories
- Ingest conversation: https://supermemory.ai/docs/api-reference/ingest/ingest-or-update-conversation
- Search memory entries: https://supermemory.ai/docs/api-reference/recall-search/search-memory-entries
- User profiles: https://supermemory.ai/docs/user-profiles
- Document operations: https://supermemory.ai/docs/document-operations
- Pipecat memory repo: https://github.com/supermemoryai/pipecat-memory

Install:

```bash
pip install supermemory
pip install supermemory-pipecat
export SUPERMEMORY_API_KEY="YOUR_API_KEY"
```

Relevant notes:

- Supermemory provides long-term and short-term memory infrastructure for AI agents.
- Supermemory integrates directly with Pipecat.
- Supermemory graph memory supports relationships like update, extend, and derive.
- Conversation ingest accepts conversation ID, messages, container tags, and metadata.
- Memory search can be scoped with container tags and filters.
- Use container tags aggressively to avoid irrelevant memory retrieval.

## Frontier research inspiration

### Thinking Machines interaction models

Link: https://thinkingmachines.ai/blog/interaction-models/

Research idea to copy at the architecture level:

- Continuous real-time interaction should not be treated as an afterthought.
- Systems should process audio, video, and text continuously.
- The design uses multi-stream, micro-turn processing.
- For Chronos, approximate this with partial transcript events every few hundred milliseconds and a real-time interaction kernel.

### Inception real-time subagents

Link: https://www.inceptionlabs.ai/blog/rise-of-realtime-subagents

Research idea to copy:

- Production agents are becoming systems of specialized subagents.
- Fast utility models and subagents can handle routing, context compaction, tool search, and handoffs.
- For Chronos, split the system into Safety Sentinel, Memory Retrieval Planner, SOP Engine, Floor Controller, and Patch Generator.

### Tau Voice benchmark

Link: https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks

Research idea to copy:

- Voice-agent evaluation must jointly test task completion and conversational dynamics.
- Realistic voice conditions include interruptions, backchannels, background noise, telephony compression, and simultaneous speech.
- For Chronos, Cekura tests should include both task success and interaction scores.

### EVA voice-agent evaluation

Links:

- https://huggingface.co/blog/ServiceNow-AI/eva
- https://arxiv.org/abs/2605.13841

Research idea to copy:

- Evaluate complete multi-turn spoken conversations.
- Score both accuracy and experience.
- For Chronos, define two high-level score groups: Mission Accuracy and Interaction Quality.

### LTS-VoiceAgent

Link: https://arxiv.org/abs/2601.19952

Research idea to copy:

- Cascaded systems are often too slow if they wait for ASR, then LLM, then TTS strictly in sequence.
- An incremental listen-think-speak design can start reasoning before the user finishes.
- For Chronos, process partial transcripts and prefetch memory/SOPs during the caller's turn.

### Full-Duplex-Bench-v3

Link: https://arxiv.org/abs/2604.04847

Research idea to copy:

- Evaluate tool use under disfluency and full-duplex conditions.
- Self-correction and multi-step reasoning are persistent failure modes.
- For Chronos, include location correction, caller interruption, background third-party speech, and multi-step tool calls in scenarios.

### Third-party interruption research

Link: https://arxiv.org/abs/2604.17358

Research idea to copy:

- Spoken language models can fail when they cannot distinguish primary-user speech from third-party interruptions.
- For Chronos, include a scenario where a background person says irrelevant information and another where the background person reveals a safety-critical fact.

## 911 market and product research

### NTIA AI in 911 operations

Link: https://www.ntia.gov/other-publication/2025/ai-driven-transformation-9-1-1-operations

Important points:

- 911 centers face escalating call volumes, staffing shortages, and aging infrastructure.
- AI tools are being explored for triage, transcription, translation, and reducing non-emergency call volumes.
- Public-sector framing favors AI as staff support, not replacement.

### Aurelian

Links:

- Main: https://www.aurelian.com/
- Cora: https://www.aurelian.com/cora
- Cora launch: https://www.businesswire.com/news/home/20251216814691/en/Aurelian-Launches-Cora-an-AI-Copilot-for-911-Call-Takers-with-Snohomish-County-911

Important points:

- Aurelian is a real company building AI for public safety agencies.
- AVA handles non-emergency calls.
- CORA provides real-time context-aware guidance without taking control.
- This validates the category while leaving room for Chronos to differentiate via memory and self-improvement.

### NENA

Links:

- Standards: https://www.nena.org/page/Standards
- Call processing standard PDF: https://cdn.ymaws.com/www.nena.org/resource/resmgr/standards/nena-sta-020.1-2020_911_call.pdf
- Training guidelines: https://www.nena.org/page/trainingguidelines

Use only as high-level context. Do not claim compliance in the hackathon demo.

## Differentiation statement

The project should repeatedly emphasize:

```text
Most voice-agent improvement loops patch what the agent says.
Chronos patches what the agent remembers, retrieves, escalates, and leaves unresolved.
```
