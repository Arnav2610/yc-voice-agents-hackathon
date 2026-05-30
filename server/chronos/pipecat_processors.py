"""Pipecat frame processors that bridge the live pipeline to the Chronos kernel.

Placement in the pipeline:

    transport.input() -> stt -> ChronosUserObserver -> user_aggregator -> llm
        -> ChronosResponseObserver -> tts -> transport.output() -> assistant_aggregator

ChronosUserObserver runs the deterministic kernel on each final transcript and
injects a CHRONOS LIVE CONTEXT system message into the shared LLM context BEFORE
forwarding the transcript, so the LLM's spoken reply is grounded in policy.
ChronosResponseObserver captures the assistant's spoken text for the trace.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from chronos.kernel import ChronosKernel
from chronos.llm_guidance import build_live_context_message


class ChronosUserObserver(FrameProcessor):
    """Observes caller transcripts, drives the kernel, injects live guidance."""

    def __init__(self, kernel: ChronosKernel, context: LLMContext) -> None:
        super().__init__()
        self._kernel = kernel
        self._context = context
        self._last_injected: dict | None = None

    def _inject_live_context(self) -> None:
        msg = build_live_context_message(self._kernel.build_llm_context())
        try:
            msgs = self._context._messages  # the real underlying list
            if self._last_injected is not None:
                for i, m in enumerate(msgs):
                    if m is self._last_injected:
                        msgs.pop(i)
                        break
            self._context.add_message(msg)
            self._last_injected = msg
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Chronos context injection failed: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            try:
                # process_caller_turn is now fast (<200ms) — extraction is
                # fire-and-forget, so no long timeout needed here.
                result = await asyncio.wait_for(
                    self._kernel.process_caller_turn(frame.text), timeout=1.5
                )
                self._inject_live_context()
                logger.info(
                    f"[chronos] turn -> incident={self._kernel.state.incident.incident_type} "
                    f"risk={self._kernel.state.incident.risk_level} "
                    f"3rd_party={self._kernel.state.incident.third_party_risk} "
                    f"escalate={result.escalation_required} next='{result.recommended_question}'"
                )
            except TimeoutError:
                logger.warning("[chronos] process_caller_turn timed out — injecting current state")
                self._inject_live_context()
            except Exception as e:  # noqa: BLE001
                logger.exception(f"Chronos kernel error on final turn: {e}")
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterimTranscriptionFrame) and frame.text:
            self._kernel.state.partial_buffer.append(frame.text)
            self._kernel._emit("partial_transcript", {"text": frame.text})
            # Real-time: drive detection + speculative memory prefetch mid-utterance
            # WITHOUT blocking frame flow. observe_partial coalesces/guards itself.
            asyncio.create_task(self._kernel.observe_partial(frame.text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


class ChronosResponseObserver(FrameProcessor):
    """Captures the assistant's spoken text and records it on the trace."""

    def __init__(self, kernel: ChronosKernel) -> None:
        super().__init__()
        self._kernel = kernel
        self._buf: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buf = []
        elif isinstance(frame, LLMTextFrame):
            if frame.text:
                self._buf.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._buf).strip()
            self._buf = []
            if text:
                try:
                    self._kernel.on_agent_response(text)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Chronos response capture failed: {e}")

        await self.push_frame(frame, direction)
