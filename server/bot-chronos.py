#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Chronos 911 — simulated 911 call-taker copilot (hackathon project).

SIMULATED training/copilot system. Not a real 911 service; never dispatches.

Pipeline: NVIDIA Nemotron ASR Streaming (STT) -> Chronos user observer ->
Nemotron-3-Super (LLM, grounded by policy) -> Chronos response observer ->
Gradium (TTS). A FastAPI dashboard runs in-process on CHRONOS_DASHBOARD_PORT.

Run locally::

    uv run bot-chronos.py
    # open http://localhost:7860  (WebRTC call)  and  http://localhost:7861  (dashboard)
"""

import os
import uuid

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndTaskFrame, LLMRunFrame
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import (
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.services.llm_service import FunctionCallParams
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from chronos import config
from chronos.dashboard_server import set_live_kernel, start_dashboard_in_thread
from chronos.events import STORE
from chronos.kernel import ChronosKernel
from chronos.llm_guidance import CHRONOS_SYSTEM_PROMPT
from chronos.memory_retrieval import ChronosMemoryClient
from chronos.pipecat_processors import ChronosResponseObserver, ChronosUserObserver
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService

load_dotenv(override=True)

_dashboard_started = False


def _ensure_dashboard() -> None:
    global _dashboard_started
    if not _dashboard_started:
        try:
            start_dashboard_in_thread(config.DASHBOARD_PORT)
            _dashboard_started = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dashboard failed to start: {e}")


def _build_stt():
    """NVIDIA Nemotron ASR streaming by default; Gradium STT as a fallback."""
    which = os.getenv("CHRONOS_STT", "nvidia").lower()
    if which == "gradium":
        from pipecat.services.gradium.stt import GradiumSTTService
        from pipecat.transcriptions.language import Language

        logger.info("Chronos STT: Gradium")
        return GradiumSTTService(
            api_key=os.environ["GRADIUM_API_KEY"],
            settings=GradiumSTTService.Settings(language=Language.EN),
        )
    logger.info("Chronos STT: NVIDIA Nemotron ASR streaming")
    return NVidiaWebSocketSTTService(
        url=os.getenv("NVIDIA_ASR_URL", "ws://44.241.251.184:8080"),
        strip_interim_prefix=True,
    )


async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
):
    """Main Chronos bot logic for one (simulated) call."""
    _ensure_dashboard()
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    logger.info(f"Starting Chronos call {call_id}")

    memory = ChronosMemoryClient(api_key=os.getenv("SUPERMEMORY_API_KEY"))
    logger.info(f"Chronos memory mode: {memory.mode}")
    kernel = ChronosKernel(call_id=call_id, memory_client=memory, event_store=STORE)
    set_live_kernel(kernel)

    stt = _build_stt()

    # Realtime voice: thinking OFF (avoid latency + any CoT leak into speech).
    voice_thinking = config._flag("CHRONOS_VOICE_THINKING", False)
    llm = VLLMOpenAILLMService(
        api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
        base_url=os.getenv("NEMOTRON_LLM_URL", "http://localhost:8000/v1"),
        settings=VLLMOpenAILLMService.Settings(
            model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
            system_instruction=CHRONOS_SYSTEM_PROMPT,
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": voice_thinking}}},
        ),
    )

    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=os.getenv("GRADIUM_VOICE_ID", "Eu9iL_CYe8N-Gkx_"),
        ),
    )

    async def dispatch_simulated_unit(params: FunctionCallParams) -> None:
        """Dispatch a simulated fire/police/EMS unit (training only — never real responders)."""
        args = params.arguments or {}
        unit_type = str(args.get("unit_type") or "").strip().lower()
        reason = str(args.get("reason") or "Simulated dispatch requested by copilot").strip()
        if unit_type not in ("fire", "police", "ems"):
            await params.result_callback({"ok": False, "error": "unit_type must be fire, police, or ems"})
            return
        sent = kernel.dispatch_simulated_units([unit_type], reason)
        await params.result_callback(
            {"ok": True, "dispatched": sent, "note": "Simulated dispatch only — no real responders sent."}
        )

    dispatch_tool = dispatch_simulated_unit
    tools = ToolsSchema(standard_tools=[dispatch_tool])
    llm.register_direct_function(dispatch_tool)

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    chronos_user = ChronosUserObserver(kernel, context)
    chronos_response = ChronosResponseObserver(kernel)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            chronos_user,         # drive kernel + inject live policy context
            user_aggregator,
            llm,
            chronos_response,     # capture spoken guidance into the trace
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Caller connected (simulated)")
        context.add_message(
            {
                "role": "user",
                "content": (
                    "A simulated caller just connected to the Chronos training line. "
                    f'Greet them by saying exactly: "{config.SPOKEN_GREETING}"'
                ),
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Caller disconnected — finalizing Chronos call")
        try:
            await kernel.on_call_complete()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"on_call_complete error: {e}")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point used by the Pipecat runner."""
    _ensure_dashboard()
    from_number: str | None = None
    transport_overrides: dict = {}

    if os.environ.get("ENV") != "local":
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

        krisp_filter = KrispVivaFilter()
    else:
        krisp_filter = None

    match runner_args:
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection
            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case WebSocketRunnerArguments():
            # Twilio path. NOTE: Twilio is 8 kHz; NVIDIA ASR needs 16 kHz, so set
            # CHRONOS_STT=gradium when using telephony. Kept intact but the
            # supported demo path is local WebRTC.
            transport_overrides["audio_in_sample_rate"] = 8000
            transport_overrides["audio_out_sample_rate"] = 8000
            _, call_data = await parse_telephony_websocket(runner_args.websocket)
            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            )
            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport, from_number=from_number, **transport_overrides)


if __name__ == "__main__":
    _ensure_dashboard()
    from pipecat.runner.run import main

    main()
