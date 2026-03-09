"""
Tests for the OpenAI-compatible service (app/services/openai_compat_service.py).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.anthropic import MessageRequest, MessageResponse


@pytest.fixture
def mock_settings():
    """Patch settings for OpenAI-compat service."""
    with patch("app.services.openai_compat_service.settings") as mock_s:
        mock_s.openai_api_key = "test-api-key"
        mock_s.openai_base_url = "https://bedrock-mantle.us-east-1.api.aws/v1"
        mock_s.bedrock_timeout = 300
        mock_s.bedrock_thread_pool_size = 4
        mock_s.bedrock_semaphore_size = 4
        mock_s.enable_extended_thinking = True
        mock_s.openai_compat_thinking_high_threshold = 10000
        mock_s.openai_compat_thinking_medium_threshold = 4000
        yield mock_s


@pytest.fixture
def mock_openai_client():
    """Patch the OpenAI client class."""
    with patch("app.services.openai_compat_service.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_client


@pytest.fixture
def service(mock_settings, mock_openai_client):
    """Create an OpenAICompatService instance with mocked dependencies."""
    from app.services.openai_compat_service import OpenAICompatService
    return OpenAICompatService()


def _make_request(**kwargs) -> MessageRequest:
    """Helper to create a minimal MessageRequest."""
    defaults = {
        "model": "us.amazon.nova-pro-v1:0",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    defaults.update(kwargs)
    return MessageRequest(**defaults)


class TestOpenAICompatServiceInit:
    """Test service initialization."""

    def test_initializes_openai_client(self, mock_settings, mock_openai_client):
        """Service should create an OpenAI client with correct parameters."""
        with patch("app.services.openai_compat_service.OpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            from app.services.openai_compat_service import OpenAICompatService
            svc = OpenAICompatService()

            mock_cls.assert_called_once_with(
                api_key="test-api-key",
                base_url="https://bedrock-mantle.us-east-1.api.aws/v1",
                timeout=300,
            )

    def test_initializes_converters(self, service):
        """Service should have request and response converters."""
        from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
        from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter

        assert isinstance(service.request_converter, AnthropicToOpenAIConverter)
        assert isinstance(service.response_converter, OpenAIToAnthropicConverter)


class TestInvokeModelSync:
    """Test synchronous model invocation."""

    def test_converts_request_and_calls_api(self, service, mock_openai_client):
        """invoke_model_sync should convert request and call OpenAI client."""
        # Set up mock response
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! How can I help you?",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "total_tokens": 18,
            },
        }
        mock_openai_client.chat.completions.create.return_value = mock_response

        request = _make_request()
        result = service.invoke_model_sync(request, request_id="test-123")

        # Verify OpenAI client was called
        mock_openai_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai_client.chat.completions.create.call_args
        # stream should be False for sync
        assert call_kwargs[1].get("stream") is False or call_kwargs.kwargs.get("stream") is False

        # Verify result is a MessageResponse
        assert isinstance(result, MessageResponse)
        assert result.role == "assistant"
        assert result.type == "message"
        assert result.model == "us.amazon.nova-pro-v1:0"

    def test_returns_correct_content(self, service, mock_openai_client):
        """invoke_model_sync should return converted content."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Test response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_openai_client.chat.completions.create.return_value = mock_response

        request = _make_request()
        result = service.invoke_model_sync(request)

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "Test response"
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 5
        assert result.usage.output_tokens == 3

    def test_handles_tool_calls(self, service, mock_openai_client):
        """invoke_model_sync should handle tool call responses."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"location": "SF"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 10},
        }
        mock_openai_client.chat.completions.create.return_value = mock_response

        request = _make_request()
        result = service.invoke_model_sync(request)

        assert result.stop_reason == "tool_use"
        # Should have a tool_use content block
        tool_blocks = [c for c in result.content if c.type == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "get_weather"
        assert tool_blocks[0].input == {"location": "SF"}


class TestFormatSSE:
    """Test SSE formatting."""

    def test_format_sse_event(self, service):
        """_format_sse_event should produce correct SSE format."""
        event = {"type": "message_start", "message": {"id": "msg_123"}}
        result = service._format_sse_event(event)

        assert result.startswith("event: message_start\n")
        assert "data: " in result
        assert result.endswith("\n\n")

        # Parse the data line
        lines = result.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data: ")][0]
        data_json = json.loads(data_line[len("data: "):])
        assert data_json["type"] == "message_start"
