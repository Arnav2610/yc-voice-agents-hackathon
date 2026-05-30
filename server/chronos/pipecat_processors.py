"""Pipecat frame processors that bridge the live pipeline to the Chronos kernel."""

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
            msgs = self._context._messages
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
                result = await asyncio.wait_for(
                    self._kernel.process_caller_turn(frame.text), timeout=4.0
                )
                self._inject_live_context()
                logger.info(
                    f"[chronos] turn -> incident={self._kernel.state.incident.incident_type} "
                    f"risk={self._kernel.state.incident.risk_level} "
                    f"3rd_party={self._kernel.state.incident.third_party_risk} "
                    f"handoff_ready={self._kernel.state.human_handoff_ready} "
                    f"next='{result.recommended_question}'"
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
            asyncio.create_task(self._kernel.observe_partial(frame.text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


class ChronosResponseObserver(FrameProcessor):
    """Buffer LLM output, sanitize policy violations, then forward to TTS."""

    def __init__(self, kernel: ChronosKernel) -> None:
        super().__init__()
        self._kernel = kernel
        self._buf: list[str] = []
        self._buffering = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buf = []
            self._buffering = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame) and self._buffering:
            if frame.text:
                self._buf.append(frame.text)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            raw = "".join(self._buf).strip()
            self._buf = []
            self._buffering = False
            if raw:
                spoken = self._kernel.sanitize_spoken_response(raw)
                if spoken != raw:
                    logger.info(f"[chronos] sanitized response: '{raw[:80]}' -> '{spoken[:80]}'")
                try:
                    self._kernel.on_agent_response(spoken)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Chronos response capture failed: {e}")
                if spoken:
                    await self.push_frame(LLMTextFrame(spoken), direction)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
