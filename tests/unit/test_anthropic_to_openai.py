"""
Tests for Anthropic → OpenAI Chat Completions API converter.
"""
import json
from unittest.mock import patch

import pytest

from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
from app.schemas.anthropic import MessageRequest


@pytest.fixture
def converter():
    return AnthropicToOpenAIConverter()


# ---------------------------------------------------------------------------
# Basic text message
# ---------------------------------------------------------------------------

def test_simple_text_message(converter):
    """Simple user text message converts correctly."""
    request = MessageRequest(
        model="meta.llama3-70b-instruct-v1:0",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello!"}],
    )
    result = converter.convert_request(request)

    assert result["model"] == "meta.llama3-70b-instruct-v1:0"
    assert result["max_tokens"] == 1024
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "user"
    # content validator converts string to list, then converter simplifies single text back
    assert result["messages"][0]["content"] == "Hello!"


# ---------------------------------------------------------------------------
# System message
# ---------------------------------------------------------------------------

def test_system_message_string(converter):
    """String system prompt becomes a system role message."""
    request = MessageRequest(
        model="model-x",
        max_tokens=512,
        system="You are helpful.",
        messages=[{"role": "user", "content": "Hi"}],
    )
    result = converter.convert_request(request)

    assert result["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert result["messages"][1]["role"] == "user"


def test_system_message_array_of_blocks(converter):
    """Array-of-text-blocks system prompt is joined with newlines."""
    request = MessageRequest(
        model="model-x",
        max_tokens=512,
        system=[
            {"type": "text", "text": "You are helpful."},
            {"type": "text", "text": "Be concise."},
        ],
        messages=[{"role": "user", "content": "Hi"}],
    )
    result = converter.convert_request(request)

    assert result["messages"][0] == {
        "role": "system",
        "content": "You are helpful.\nBe concise.",
    }


# ---------------------------------------------------------------------------
# Optional parameters: temperature, top_p, stop_sequences
# ---------------------------------------------------------------------------

def test_temperature(converter):
    request = MessageRequest(
        model="m", max_tokens=100, temperature=0.7,
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert result["temperature"] == 0.7


def test_top_p(converter):
    request = MessageRequest(
        model="m", max_tokens=100, top_p=0.9,
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert result["top_p"] == 0.9


def test_stop_sequences(converter):
    request = MessageRequest(
        model="m", max_tokens=100, stop_sequences=["STOP", "END"],
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert result["stop"] == ["STOP", "END"]


def test_optional_params_absent(converter):
    """When optional params are None they should not appear in the result."""
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert "temperature" not in result
    assert "top_p" not in result
    assert "stop" not in result


# ---------------------------------------------------------------------------
# Stream flag
# ---------------------------------------------------------------------------

def test_stream_true(converter):
    request = MessageRequest(
        model="m", max_tokens=100, stream=True,
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert result["stream"] is True


def test_stream_false(converter):
    request = MessageRequest(
        model="m", max_tokens=100, stream=False,
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert result["stream"] is False


# ---------------------------------------------------------------------------
# Tools conversion
# ---------------------------------------------------------------------------

def test_tools_conversion(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tools=[
            {
                "name": "get_weather",
                "description": "Get the weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ],
    )
    result = converter.convert_request(request)
    assert len(result["tools"]) == 1
    tool = result["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "get_weather"
    assert tool["function"]["description"] == "Get the weather"
    assert tool["function"]["parameters"]["properties"]["city"]["type"] == "string"


def test_server_tools_filtered_out(converter):
    """Server-side tools (web_search_*, web_fetch_*, code_execution, computer_*) are skipped."""
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tools=[
            {"name": "web_search_20250305", "description": "search", "input_schema": {"type": "object"}},
            {"name": "web_fetch_20260209", "description": "fetch", "input_schema": {"type": "object"}},
            {"name": "code_execution", "type": "code_execution_20250825"},
            {"name": "my_tool", "description": "custom", "input_schema": {"type": "object"}},
            {"name": "screen", "type": "computer_20241022", "description": "computer", "input_schema": {"type": "object"}},
        ],
    )
    result = converter.convert_request(request)
    assert len(result["tools"]) == 1
    assert result["tools"][0]["function"]["name"] == "my_tool"


# ---------------------------------------------------------------------------
# Tool choice
# ---------------------------------------------------------------------------

def test_tool_choice_auto_dict(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tool_choice={"type": "auto"},
    )
    result = converter.convert_request(request)
    assert result["tool_choice"] == "auto"


def test_tool_choice_any_dict(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tool_choice={"type": "any"},
    )
    result = converter.convert_request(request)
    assert result["tool_choice"] == "required"


def test_tool_choice_specific_tool(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tool_choice={"type": "tool", "name": "get_weather"},
    )
    result = converter.convert_request(request)
    assert result["tool_choice"] == {
        "type": "function",
        "function": {"name": "get_weather"},
    }


def test_tool_choice_none(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tool_choice={"type": "none"},
    )
    result = converter.convert_request(request)
    assert result["tool_choice"] == "none"


def test_tool_choice_string_auto(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tool_choice="auto",
    )
    result = converter.convert_request(request)
    assert result["tool_choice"] == "auto"


def test_tool_choice_string_any(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        tool_choice="any",
    )
    result = converter.convert_request(request)
    assert result["tool_choice"] == "required"


# ---------------------------------------------------------------------------
# Assistant message with tool_use → tool_calls
# ---------------------------------------------------------------------------

def test_assistant_tool_use(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "call_123",
                        "name": "get_weather",
                        "input": {"city": "Tokyo"},
                    },
                ],
            },
        ],
    )
    result = converter.convert_request(request)
    assistant_msg = result["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "Let me check."
    assert len(assistant_msg["tool_calls"]) == 1
    tc = assistant_msg["tool_calls"][0]
    assert tc["id"] == "call_123"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Tokyo"}


def test_assistant_only_tool_use_no_text(converter):
    """Assistant message with only tool_use blocks has content=None."""
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "fn",
                        "input": {},
                    },
                ],
            },
        ],
    )
    result = converter.convert_request(request)
    assistant_msg = result["messages"][1]
    assert assistant_msg["content"] is None
    assert len(assistant_msg["tool_calls"]) == 1


