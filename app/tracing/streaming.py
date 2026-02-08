"""
Streaming span accumulator for SSE responses.

Wraps an SSE async generator to accumulate token usage and set span attributes
when the stream completes.
"""
import json
import logging
from typing import Any, AsyncGenerator, Optional

from opentelemetry.trace import Span, StatusCode

from app.tracing.attributes import (
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    PROXY_USAGE_CACHE_READ_TOKENS,
    PROXY_USAGE_CACHE_WRITE_TOKENS,
)

logger = logging.getLogger(__name__)


class StreamingSpanAccumulator:
    """Wraps an SSE async generator to accumulate metrics and finalize a span."""

    def __init__(
        self,
        span: Optional[Span],
        request_data: Any,
        request_id: str,
        trace_content: bool = False,
    ):
        self.span = span
        self.request_data = request_data
        self.request_id = request_id
        self.trace_content = trace_content

        # Accumulators
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.stop_reason: Optional[str] = None
        self.response_id: Optional[str] = None
        self.accumulated_text: list = []

    async def wrap_stream(
        self, sse_generator: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """
        Wrap an SSE generator, accumulating metrics and finalizing span on completion.

        Yields events unchanged (zero-copy pass-through).
        """
        try:
            async for sse_event in sse_generator:
                # Parse and accumulate metrics from the event
                self._accumulate_from_event(sse_event)
                # Yield event unchanged
                yield sse_event

        except Exception as e:
            if self.span:
                self.span.set_status(StatusCode.ERROR, str(e))
                self.span.record_exception(e)
            raise

        finally:
            self._finalize_span()

    def _accumulate_from_event(self, sse_event: str) -> None:
        """Parse SSE event and accumulate metrics."""
        if "data:" not in sse_event:
            return

        try:
            event_type, event_data = self._parse_sse_event(sse_event)
            if event_data is None:
                return

            if event_type == "message_start":
                message = event_data.get("message", {})
                self.response_id = message.get("id")
                usage = message.get("usage", {})
                self.input_tokens = usage.get("input_tokens", 0)
                self.cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                self.cache_write_tokens = usage.get("cache_creation_input_tokens", 0)

            elif event_type == "message_delta":
                delta = event_data.get("delta", {})
                self.stop_reason = delta.get("stop_reason", self.stop_reason)
                usage = event_data.get("usage", {})
                if "output_tokens" in usage:
                    self.output_tokens = usage["output_tokens"]
                if "input_tokens" in usage:
                    self.input_tokens = usage["input_tokens"]
                if "cache_read_input_tokens" in usage:
                    self.cache_read_tokens = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage:
                    self.cache_write_tokens = usage["cache_creation_input_tokens"]

            elif event_type == "content_block_delta" and self.trace_content:
                delta = event_data.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self.accumulated_text.append(text)

        except Exception:
            pass  # Don't fail on parse errors

    def _parse_sse_event(self, sse_event: str):
        """Parse SSE event string to extract event type and JSON data."""
        event_type = None
        event_data = None

        for line in sse_event.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    event_data = json.loads(line[5:].strip())
                except (json.JSONDecodeError, ValueError):
                    pass

        return event_type, event_data

    def _finalize_span(self) -> None:
        """Set accumulated attributes on span and end it."""
        if self.span is None:
            return

        try:
            if self.response_id:
                self.span.set_attribute(GEN_AI_RESPONSE_ID, self.response_id)
            if self.input_tokens:
                self.span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, self.input_tokens)
            if self.output_tokens:
                self.span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, self.output_tokens)
            if self.stop_reason:
                self.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [self.stop_reason])
            if self.cache_read_tokens:
                self.span.set_attribute(PROXY_USAGE_CACHE_READ_TOKENS, self.cache_read_tokens)
            if self.cache_write_tokens:
                self.span.set_attribute(PROXY_USAGE_CACHE_WRITE_TOKENS, self.cache_write_tokens)

            # Add completion content as event if tracing content
            if self.trace_content and self.accumulated_text:
                full_text = "".join(self.accumulated_text)
                if len(full_text) > 2000:
                    full_text = full_text[:2000] + "... [truncated]"
                self.span.add_event("gen_ai.completion", {"content": full_text})

        except Exception as e:
            logger.debug(f"Error finalizing streaming span: {e}")

        finally:
            try:
                self.span.end()
            except Exception:
                pass
