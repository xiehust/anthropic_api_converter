"""
Unit tests for format converters.

Tests conversion between Anthropic and Bedrock formats.
"""
import pytest
from unittest.mock import patch

from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.converters.bedrock_to_anthropic import BedrockToAnthropicConverter
from app.schemas.anthropic import MessageRequest, TextContent


class TestAnthropicToBedrockConverter:
    """Test Anthropic to Bedrock conversion."""

    def setup_method(self):
        """Setup test fixtures."""
        self.converter = AnthropicToBedrockConverter()

    def test_convert_simple_message(self):
        """Test conversion of simple text message."""
        request = MessageRequest(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": "Hello, world!",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        assert "modelId" in bedrock_request
        assert "messages" in bedrock_request
        assert "inferenceConfig" in bedrock_request
        assert bedrock_request["inferenceConfig"]["maxTokens"] == 1024
        assert len(bedrock_request["messages"]) == 1
        assert bedrock_request["messages"][0]["role"] == "user"

    def test_convert_model_id(self):
        """Test model ID conversion."""
        anthropic_model = "claude-3-5-sonnet-20241022"
        bedrock_model = self.converter._convert_model_id(anthropic_model)

        assert bedrock_model.startswith("anthropic.claude")

    def test_convert_with_system_message(self):
        """Test conversion with system message."""
        request = MessageRequest(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            system="You are a helpful assistant.",
            messages=[
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        assert "system" in bedrock_request
        assert len(bedrock_request["system"]) == 1
        assert bedrock_request["system"][0]["text"] == "You are a helpful assistant."

    def test_convert_with_tools(self):
        """Test conversion with tool definitions."""
        request = MessageRequest(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            tools=[
                {
                    "name": "get_weather",
                    "description": "Get weather",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"},
                        },
                        "required": ["location"],
                    },
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": "What's the weather?",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        assert "toolConfig" in bedrock_request
        assert "tools" in bedrock_request["toolConfig"]
        assert len(bedrock_request["toolConfig"]["tools"]) == 1

    def test_anthropic_beta_features_enabled_for_claude(self):
        """Test that anthropic_beta features are added for Claude models when enabled."""
        request = MessageRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        # Both features should be enabled by default
        assert "additionalModelRequestFields" in bedrock_request
        assert "anthropic_beta" in bedrock_request["additionalModelRequestFields"]
        beta_features = bedrock_request["additionalModelRequestFields"]["anthropic_beta"]
        assert "fine-grained-tool-streaming-2025-05-14" in beta_features
        assert "interleaved-thinking-2025-05-14" in beta_features

    @patch("app.converters.anthropic_to_bedrock.settings")
    def test_anthropic_beta_features_disabled(self, mock_settings):
        """Test that anthropic_beta features are not added when disabled."""
        mock_settings.fine_grained_tool_streaming_enabled = False
        mock_settings.interleaved_thinking_enabled = False
        mock_settings.default_model_mapping = {
            "claude-sonnet-4-5-20250929": "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        }

        request = MessageRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        # anthropic_beta should not be present when both features are disabled
        if "additionalModelRequestFields" in bedrock_request:
            assert "anthropic_beta" not in bedrock_request["additionalModelRequestFields"]

    @patch("app.converters.anthropic_to_bedrock.settings")
    def test_anthropic_beta_only_fine_grained_tool_streaming(self, mock_settings):
        """Test that only fine-grained tool streaming is added when enabled."""
        mock_settings.fine_grained_tool_streaming_enabled = True
        mock_settings.interleaved_thinking_enabled = False
        mock_settings.default_model_mapping = {
            "claude-sonnet-4-5-20250929": "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        }

        request = MessageRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        assert "additionalModelRequestFields" in bedrock_request
        assert "anthropic_beta" in bedrock_request["additionalModelRequestFields"]
        beta_features = bedrock_request["additionalModelRequestFields"]["anthropic_beta"]
        assert "fine-grained-tool-streaming-2025-05-14" in beta_features
        assert "interleaved-thinking-2025-05-14" not in beta_features

    def test_anthropic_beta_not_added_for_non_claude_models(self):
        """Test that anthropic_beta features are not added for non-Claude models."""
        request = MessageRequest(
            model="meta.llama3-8b-instruct-v1:0",  # Non-Claude model
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        )

        bedrock_request = self.converter.convert_request(request)

        # anthropic_beta should not be present for non-Claude models
        if "additionalModelRequestFields" in bedrock_request:
            assert "anthropic_beta" not in bedrock_request["additionalModelRequestFields"]


class TestBedrockToAnthropicConverter:
    """Test Bedrock to Anthropic conversion."""

    def setup_method(self):
        """Setup test fixtures."""
        self.converter = BedrockToAnthropicConverter()

    def test_convert_simple_response(self):
        """Test conversion of simple response."""
        bedrock_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Hello! How can I help you?"}],
                }
            },
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 20,
                "totalTokens": 30,
            },
        }

        anthropic_response = self.converter.convert_response(
            bedrock_response,
            "claude-3-5-sonnet-20241022",
            "msg_123",
        )

        assert anthropic_response.id == "msg_123"
        assert anthropic_response.role == "assistant"
        assert anthropic_response.model == "claude-3-5-sonnet-20241022"
        assert anthropic_response.stop_reason == "end_turn"
        assert anthropic_response.usage.input_tokens == 10
        assert anthropic_response.usage.output_tokens == 20

    def test_convert_stop_reason(self):
        """Test stop reason conversion."""
        test_cases = [
            ("end_turn", "end_turn"),
            ("max_tokens", "max_tokens"),
            ("stop_sequence", "stop_sequence"),
            ("tool_use", "tool_use"),
            ("content_filtered", "end_turn"),
        ]

        for bedrock_reason, expected_reason in test_cases:
            result = self.converter._convert_stop_reason(bedrock_reason)
            assert result == expected_reason

    def test_convert_response_with_empty_text_block_before_tool_use(self):
        """Test that empty text blocks before tool_use are filtered out.

        When stop_reason is tool_use, Bedrock often returns:
        [{"text": ""}, {"toolUse": {...}}]

        This test ensures empty text blocks are filtered out.
        """
        bedrock_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": ""},  # Empty text block
                        {
                            "toolUse": {
                                "toolUseId": "toolu_123",
                                "name": "get_weather",
                                "input": {"location": "San Francisco"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 20,
                "totalTokens": 30,
            },
        }

        anthropic_response = self.converter.convert_response(
            bedrock_response,
            "claude-3-5-sonnet-20241022",
            "msg_123",
        )

        # Should only have 1 content block (tool_use), empty text block filtered out
        assert len(anthropic_response.content) == 1
        assert anthropic_response.content[0].type == "tool_use"
        assert anthropic_response.content[0].id == "toolu_123"
        assert anthropic_response.content[0].name == "get_weather"
        assert anthropic_response.stop_reason == "tool_use"

    def test_convert_response_with_non_empty_text_and_tool_use(self):
        """Test that non-empty text blocks are kept before tool_use."""
        bedrock_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "I'll check the weather for you."},  # Non-empty text
                        {
                            "toolUse": {
                                "toolUseId": "toolu_456",
                                "name": "get_weather",
                                "input": {"location": "New York"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {
                "inputTokens": 15,
                "outputTokens": 25,
                "totalTokens": 40,
            },
        }

        anthropic_response = self.converter.convert_response(
            bedrock_response,
            "claude-3-5-sonnet-20241022",
            "msg_456",
        )

        # Should have 2 content blocks (text + tool_use)
        assert len(anthropic_response.content) == 2
        assert anthropic_response.content[0].type == "text"
        assert anthropic_response.content[0].text == "I'll check the weather for you."
        assert anthropic_response.content[1].type == "tool_use"
        assert anthropic_response.content[1].id == "toolu_456"
        assert anthropic_response.stop_reason == "tool_use"