# ---------------------------------------------------------------------------
# User message with tool_result → tool messages
# ---------------------------------------------------------------------------

def test_tool_result_string_content(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "fn", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": "result text",
                    },
                ],
            },
        ],
    )
    result = converter.convert_request(request)
    tool_msg = result["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["content"] == "result text"


def test_tool_result_with_text_blocks(converter):
    """Tool result with list-of-text content blocks joins text."""
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "fn", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": [
                            {"type": "text", "text": "line 1"},
                            {"type": "text", "text": "line 2"},
                        ],
                    },
                ],
            },
        ],
    )
    result = converter.convert_request(request)
    tool_msg = result["messages"][2]
    assert tool_msg["content"] == "line 1\nline 2"


def test_mixed_tool_result_and_text(converter):
    """User message with both text and tool_result blocks produces multiple messages."""
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "fn", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here's the result:"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": "done",
                    },
                ],
            },
        ],
    )
    result = converter.convert_request(request)
    # Should have: user, assistant, user (text), tool (result)
    assert result["messages"][2]["role"] == "user"
    assert result["messages"][2]["content"] == "Here's the result:"
    assert result["messages"][3]["role"] == "tool"
    assert result["messages"][3]["tool_call_id"] == "call_1"


# ---------------------------------------------------------------------------
# Thinking → reasoning (only for kimi-k2.5 models)
# ---------------------------------------------------------------------------

