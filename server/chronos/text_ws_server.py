"""Text WebSocket bridge so Cekura can run text simulations against Chronos.

Cekura connects as a CLIENT to this server's ws:// URL (run via run_scenarios_text
with `websocket_url`). Protocol:
  * Cekura sends each caller turn as {"content": "..."} (and {"type":"end_call"}
    to end).
  * We reply with the agent's spoken text as {"content": "..."}.

Each connection is one simulated call: it drives the REAL ChronosKernel
(deterministic detection / memory / SOP / escalation) and asks Nemotron to voice
the policy-grounded next question. Every connection is also registered with the
dashboard, so Cekura-driven calls show up live alongside WebRTC calls.

Run: `uv run python scripts/run_text_ws.py` (then expose via ngrok).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import websockets
from loguru import logger

from chronos import config
from chronos.dashboard_server import set_live_kernel
from chronos.events import STORE
from chronos.kernel import ChronosKernel
from chronos.llm_guidance import CHRONOS_SYSTEM_PROMPT, _chat, build_live_context_message
from chronos.memory_retrieval import ChronosMemoryClient


async def _handle(websocket) -> None:
    call_id = f"cekura_{uuid.uuid4().hex[:8]}"
    # Optional shared-secret check (Cekura sends X-VOCERA-SECRET).
    expected = os.getenv("CHRONOS_WS_SECRET")
    if expected:
        try:
            got = websocket.request.headers.get("X-VOCERA-SECRET")
            if got != expected:
                logger.warning(f"[{call_id}] bad X-VOCERA-SECRET; closing")
                await websocket.close(code=4001)
                return
        except Exception:
            pass

    logger.info(f"[{call_id}] Cekura text connection opened")
    mem = ChronosMemoryClient(api_key=os.getenv("SUPERMEMORY_API_KEY"))
    kernel = ChronosKernel(call_id, memory_client=mem, event_store=STORE)
    set_live_kernel(kernel)

    messages: list[dict[str, str]] = [{"role": "system", "content": CHRONOS_SYSTEM_PROMPT}]

    # Agent greets first (the Cekura agent is configured agent_gives_first_message).
    # NOTE: keep the greeting OUT of the LLM `messages` history so the model
    # doesn't parrot the disclaimer back on its first real reply.
    greeting = config.SPOKEN_GREETING
    kernel.on_agent_response(greeting)
    await websocket.send(json.dumps({"content": greeting}))

    _GOODBYE = ("goodbye", "bye", "thank you", "thanks", "that's all", "that is all", "okay bye")
    caller_turns = 0
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                msg = {"content": str(raw)}
            content = (msg.get("content") or "").strip()
            ending = msg.get("type") == "end_call"

            if content:
                await kernel.process_caller_turn(content)
                caller_turns += 1
            if ending:
                break
            if not content:
                continue

            reply = await _agent_reply(kernel, messages, content)
            messages.append({"role": "user", "content": content})
            messages.append({"role": "assistant", "content": reply})
            kernel.on_agent_response(reply)

            # Wrap up so the conversation terminates cleanly (Cekura's caller may
            # not hang up, and our agent shouldn't loop forever): end after the
            # caller winds down or after enough turns to gather the safety info.
            wind_down = caller_turns >= 6 or any(w in content.lower() for w in _GOODBYE)
            out = {"content": reply}
            if wind_down:
                out["type"] = "end_call"
            await websocket.send(json.dumps(out))
            if wind_down:
                break
    except websockets.ConnectionClosed:
        pass
    finally:
        try:
            await kernel.on_call_complete()
        except Exception:
            pass
        logger.info(f"[{call_id}] Cekura text connection closed")


async def _agent_reply(kernel: ChronosKernel, messages: list[dict[str, str]], caller_turn: str) -> str:
    """Ask Nemotron to voice the policy-grounded next question; fall back to the
    deterministic recommended question if the LLM is unreachable."""
    ctx = kernel.build_llm_context()
    live = build_live_context_message(ctx)
    turn_messages = messages + [live, {"role": "user", "content": caller_turn}]
    try:
        # Hard cap each LLM turn so a slow/hung Nemotron call can't stall the
        # conversation (which would block Cekura's concurrency slot).
        reply = await asyncio.wait_for(asyncio.to_thread(_chat, turn_messages, False, 160), timeout=12)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Nemotron reply failed/slow, using deterministic guidance: {type(e).__name__}")
        reply = ""
    if not reply:
        parts = []
        if kernel.state.recommended_question:
            parts.append(kernel.state.recommended_question)
        if kernel.state.incident.escalation_required:
            parts.append("I'm bringing in a human dispatcher now.")
        reply = " ".join(parts) or "Can you tell me your exact location?"
    return reply


async def serve(host: str = "0.0.0.0", port: int | None = None) -> None:
    port = port or int(os.getenv("CHRONOS_WS_PORT", "8970"))
    async with websockets.serve(_handle, host, port, ping_interval=30, ping_timeout=30):
        logger.info(f"Chronos text-WS bridge listening on ws://{host}:{port}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(serve())
