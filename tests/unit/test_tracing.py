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
            SPAN_TURN, SPAN_TRACE_ROOT,
        )
        assert SPAN_PROXY_REQUEST == "proxy.request"
        assert SPAN_GEN_AI_CHAT == "gen_ai.chat"
        assert SPAN_BEDROCK_INVOKE == "bedrock.invoke_model"
        assert SPAN_TURN == "Turn"
        assert SPAN_TRACE_ROOT == "trace_root"

    def test_langfuse_observation_attributes_defined(self):
        from app.tracing.attributes import (
            LANGFUSE_OBSERVATION_INPUT, LANGFUSE_OBSERVATION_OUTPUT,
        )
        assert LANGFUSE_OBSERVATION_INPUT == "langfuse.observation.input"
        assert LANGFUSE_OBSERVATION_OUTPUT == "langfuse.observation.output"


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


class TestSessionTraceStore:
    """Test SessionTraceStore with turn counting."""

    def test_put_and_get(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore()
        store.put("sess-1", 123, 456)
        result = store.get("sess-1")
        assert result is not None
        trace_id, span_id, turn_count, root_span = result
        assert trace_id == 123
        assert span_id == 456
        assert turn_count == 0
        assert root_span is None

    def test_put_with_root_span(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore()
        mock_span = MagicMock()
        store.put("sess-1", 123, 456, root_span=mock_span)
        result = store.get("sess-1")
        assert result is not None
        _, _, _, root_span = result
        assert root_span is mock_span

    def test_next_turn_increments(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore()
        store.put("sess-1", 123, 456)
        assert store.next_turn("sess-1") == 1
        assert store.next_turn("sess-1") == 2
        assert store.next_turn("sess-1") == 3

    def test_next_turn_missing_session(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore()
        assert store.next_turn("nonexistent") == 1

    def test_get_returns_updated_turn_count(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore()
        store.put("sess-1", 123, 456)
        store.next_turn("sess-1")
        store.next_turn("sess-1")
        result = store.get("sess-1")
        assert result is not None
        _, _, turn_count, _ = result
        assert turn_count == 2

    def test_first_request_wins(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore()
        store.put("sess-1", 100, 200)
        store.put("sess-1", 300, 400)  # Should be ignored
        result = store.get("sess-1")
        assert result is not None
        trace_id, span_id, _, _ = result
        assert trace_id == 100
        assert span_id == 200

    def test_ttl_expiry(self):
        from app.tracing.session_store import SessionTraceStore
        store = SessionTraceStore(ttl_seconds=0)
        mock_span = MagicMock()
        store.put("sess-1", 123, 456, root_span=mock_span)
        import time
        time.sleep(0.01)
        result = store.get("sess-1")
        assert result is None
        # Root span should have been ended on expiry
        mock_span.end.assert_called_once()


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

    def test_start_llm_span_with_context(self, tracer_setup):
        """Test that LLM span can be created as child of another span."""
        from app.tracing.spans import start_llm_span, start_turn_span
        from opentelemetry import trace as trace_api
        tracer, exporter = tracer_setup

        # Create parent Turn span
        turn_span = start_turn_span(tracer, 1)
        turn_ctx = trace_api.set_span_in_context(turn_span)

        request_data = MagicMock()
        request_data.model = "test-model"
        request_data.max_tokens = 100
        request_data.temperature = None
        request_data.top_p = None
        request_data.messages = []

        # Create LLM span as child of Turn
        llm_span = start_llm_span(tracer, request_data, "req-1", context=turn_ctx)
        llm_span.end()
        turn_span.end()

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        llm_finished = [s for s in spans if s.name == "gen_ai.chat"][0]
        turn_finished = [s for s in spans if s.name == "Turn 1"][0]

        # LLM span should be child of Turn span
        assert llm_finished.parent.span_id == turn_finished.context.span_id

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

        # Verify Langfuse usage_details JSON includes cache tokens
        usage_json = json.loads(attrs["langfuse.observation.usage_details"])
        assert usage_json["input"] == 100
        assert usage_json["output"] == 50
        assert usage_json["cache_read_input_tokens"] == 10
        assert usage_json["cache_creation_input_tokens"] == 5

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

    def test_start_tool_span_with_context(self, tracer_setup):
        """Test that tool span can be created as child of Turn span."""
        from app.tracing.spans import start_tool_span, start_turn_span
        from opentelemetry import trace as trace_api
        tracer, exporter = tracer_setup

        turn_span = start_turn_span(tracer, 1)
        turn_ctx = trace_api.set_span_in_context(turn_span)

        tool_span = start_tool_span(tracer, "read_file", "toolu_abc", context=turn_ctx)
        tool_span.end()
        turn_span.end()

        spans = exporter.get_finished_spans()
        tool_finished = [s for s in spans if "read_file" in s.name][0]
        turn_finished = [s for s in spans if "Turn" in s.name][0]

        # Tool span should be child of Turn span
        assert tool_finished.parent.span_id == turn_finished.context.span_id

    def test_start_turn_span(self, tracer_setup):
        """Test Turn span creation."""
        from app.tracing.spans import start_turn_span
        tracer, exporter = tracer_setup

        span = start_turn_span(tracer, 1)
        assert span is not None
        span.end()

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "Turn 1"

    def test_start_turn_span_none_tracer(self):
        from app.tracing.spans import start_turn_span
        assert start_turn_span(None, 1) is None

    def test_turn_based_hierarchy(self, tracer_setup):
        """Test the full Turn → gen_ai.chat + Tool span hierarchy."""
        from app.tracing.spans import start_turn_span, start_llm_span, start_tool_span
        from opentelemetry import trace as trace_api
        tracer, exporter = tracer_setup

        # Create root span
        root_span = tracer.start_span("trace_root")
        root_ctx = trace_api.set_span_in_context(root_span)

        # Create Turn span as child of root
        turn_span = start_turn_span(tracer, 1, context=root_ctx)
        turn_ctx = trace_api.set_span_in_context(turn_span)

        # Create gen_ai.chat as child of Turn
        request_data = MagicMock()
        request_data.model = "test-model"
        request_data.max_tokens = 100
        request_data.temperature = None
        request_data.top_p = None
        request_data.messages = []
        llm_span = start_llm_span(tracer, request_data, "req-1", context=turn_ctx)
        llm_span.end()

        # Create tool spans as children of Turn
        tool1 = start_tool_span(tracer, "Read", "toolu_1", context=turn_ctx)
        tool1.end()
        tool2 = start_tool_span(tracer, "Edit", "toolu_2", context=turn_ctx)
        tool2.end()

        turn_span.end()
        root_span.end()

        spans = exporter.get_finished_spans()
        assert len(spans) == 5  # root, turn, llm, tool1, tool2

        root_finished = [s for s in spans if s.name == "trace_root"][0]
        turn_finished = [s for s in spans if s.name == "Turn 1"][0]
        llm_finished = [s for s in spans if s.name == "gen_ai.chat"][0]
        tool_spans = [s for s in spans if "gen_ai.execute_tool" in s.name]

        # Turn is child of root
        assert turn_finished.parent.span_id == root_finished.context.span_id
        # LLM is child of Turn
        assert llm_finished.parent.span_id == turn_finished.context.span_id
        # Tools are children of Turn (siblings of LLM)
        for ts in tool_spans:
            assert ts.parent.span_id == turn_finished.context.span_id


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

    def test_accumulates_tool_use_blocks(self, mock_span):
        """Test that tool_use blocks are accumulated from streaming events."""
        from app.tracing.streaming import StreamingSpanAccumulator

        accumulator = StreamingSpanAccumulator(mock_span, MagicMock(), "req-1")

        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-1","usage":{"input_tokens":10}}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_abc","name":"read_file"}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\": \\"/tmp/"}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"test.txt\\"}"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":5}}\n\n',
        ]

        async def mock_generator():
            for e in events:
                yield e

        async def run():
            async for _ in accumulator.wrap_stream(mock_generator()):
                pass

        asyncio.run(run())

        assert len(accumulator.tool_use_blocks) == 1
        name, tid, input_str = accumulator.tool_use_blocks[0]
        assert name == "read_file"
        assert tid == "toolu_abc"
        assert '"path"' in input_str

    def test_turn_span_and_tool_spans_created(self):
        """Test that Turn span gets output set and tool spans are created."""
        from app.tracing.streaming import StreamingSpanAccumulator

        tracer, exporter, provider = _make_tracer_and_exporter()
        try:
            from opentelemetry import trace as trace_api

            # Create Turn span
            turn_span = tracer.start_span("Turn 1")
            turn_ctx = trace_api.set_span_in_context(turn_span)

            # Create LLM span (gen_ai.chat)
            llm_span = tracer.start_span("gen_ai.chat", context=turn_ctx)

            accumulator = StreamingSpanAccumulator(
                llm_span, MagicMock(), "req-1",
                trace_content=True,
                turn_span=turn_span,
                turn_ctx=turn_ctx,
                root_span=None,
                tracer=tracer,
            )

            events = [
                'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-1","usage":{"input_tokens":10}}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Analyzing..."}}\n\n',
                'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_xyz","name":"Bash"}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"command\\": \\"ls\\"}"}}\n\n',
                'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
                'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":20}}\n\n',
            ]

            async def mock_generator():
                for e in events:
                    yield e

            async def run():
                async for _ in accumulator.wrap_stream(mock_generator()):
                    pass

            asyncio.run(run())

            spans = exporter.get_finished_spans()
            span_names = [s.name for s in spans]

            # Should have: gen_ai.chat, tool span (Bash), Turn 1
            assert "gen_ai.chat" in span_names
            assert any("Bash" in name for name in span_names)
            assert "Turn 1" in span_names

            # Tool span should be child of Turn
            turn_finished = [s for s in spans if s.name == "Turn 1"][0]
            tool_finished = [s for s in spans if "Bash" in s.name][0]
            assert tool_finished.parent.span_id == turn_finished.context.span_id

        finally:
            provider.shutdown()

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

    def test_turn_span_ended_even_without_tools(self):
        """Test that Turn span is ended even when there are no tool blocks."""
        from app.tracing.streaming import StreamingSpanAccumulator

        mock_llm_span = MagicMock()
        mock_turn_span = MagicMock()

        accumulator = StreamingSpanAccumulator(
            mock_llm_span, MagicMock(), "req-1",
            turn_span=mock_turn_span,
        )

        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-1","usage":{"input_tokens":5}}}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n\n',
        ]

        async def mock_generator():
            for e in events:
                yield e

        async def run():
            async for _ in accumulator.wrap_stream(mock_generator()):
                pass

        asyncio.run(run())

        mock_llm_span.end.assert_called_once()
        mock_turn_span.end.assert_called_once()


class TestChatOnlySpanProcessor:
    """Test ChatOnlySpanProcessor filters out non-app spans."""

    def test_app_spans_are_exported(self):
        from app.tracing.provider import ChatOnlySpanProcessor
        exporter = InMemorySpanExporter()
        delegate = SimpleSpanProcessor(exporter)
        processor = ChatOnlySpanProcessor(delegate)

        provider = TracerProvider()
        provider.add_span_processor(processor)

        # Tracer with app prefix — should be exported
        tracer = provider.get_tracer("app.middleware.tracing")
        with tracer.start_as_current_span("proxy.request") as span:
            span.set_attribute("test", True)

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "proxy.request"
        provider.shutdown()

    def test_default_tracer_spans_are_exported(self):
        from app.tracing.provider import ChatOnlySpanProcessor
        exporter = InMemorySpanExporter()
        delegate = SimpleSpanProcessor(exporter)
        processor = ChatOnlySpanProcessor(delegate)

        provider = TracerProvider()
        provider.add_span_processor(processor)

        # Default tracer name — should be exported
        tracer = provider.get_tracer("anthropic-bedrock-proxy")
        with tracer.start_as_current_span("gen_ai.chat"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "gen_ai.chat"
        provider.shutdown()

    def test_third_party_spans_are_filtered(self):
        from app.tracing.provider import ChatOnlySpanProcessor
        exporter = InMemorySpanExporter()
        delegate = SimpleSpanProcessor(exporter)
        processor = ChatOnlySpanProcessor(delegate)

        provider = TracerProvider()
        provider.add_span_processor(processor)

        # Third-party tracer (e.g., Docker SDK, gRPC) — should be filtered out
        docker_tracer = provider.get_tracer("docker.api")
        with docker_tracer.start_as_current_span("moby.filesync.v1.FileSync/DiffCopy"):
            pass

        grpc_tracer = provider.get_tracer("opentelemetry.instrumentation.grpc")
        with grpc_tracer.start_as_current_span("grpc.client"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 0
        provider.shutdown()

    def test_mixed_spans_only_app_exported(self):
        from app.tracing.provider import ChatOnlySpanProcessor
        exporter = InMemorySpanExporter()
        delegate = SimpleSpanProcessor(exporter)
        processor = ChatOnlySpanProcessor(delegate)

        provider = TracerProvider()
        provider.add_span_processor(processor)

        # App span
        app_tracer = provider.get_tracer("app.middleware.tracing")
        with app_tracer.start_as_current_span("trace_root"):
            pass

        # Third-party span
        other_tracer = provider.get_tracer("some.other.library")
        with other_tracer.start_as_current_span("build ."):
            pass

        # Another app span
        proxy_tracer = provider.get_tracer("anthropic-bedrock-proxy")
        with proxy_tracer.start_as_current_span("gen_ai.chat"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        names = {s.name for s in spans}
        assert names == {"trace_root", "gen_ai.chat"}
        provider.shutdown()


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


class TestCurrentTurnMessages:
    """Test _extract_current_turn_messages logic."""

    def test_first_turn_no_assistant(self):
        from app.tracing.spans import _extract_current_turn_messages
        msgs = [{"role": "user", "content": "hello"}]
        result = _extract_current_turn_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_second_turn_returns_last_pair(self):
        from app.tracing.spans import _extract_current_turn_messages
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1"}]},
        ]
        result = _extract_current_turn_messages(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "user"

    def test_third_turn_skips_early_history(self):
        from app.tracing.spans import _extract_current_turn_messages
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "first response"},
            {"role": "user", "content": "follow up"},
            {"role": "assistant", "content": "second response"},
            {"role": "user", "content": "last question"},
        ]
        result = _extract_current_turn_messages(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "second response"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "last question"

    def test_empty_messages(self):
        from app.tracing.spans import _extract_current_turn_messages
        assert _extract_current_turn_messages([]) == []
        assert _extract_current_turn_messages(None) == []

    def test_works_with_pydantic_objects(self):
        from app.tracing.spans import _extract_current_turn_messages
        msg1 = MagicMock()
        msg1.role = "user"
        msg2 = MagicMock()
        msg2.role = "assistant"
        msg3 = MagicMock()
        msg3.role = "user"
        result = _extract_current_turn_messages([msg1, msg2, msg3])
        assert len(result) == 2
        assert result[0].role == "assistant"
        assert result[1].role == "user"


class TestExtractTraceInput:
    """Test _extract_trace_input from messages.py."""

    def _make_tool(self, name, tool_type=None):
        """Create a mock tool with name as a regular attribute (not MagicMock's special 'name')."""
        tool = MagicMock(spec=[])
        tool.name = name
        tool.type = tool_type
        return tool

    def test_with_system_and_tools_and_user(self):
        from app.api.messages import _extract_trace_input
        req = MagicMock()
        req.system = [MagicMock(text="You are a helpful assistant")]
        tool_read = self._make_tool("Read")
        tool_read.description = "Read a file"
        tool_read.input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        tool_edit = self._make_tool("Edit")
        tool_edit.description = None
        tool_edit.input_schema = None
        req.tools = [tool_read, tool_edit]
        msg = MagicMock()
        msg.role = "user"
        msg.content = "Help me refactor"
        req.messages = [msg]

        result = json.loads(_extract_trace_input(req))
        assert result["system"] == "You are a helpful assistant"
        assert len(result["tools"]) == 2
        assert result["tools"][0]["name"] == "Read"
        assert result["tools"][0]["description"] == "Read a file"
        assert result["tools"][0]["input_schema"] == {"type": "object", "properties": {"path": {"type": "string"}}}
        assert result["tools"][1] == {"name": "Edit"}
        assert result["user_message"] == "Help me refactor"

    def test_system_as_string(self):
        from app.api.messages import _extract_trace_input
        req = MagicMock()
        req.system = "Be concise"
        req.tools = None
        req.messages = []

        result = json.loads(_extract_trace_input(req))
        assert result["system"] == "Be concise"
        assert "tools" not in result
        assert "user_message" not in result

    def test_special_tools_fallback_to_type(self):
        from app.api.messages import _extract_trace_input
        req = MagicMock()
        req.system = None
        req.tools = [
            self._make_tool("Read"),
            self._make_tool(None, tool_type="code_execution"),
        ]
        req.messages = []

        result = json.loads(_extract_trace_input(req))
        assert result["tools"][0] == {"name": "Read"}
        assert result["tools"][1] == {"type": "code_execution"}

    def test_no_system_no_tools_no_messages(self):
        from app.api.messages import _extract_trace_input
        req = MagicMock()
        req.system = None
        req.tools = None
        req.messages = []

        result = _extract_trace_input(req)
        assert result is None

    def test_user_message_with_list_content(self):
        from app.api.messages import _extract_trace_input
        req = MagicMock()
        req.system = None
        req.tools = None
        block = MagicMock()
        block.type = "text"
        block.text = "Hello world"
        msg = MagicMock()
        msg.role = "user"
        msg.content = [block]
        req.messages = [msg]

        result = json.loads(_extract_trace_input(req))
        assert result["user_message"] == "Hello world"


class TestExtractLastUserText:
    """Test _extract_last_user_text."""

    def test_simple_string_content(self):
        from app.api.messages import _extract_last_user_text
        msgs = [
            MagicMock(role="user", content="first"),
            MagicMock(role="assistant", content="reply"),
            MagicMock(role="user", content="second"),
        ]
        assert _extract_last_user_text(msgs) == "second"

    def test_no_user_messages(self):
        from app.api.messages import _extract_last_user_text
        msgs = [MagicMock(role="assistant", content="reply")]
        assert _extract_last_user_text(msgs) is None

    def test_empty_list(self):
        from app.api.messages import _extract_last_user_text
        assert _extract_last_user_text([]) is None


class TestExtractResponseText:
    """Test _extract_response_text."""

    def test_text_response(self):
        from app.api.messages import _extract_response_text
        response = MagicMock()
        block = MagicMock()
        block.type = "text"
        block.text = "Hello!"
        response.content = [block]
        assert _extract_response_text(response) == "Hello!"

    def test_tool_use_response(self):
        from app.api.messages import _extract_response_text
        response = MagicMock()
        block = MagicMock()
        block.type = "tool_use"
        block.name = "Read"
        block.input = {"path": "/tmp/test.py"}
        response.content = [block]
        result = _extract_response_text(response)
        assert "tool_use" in result
        assert "Read" in result

    def test_empty_content(self):
        from app.api.messages import _extract_response_text
        response = MagicMock()
        response.content = []
        assert _extract_response_text(response) is None