@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_high(mock_settings, converter):
    mock_settings.enable_extended_thinking = True
    mock_settings.openai_compat_thinking_high_threshold = 10000
    mock_settings.openai_compat_thinking_medium_threshold = 4000

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 15000},
    )
    result = converter.convert_request(request)
    assert result["reasoning_effort"] == "high"
    assert result["extra_body"] == {"include_reasoning": True}


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_medium(mock_settings, converter):
    mock_settings.enable_extended_thinking = True
    mock_settings.openai_compat_thinking_high_threshold = 10000
    mock_settings.openai_compat_thinking_medium_threshold = 4000

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 5000},
    )
    result = converter.convert_request(request)
    assert result["reasoning_effort"] == "medium"
    assert result["extra_body"] == {"include_reasoning": True}


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_low(mock_settings, converter):
    mock_settings.enable_extended_thinking = True
    mock_settings.openai_compat_thinking_high_threshold = 10000
    mock_settings.openai_compat_thinking_medium_threshold = 4000

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 2000},
    )
    result = converter.convert_request(request)
    assert result["reasoning_effort"] == "low"
    assert result["extra_body"] == {"include_reasoning": True}


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_at_high_boundary(mock_settings, converter):
    """budget_tokens exactly at high threshold → high."""
    mock_settings.enable_extended_thinking = True
    mock_settings.openai_compat_thinking_high_threshold = 10000
    mock_settings.openai_compat_thinking_medium_threshold = 4000

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 10000},
    )
    result = converter.convert_request(request)
    assert result["reasoning_effort"] == "high"
    assert result["extra_body"] == {"include_reasoning": True}


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_at_medium_boundary(mock_settings, converter):
    """budget_tokens exactly at medium threshold → medium."""
    mock_settings.enable_extended_thinking = True
    mock_settings.openai_compat_thinking_high_threshold = 10000
    mock_settings.openai_compat_thinking_medium_threshold = 4000

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 4000},
    )
    result = converter.convert_request(request)
    assert result["reasoning_effort"] == "medium"
    assert result["extra_body"] == {"include_reasoning": True}


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_not_added_for_non_kimi(mock_settings, converter):
    """Non kimi-k2.5 models should NOT get reasoning_effort even with thinking enabled."""
    mock_settings.enable_extended_thinking = True
    mock_settings.openai_compat_thinking_high_threshold = 10000
    mock_settings.openai_compat_thinking_medium_threshold = 4000

    request = MessageRequest(
        model="us.deepseek.r1-v1:0", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 15000},
    )
    result = converter.convert_request(request)
    assert "reasoning_effort" not in result
    assert "extra_body" not in result


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_disabled_in_settings(mock_settings, converter):
    """When extended thinking is disabled in settings, reasoning is not added."""
    mock_settings.enable_extended_thinking = False

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "enabled", "budget_tokens": 15000},
    )
    result = converter.convert_request(request)
    assert "reasoning_effort" not in result


@patch("app.converters.anthropic_to_openai.settings")
def test_thinking_type_not_enabled(mock_settings, converter):
    """When thinking type is not 'enabled', reasoning is not added."""
    mock_settings.enable_extended_thinking = True

    request = MessageRequest(
        model="moonshotai.kimi-k2.5", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "disabled"},
    )
    result = converter.convert_request(request)
    assert "reasoning_effort" not in result


# ---------------------------------------------------------------------------
# Image content → image_url
# ---------------------------------------------------------------------------

def test_image_content(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            }
        ],
    )
    result = converter.convert_request(request)
    user_msg = result["messages"][0]
    assert user_msg["role"] == "user"
    content = user_msg["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "Describe this image."}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,iVBORw0KGgo="


# ---------------------------------------------------------------------------
# No tools → no tools key
# ---------------------------------------------------------------------------

def test_no_tools_no_key(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[{"role": "user", "content": "x"}],
    )
    result = converter.convert_request(request)
    assert "tools" not in result
    assert "tool_choice" not in result


# ---------------------------------------------------------------------------
# Multi-turn conversation
# ---------------------------------------------------------------------------

def test_multi_turn(converter):
    request = MessageRequest(
        model="m", max_tokens=100,
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]},
            {"role": "user", "content": "How are you?"},
        ],
    )
    result = converter.convert_request(request)
    assert len(result["messages"]) == 3
    assert result["messages"][0]["role"] == "user"
    assert result["messages"][1]["role"] == "assistant"
    assert result["messages"][1]["content"] == "Hi there!"
    assert result["messages"][2]["role"] == "user"
