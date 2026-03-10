"""Tests for OpenAI to Anthropic response converter."""
import pytest

from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter


@pytest.fixture
def converter():
    return OpenAIToAnthropicConverter()


class TestConvertResponse:
    """Tests for non-streaming response conversion."""

    def test_simple_text_response(self, converter):
        openai_response = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello, world!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        result = converter.convert_response(openai_response, "gpt-4", "msg_001")

        assert result.id == "msg_001"
        assert result.type == "message"
        assert result.role == "assistant"
        assert result.model == "gpt-4"
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "Hello, world!"
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    def test_stop_reason_stop(self, converter):
        resp = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        result = converter.convert_response(resp, "m", "msg_1")
        assert result.stop_reason == "end_turn"

    def test_stop_reason_length(self, converter):
        resp = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        result = converter.convert_response(resp, "m", "msg_1")
        assert result.stop_reason == "max_tokens"

    def test_stop_reason_tool_calls(self, converter):
        resp = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city": "NYC"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        }
        result = converter.convert_response(resp, "m", "msg_1")
        assert result.stop_reason == "tool_use"

    def test_tool_calls_in_response(self, converter):
        openai_response = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"location": "Paris", "units": "celsius"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15},
        }

        result = converter.convert_response(openai_response, "gpt-4", "msg_002")

        assert len(result.content) == 1
        tool_block = result.content[0]
        assert tool_block.type == "tool_use"
        assert tool_block.id == "call_abc"
        assert tool_block.name == "get_weather"
        assert tool_block.input == {"location": "Paris", "units": "celsius"}
        assert result.stop_reason == "tool_use"

    def test_text_and_tool_calls_combined(self, converter):
        openai_response = {
            "choices": [
                {
                    "message": {
                        "content": "Let me check the weather for you.",
                        "tool_calls": [
                            {
                                "id": "call_xyz",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city": "London"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 20},
        }

        result = converter.convert_response(openai_response, "gpt-4", "msg_003")

        assert len(result.content) == 2
        assert result.content[0].type == "text"
        assert result.content[0].text == "Let me check the weather for you."
        assert result.content[1].type == "tool_use"
        assert result.content[1].id == "call_xyz"
        assert result.content[1].name == "get_weather"
        assert result.content[1].input == {"city": "London"}

    def test_empty_content_produces_empty_text_block(self, converter):
        openai_response = {
            "choices": [{"message": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0},
        }
        result = converter.convert_response(openai_response, "m", "msg_1")
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == ""


class TestStreamingEvents:
    """Tests for streaming helper methods."""

    def test_create_message_start_event(self, converter):
        event = converter.create_message_start_event("msg_100", "gpt-4")

        assert event["type"] == "message_start"
        msg = event["message"]
        assert msg["id"] == "msg_100"
        assert msg["type"] == "message"
        assert msg["role"] == "assistant"
        assert msg["content"] == []
        assert msg["model"] == "gpt-4"
        assert msg["usage"] == {"input_tokens": 0, "output_tokens": 0}

    def test_create_message_stop_event(self, converter):
        event = converter.create_message_stop_event()
        assert event == {"type": "message_stop"}


class TestErrorEvent:
    """Tests for error event creation."""

    def test_create_error_event_400(self, converter):
        event = converter.create_error_event("400", "Bad request")
        assert event["type"] == "error"
        assert event["error"]["type"] == "invalid_request_error"
        assert event["error"]["message"] == "Bad request"

    def test_create_error_event_429(self, converter):
        event = converter.create_error_event("429", "Rate limited")
        assert event["error"]["type"] == "rate_limit_error"

    def test_create_error_event_unknown_code(self, converter):
        event = converter.create_error_event("502", "Bad gateway")
        assert event["error"]["type"] == "api_error"
