"""
Unit tests for converter handling of web_fetch_tool_result blocks.

Tests that the AnthropicToBedrockConverter correctly converts
web_fetch_tool_result and web_fetch_tool_error blocks in multi-turn
messages to Bedrock toolResult format.
"""
from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter


class TestWebFetchToolResultConversion:
    """Tests for web_fetch_tool_result handling in _convert_content_blocks."""

    def setup_method(self):
        """Setup test fixtures."""
        self.converter = AnthropicToBedrockConverter()

    def test_web_fetch_tool_result_in_message(self):
        """web_fetch_tool_result block gets converted to Bedrock toolResult."""
        content_blocks = [
            {
                "type": "web_fetch_tool_result",
                "tool_use_id": "srvtoolu_abc123",
                "content": {
                    "type": "web_fetch_result",
                    "url": "https://example.com",
                    "content": {
                        "type": "document",
                        "source": {
                            "type": "text",
                            "media_type": "text/html",
                            "data": "Page content here",
                        },
                        "title": "Example Page",
                    },
                    "retrieved_at": "2026-03-03T00:00:00Z",
                },
            }
        ]

        bedrock_content = self.converter._convert_content_blocks(content_blocks)

        assert len(bedrock_content) == 1

        tool_result = bedrock_content[0]
        assert "toolResult" in tool_result
        assert tool_result["toolResult"]["toolUseId"] == "srvtoolu_abc123"
        assert tool_result["toolResult"]["status"] == "success"

        # The result text should contain the URL, title, and content
        result_text = tool_result["toolResult"]["content"][0]["text"]
        assert "Example Page" in result_text
        assert "https://example.com" in result_text
        assert "Page content here" in result_text

    def test_web_fetch_tool_error_in_message(self):
        """web_fetch_tool_error gets converted correctly to Bedrock toolResult."""
        content_blocks = [
            {
                "type": "web_fetch_tool_result",
                "tool_use_id": "srvtoolu_xyz789",
                "content": {
                    "type": "web_fetch_tool_error",
                    "error_code": "url_not_accessible",
                },
            }
        ]

        bedrock_content = self.converter._convert_content_blocks(content_blocks)

        assert len(bedrock_content) == 1

        tool_result = bedrock_content[0]
        assert "toolResult" in tool_result
        assert tool_result["toolResult"]["toolUseId"] == "srvtoolu_xyz789"
        assert tool_result["toolResult"]["status"] == "success"

        result_text = tool_result["toolResult"]["content"][0]["text"]
        assert "url_not_accessible" in result_text

    def test_web_fetch_mixed_content_blocks(self):
        """web_fetch_tool_result alongside regular text blocks."""
        content_blocks = [
            {"type": "text", "text": "Here are the results:"},
            {
                "type": "web_fetch_tool_result",
                "tool_use_id": "srvtoolu_mixed1",
                "content": {
                    "type": "web_fetch_result",
                    "url": "https://docs.python.org",
                    "content": {
                        "type": "document",
                        "source": {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": "Python docs content",
                        },
                        "title": "Python Docs",
                    },
                    "retrieved_at": "2026-03-03T00:00:00Z",
                },
            },
        ]

        bedrock_content = self.converter._convert_content_blocks(content_blocks)

        assert len(bedrock_content) == 2

        # First block should be text
        assert "text" in bedrock_content[0]
        assert bedrock_content[0]["text"] == "Here are the results:"

        # Second block should be toolResult
        assert "toolResult" in bedrock_content[1]
        assert bedrock_content[1]["toolResult"]["toolUseId"] == "srvtoolu_mixed1"

    def test_web_fetch_tool_error_unknown_content(self):
        """web_fetch_tool_result with unrecognized content type falls back to str()."""
        content_blocks = [
            {
                "type": "web_fetch_tool_result",
                "tool_use_id": "srvtoolu_fallback1",
                "content": {
                    "type": "some_unknown_type",
                    "data": "arbitrary",
                },
            }
        ]

        bedrock_content = self.converter._convert_content_blocks(content_blocks)

        assert len(bedrock_content) == 1
        tool_result = bedrock_content[0]
        assert "toolResult" in tool_result
        assert tool_result["toolResult"]["toolUseId"] == "srvtoolu_fallback1"
        # Should fall through to str(wf_content) for unrecognized types
        result_text = tool_result["toolResult"]["content"][0]["text"]
        assert "some_unknown_type" in result_text
