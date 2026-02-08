"""
Span creation and attribute-setting helpers for tracing.
"""
import logging
from typing import Any, Optional

from opentelemetry.trace import Span, StatusCode, Tracer

from app.core.config import settings
from app.tracing.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_SYSTEM,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_MAX_OUTPUT_TOKENS,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_CALL_ID,
    PROXY_REQUEST_ID,
    PROXY_STREAM,
    PROXY_IS_PTC,
    PROXY_API_MODE,
    PROXY_USAGE_CACHE_READ_TOKENS,
    PROXY_USAGE_CACHE_WRITE_TOKENS,
    PROXY_PTC_SESSION_ID,
    SPAN_GEN_AI_CHAT,
    SPAN_BEDROCK_INVOKE,
    SPAN_GEN_AI_EXECUTE_TOOL,
    SPAN_PTC_CODE_EXECUTION,
)

logger = logging.getLogger(__name__)


def start_llm_span(
    tracer: Optional[Tracer],
    request_data: Any,
    request_id: str,
    session_id: Optional[str] = None,
    stream: bool = False,
    is_ptc: bool = False,
) -> Optional[Span]:
    """Start a gen_ai.chat span for an LLM request."""
    if tracer is None:
        return None

    span = tracer.start_span(SPAN_GEN_AI_CHAT)

    span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
    span.set_attribute(GEN_AI_SYSTEM, "aws.bedrock")
    span.set_attribute(PROXY_REQUEST_ID, request_id)
    span.set_attribute(PROXY_STREAM, stream)
    span.set_attribute(PROXY_IS_PTC, is_ptc)

    if hasattr(request_data, "model") and request_data.model:
        span.set_attribute(GEN_AI_REQUEST_MODEL, request_data.model)

    if hasattr(request_data, "max_tokens") and request_data.max_tokens:
        span.set_attribute(GEN_AI_REQUEST_MAX_OUTPUT_TOKENS, request_data.max_tokens)

    if hasattr(request_data, "temperature") and request_data.temperature is not None:
        span.set_attribute(GEN_AI_REQUEST_TEMPERATURE, request_data.temperature)

    if hasattr(request_data, "top_p") and request_data.top_p is not None:
        span.set_attribute(GEN_AI_REQUEST_TOP_P, request_data.top_p)

    if session_id:
        span.set_attribute(GEN_AI_CONVERSATION_ID, session_id)

    # Optionally trace prompt content (PII opt-in)
    if settings.otel_trace_content:
        try:
            messages = getattr(request_data, "messages", [])
            if messages:
                # Add prompt content as a span event
                prompt_summary = []
                for msg in messages[-3:]:  # Last 3 messages to keep size manageable
                    role = getattr(msg, "role", "unknown") if hasattr(msg, "role") else msg.get("role", "unknown")
                    content = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "")
                    if isinstance(content, str):
                        prompt_summary.append(f"{role}: {content[:500]}")
                    elif isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", "")[:200])
                            elif hasattr(block, "type") and block.type == "text":
                                text_parts.append(getattr(block, "text", "")[:200])
                        if text_parts:
                            prompt_summary.append(f"{role}: {' '.join(text_parts)}")

                span.add_event("gen_ai.prompt", {"content": "\n".join(prompt_summary)})
        except Exception:
            pass  # Don't fail on content tracing errors

    return span


def set_llm_response_attributes(span: Optional[Span], response: Any) -> None:
    """Set response attributes on an LLM span."""
    if span is None:
        return

    try:
        # Set response ID
        response_id = getattr(response, "id", None)
        if response_id:
            span.set_attribute(GEN_AI_RESPONSE_ID, response_id)

        # Set stop reason
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason:
            span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [stop_reason])

        # Set token usage
        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)
            if input_tokens:
                span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
            if output_tokens:
                span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)

            # Cache tokens
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            if cache_read:
                span.set_attribute(PROXY_USAGE_CACHE_READ_TOKENS, cache_read)
            if cache_write:
                span.set_attribute(PROXY_USAGE_CACHE_WRITE_TOKENS, cache_write)

        # Optionally trace completion content
        if settings.otel_trace_content:
            content = getattr(response, "content", [])
            if content:
                text_parts = []
                for block in content:
                    if hasattr(block, "type") and block.type == "text":
                        text_parts.append(getattr(block, "text", "")[:500])
                if text_parts:
                    span.add_event("gen_ai.completion", {"content": "\n".join(text_parts)})

    except Exception as e:
        logger.debug(f"Error setting response attributes on span: {e}")


def start_bedrock_span(
    tracer: Optional[Tracer],
    api_mode: str,
    model_id: str,
) -> Optional[Span]:
    """Start a bedrock.invoke_model child span."""
    if tracer is None:
        return None

    span = tracer.start_span(SPAN_BEDROCK_INVOKE)
    span.set_attribute(PROXY_API_MODE, api_mode)
    span.set_attribute(GEN_AI_REQUEST_MODEL, model_id)
    return span


def start_tool_span(
    tracer: Optional[Tracer],
    tool_name: str,
    tool_use_id: str,
) -> Optional[Span]:
    """Start a gen_ai.execute_tool child span."""
    if tracer is None:
        return None

    span = tracer.start_span(f"{SPAN_GEN_AI_EXECUTE_TOOL} {tool_name}")
    span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
    span.set_attribute(GEN_AI_TOOL_CALL_ID, tool_use_id)
    return span


def start_ptc_span(
    tracer: Optional[Tracer],
    session_id: str,
) -> Optional[Span]:
    """Start a ptc.code_execution child span."""
    if tracer is None:
        return None

    span = tracer.start_span(SPAN_PTC_CODE_EXECUTION)
    span.set_attribute(PROXY_PTC_SESSION_ID, session_id)
    return span


def set_error_on_span(span: Optional[Span], error: Exception) -> None:
    """Set error status and record exception on a span."""
    if span is None:
        return

    try:
        span.set_status(StatusCode.ERROR, str(error))
        span.record_exception(error)
    except Exception:
        pass  # Don't fail on tracing errors
