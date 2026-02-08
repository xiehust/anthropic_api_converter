"""Unit tests for OpenTelemetry tracing module."""
import asyncio
import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry import trace


class InMemorySpanExporter(SpanExporter):
    """Simple in-memory span exporter for testing."""

    def __init__(self):
        self._spans = []

    def export(self, spans):
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def get_finished_spans(self):
        return list(self._spans)

    def shutdown(self):
        pass

    def clear(self):
        self._spans.clear()


def _make_tracer_and_exporter():
    """Create a TracerProvider with in-memory exporter and return (tracer, exporter).

    Uses the provider directly (not the global singleton) to avoid
    'Overriding of current TracerProvider is not allowed' warnings.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    return tracer, exporter, provider


class TestAttributes:
    """Test tracing attribute constants."""

    def test_gen_ai_attributes_defined(self):
        from app.tracing.attributes import (
            GEN_AI_OPERATION_NAME, GEN_AI_SYSTEM, GEN_AI_REQUEST_MODEL,
            GEN_AI_USAGE_INPUT_TOKENS, GEN_AI_USAGE_OUTPUT_TOKENS,
        )
        assert GEN_AI_OPERATION_NAME == "gen_ai.operation.name"
        assert GEN_AI_SYSTEM == "gen_ai.system"
        assert GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
        assert GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
        assert GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"

    def test_proxy_attributes_defined(self):
        from app.tracing.attributes import (
            PROXY_REQUEST_ID, PROXY_API_KEY_HASH, PROXY_STREAM,
        )
        assert PROXY_REQUEST_ID == "proxy.request_id"
        assert PROXY_API_KEY_HASH == "proxy.api_key_hash"
        assert PROXY_STREAM == "proxy.stream"

    def test_span_names_defined(self):
        from app.tracing.attributes import (
            SPAN_PROXY_REQUEST, SPAN_GEN_AI_CHAT, SPAN_BEDROCK_INVOKE,
        )
        assert SPAN_PROXY_REQUEST == "proxy.request"
        assert SPAN_GEN_AI_CHAT == "gen_ai.chat"
        assert SPAN_BEDROCK_INVOKE == "bedrock.invoke_model"


class TestSessionIdExtraction:
    """Test session ID extraction from various sources."""

    def test_session_id_from_header(self):
        from app.tracing.context import get_session_id
        request = MagicMock()
        request.headers = {"x-session-id": "session-123"}
        assert get_session_id(request=request) == "session-123"

    def test_session_id_from_metadata_dict(self):
        from app.tracing.context import get_session_id
        request_data = MagicMock()
        request_data.metadata = {"session_id": "meta-session"}
        request_data.container = None
        assert get_session_id(request_data=request_data) == "meta-session"

    def test_session_id_from_container_string(self):
        from app.tracing.context import get_session_id
        request_data = MagicMock()
        request_data.metadata = None
        request_data.container = "container-456"
        assert get_session_id(request_data=request_data) == "container-456"

    def test_session_id_none_when_no_sources(self):
        from app.tracing.context import get_session_id
        assert get_session_id() is None

    def test_header_takes_priority(self):
        from app.tracing.context import get_session_id
        request = MagicMock()
        request.headers = {"x-session-id": "from-header"}
        request_data = MagicMock()
        request_data.metadata = {"session_id": "from-metadata"}
        request_data.container = "from-container"
        assert get_session_id(request=request, request_data=request_data) == "from-header"


class TestSpanHelpers:
    """Test span creation helpers."""

    @pytest.fixture
    def tracer_setup(self):
        """Set up in-memory tracer for testing."""
        tracer, exporter, provider = _make_tracer_and_exporter()
        yield tracer, exporter
        provider.shutdown()

    def test_start_llm_span_returns_none_when_tracer_none(self):
        from app.tracing.spans import start_llm_span
        assert start_llm_span(None, MagicMock(), "req-1") is None

    def test_start_llm_span_creates_span(self, tracer_setup):
        from app.tracing.spans import start_llm_span
        tracer, exporter = tracer_setup

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-5-20250929"
        request_data.max_tokens = 1024
        request_data.temperature = 0.7
        request_data.top_p = None
        request_data.messages = []

        span = start_llm_span(tracer, request_data, "req-123", session_id="sess-1")
        assert span is not None
        span.end()

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "gen_ai.chat"
        attrs = dict(spans[0].attributes)
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.system"] == "aws.bedrock"
        assert attrs["gen_ai.request.model"] == "claude-sonnet-4-5-20250929"
        assert attrs["proxy.request_id"] == "req-123"
        assert attrs["gen_ai.conversation.id"] == "sess-1"

    def test_set_llm_response_attributes(self, tracer_setup):
        from app.tracing.spans import start_llm_span, set_llm_response_attributes
        tracer, exporter = tracer_setup

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-5-20250929"
        request_data.max_tokens = 1024
        request_data.temperature = None
        request_data.top_p = None
        request_data.messages = []

        span = start_llm_span(tracer, request_data, "req-1")

        response = MagicMock()
        response.id = "msg-abc"
        response.stop_reason = "end_turn"
        response.usage = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50
        response.usage.cache_read_input_tokens = 10
        response.usage.cache_creation_input_tokens = 5
        response.content = []

        set_llm_response_attributes(span, response)
        span.end()

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert attrs["gen_ai.response.id"] == "msg-abc"
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50

    def test_set_error_on_span(self, tracer_setup):
        from app.tracing.spans import start_llm_span, set_error_on_span
        tracer, exporter = tracer_setup

        request_data = MagicMock()
        request_data.model = "test"
        request_data.max_tokens = 1
        request_data.temperature = None
        request_data.top_p = None
        request_data.messages = []

        span = start_llm_span(tracer, request_data, "req-1")
        set_error_on_span(span, ValueError("test error"))
        span.end()

        spans = exporter.get_finished_spans()
        assert spans[0].status.status_code.name == "ERROR"

    def test_set_error_on_none_span(self):
        from app.tracing.spans import set_error_on_span
        # Should not raise
        set_error_on_span(None, ValueError("test"))

    def test_start_bedrock_span(self, tracer_setup):
        from app.tracing.spans import start_bedrock_span
        tracer, exporter = tracer_setup

        span = start_bedrock_span(tracer, "invoke_model", "claude-sonnet-4-5-20250929")
        assert span is not None
        span.end()

        spans = exporter.get_finished_spans()
        assert spans[0].name == "bedrock.invoke_model"
        attrs = dict(spans[0].attributes)
        assert attrs["proxy.api_mode"] == "invoke_model"

    def test_start_tool_span(self, tracer_setup):
        from app.tracing.spans import start_tool_span
        tracer, exporter = tracer_setup

        span = start_tool_span(tracer, "get_weather", "toolu_123")
        assert span is not None
        span.end()

        spans = exporter.get_finished_spans()
        assert "get_weather" in spans[0].name
        attrs = dict(spans[0].attributes)
        assert attrs["gen_ai.tool.name"] == "get_weather"
        assert attrs["gen_ai.tool.call.id"] == "toolu_123"


class TestStreamingSpanAccumulator:
    """Test StreamingSpanAccumulator."""

    @pytest.fixture
    def mock_span(self):
        span = MagicMock()
        return span

    def test_accumulates_tokens_from_stream(self, mock_span):
        from app.tracing.streaming import StreamingSpanAccumulator

        accumulator = StreamingSpanAccumulator(mock_span, MagicMock(), "req-1")

        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-1","usage":{"input_tokens":150}}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":25}}\n\n',
        ]

        async def mock_generator():
            for e in events:
                yield e

        async def run():
            collected = []
            async for event in accumulator.wrap_stream(mock_generator()):
                collected.append(event)
            return collected

        collected = asyncio.run(run())

        # Verify all events passed through
        assert len(collected) == 4

        # Verify accumulated values
        assert accumulator.input_tokens == 150
        assert accumulator.output_tokens == 25
        assert accumulator.stop_reason == "end_turn"
        assert accumulator.response_id == "msg-1"

        # Verify span was finalized
        mock_span.set_attribute.assert_any_call("gen_ai.response.id", "msg-1")
        mock_span.set_attribute.assert_any_call("gen_ai.usage.input_tokens", 150)
        mock_span.set_attribute.assert_any_call("gen_ai.usage.output_tokens", 25)
        mock_span.end.assert_called_once()

    def test_handles_stream_error(self, mock_span):
        from app.tracing.streaming import StreamingSpanAccumulator

        accumulator = StreamingSpanAccumulator(mock_span, MagicMock(), "req-1")

        async def error_generator():
            yield 'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-1","usage":{"input_tokens":10}}}\n\n'
            raise RuntimeError("stream error")

        async def run():
            async for _ in accumulator.wrap_stream(error_generator()):
                pass

        with pytest.raises(RuntimeError, match="stream error"):
            asyncio.run(run())

        # Span should still be ended
        mock_span.end.assert_called_once()

    def test_none_span_passes_through(self):
        from app.tracing.streaming import StreamingSpanAccumulator

        accumulator = StreamingSpanAccumulator(None, MagicMock(), "req-1")

        async def mock_generator():
            yield "event: ping\ndata: {}\n\n"

        async def run():
            collected = []
            async for event in accumulator.wrap_stream(mock_generator()):
                collected.append(event)
            return collected

        collected = asyncio.run(run())
        assert len(collected) == 1


class TestProviderInitShutdown:
    """Test provider initialization and shutdown."""

    def test_parse_headers(self):
        from app.tracing.provider import _parse_headers

        assert _parse_headers(None) == {}
        assert _parse_headers("") == {}
        assert _parse_headers("key1=val1,key2=val2") == {"key1": "val1", "key2": "val2"}
        assert _parse_headers("Authorization=Basic abc123") == {"Authorization": "Basic abc123"}

    @patch("app.tracing.provider.settings")
    def test_init_tracing_disabled(self, mock_settings):
        from app.tracing.provider import init_tracing
        import app.tracing.provider as provider_module

        mock_settings.enable_tracing = False
        provider_module._provider = None
        init_tracing()
        assert provider_module._provider is None

    @patch("app.tracing.provider.settings")
    def test_init_tracing_no_endpoint(self, mock_settings):
        from app.tracing.provider import init_tracing, shutdown_tracing
        import app.tracing.provider as provider_module

        mock_settings.enable_tracing = True
        mock_settings.otel_service_name = "test"
        mock_settings.app_version = "1.0"
        mock_settings.environment = "test"
        mock_settings.otel_trace_sampling_ratio = 1.0
        mock_settings.otel_exporter_endpoint = None
        mock_settings.otel_exporter_headers = None
        mock_settings.otel_exporter_protocol = "http/protobuf"
        mock_settings.otel_batch_max_queue_size = 2048
        mock_settings.otel_batch_schedule_delay_ms = 5000

        provider_module._provider = None
        init_tracing()
        assert provider_module._provider is not None

        # Cleanup
        shutdown_tracing()
        assert provider_module._provider is None


class TestContextPropagation:
    """Test OTEL context propagation for threads."""

    def test_propagate_and_attach(self):
        from app.tracing.context import (
            propagate_context_to_thread,
            attach_context_in_thread,
            detach_context_in_thread,
        )

        ctx = propagate_context_to_thread()
        assert ctx is not None

        token = attach_context_in_thread(ctx)
        assert token is not None

        # Should not raise
        detach_context_in_thread(token)

    def test_attach_none_context(self):
        from app.tracing.context import attach_context_in_thread
        assert attach_context_in_thread(None) is None

    def test_detach_none_token(self):
        from app.tracing.context import detach_context_in_thread
        # Should not raise
        detach_context_in_thread(None)
