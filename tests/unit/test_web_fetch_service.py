"""
Unit tests for WebFetchService.

Tests the static and instance methods of WebFetchService defined in
app/services/web_fetch_service.py.
"""
from unittest.mock import patch

from app.schemas.anthropic import MessageRequest
from app.services.web_fetch_service import WebFetchService


class TestIsWebFetchRequest:
    """Tests for WebFetchService.is_web_fetch_request()."""

    @patch("app.services.web_fetch_service.settings")
    def test_is_web_fetch_request_true(self, mock_settings):
        """Detects web_fetch_20250910 tool in request."""
        mock_settings.enable_web_fetch = True
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "web_fetch_20250910", "name": "web_fetch"}],
        )
        assert WebFetchService.is_web_fetch_request(request) is True

    @patch("app.services.web_fetch_service.settings")
    def test_is_web_fetch_request_true_20260209(self, mock_settings):
        """Detects web_fetch_20260209 tool in request."""
        mock_settings.enable_web_fetch = True
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "web_fetch_20260209", "name": "web_fetch"}],
        )
        assert WebFetchService.is_web_fetch_request(request) is True

    @patch("app.services.web_fetch_service.settings")
    def test_is_web_fetch_request_false(self, mock_settings):
        """Returns false for non-fetch tools."""
        mock_settings.enable_web_fetch = True
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[
                {
                    "name": "get_weather",
                    "description": "Get weather",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                }
            ],
        )
        assert WebFetchService.is_web_fetch_request(request) is False

    @patch("app.services.web_fetch_service.settings")
    def test_is_web_fetch_request_no_tools(self, mock_settings):
        """Returns false when request has no tools."""
        mock_settings.enable_web_fetch = True
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
        )
        assert WebFetchService.is_web_fetch_request(request) is False

    @patch("app.services.web_fetch_service.settings")
    def test_is_web_fetch_request_disabled(self, mock_settings):
        """Returns false when web fetch is disabled in settings."""
        mock_settings.enable_web_fetch = False
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "web_fetch_20250910", "name": "web_fetch"}],
        )
        assert WebFetchService.is_web_fetch_request(request) is False


class TestExtractWebFetchConfig:
    """Tests for WebFetchService.extract_web_fetch_config()."""

    def test_extract_web_fetch_config(self):
        """Extracts full config from request with all parameters."""
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[
                {
                    "type": "web_fetch_20260209",
                    "name": "web_fetch",
                    "max_uses": 5,
                    "allowed_domains": ["example.com"],
                    "blocked_domains": ["evil.com"],
                    "citations": {"enabled": True},
                    "max_content_tokens": 3000,
                }
            ],
        )
        config = WebFetchService.extract_web_fetch_config(request)
        assert config is not None
        assert config.type == "web_fetch_20260209"
        assert config.name == "web_fetch"
        assert config.max_uses == 5
        assert config.allowed_domains == ["example.com"]
        assert config.blocked_domains == ["evil.com"]
        assert config.max_content_tokens == 3000

    def test_extract_web_fetch_config_minimal(self):
        """Extracts config with only type specified (minimal)."""
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "web_fetch_20250910", "name": "web_fetch"}],
        )
        config = WebFetchService.extract_web_fetch_config(request)
        assert config is not None
        assert config.type == "web_fetch_20250910"
        assert config.name == "web_fetch"
        assert config.max_uses is None
        assert config.allowed_domains is None
        assert config.blocked_domains is None

    def test_extract_web_fetch_config_no_tools(self):
        """Returns None when request has no tools."""
        request = MessageRequest(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
        )
        config = WebFetchService.extract_web_fetch_config(request)
        assert config is None


class TestBuildWebFetchToolResult:
    """Tests for WebFetchService._build_web_fetch_tool_result()."""

    def test_build_web_fetch_tool_result(self):
        """Builds correct result structure for a successful fetch."""
        service = WebFetchService()
        fetch_data = {
            "url": "https://example.com/page",
            "title": "Example Page",
            "content": "This is the page content.",
            "media_type": "text/html",
            "is_pdf": False,
        }
        result = service._build_web_fetch_tool_result("srvtoolu_123", fetch_data)

        assert result["type"] == "web_fetch_tool_result"
        assert result["tool_use_id"] == "srvtoolu_123"
        assert result["content"]["type"] == "web_fetch_result"
        assert result["content"]["url"] == "https://example.com/page"
        assert result["content"]["content"]["type"] == "document"
        assert result["content"]["content"]["source"]["type"] == "text"
        assert result["content"]["content"]["source"]["media_type"] == "text/html"
        assert result["content"]["content"]["source"]["data"] == "This is the page content."
        assert result["content"]["content"]["title"] == "Example Page"
        assert "retrieved_at" in result["content"]

    def test_build_web_fetch_tool_result_pdf(self):
        """Builds result with base64 source type for PDF."""
        service = WebFetchService()
        fetch_data = {
            "url": "https://example.com/doc.pdf",
            "title": "PDF Document",
            "content": "base64encodeddata",
            "media_type": "application/pdf",
            "is_pdf": True,
        }
        result = service._build_web_fetch_tool_result("srvtoolu_456", fetch_data)
        assert result["content"]["content"]["source"]["type"] == "base64"
        assert result["content"]["content"]["source"]["media_type"] == "application/pdf"


class TestBuildWebFetchError:
    """Tests for WebFetchService._build_web_fetch_error()."""

    def test_build_web_fetch_error(self):
        """Builds correct error structure."""
        service = WebFetchService()
        result = service._build_web_fetch_error("srvtoolu_789", "url_not_allowed")

        assert result["type"] == "web_fetch_tool_result"
        assert result["tool_use_id"] == "srvtoolu_789"
        assert result["content"]["type"] == "web_fetch_tool_error"
        assert result["content"]["error_code"] == "url_not_allowed"


class TestToServerToolId:
    """Tests for WebFetchService._to_server_tool_id()."""

    def test_to_server_tool_id_from_toolu(self):
        """Converts toolu_ prefix to srvtoolu_ prefix."""
        result = WebFetchService._to_server_tool_id("toolu_abc123")
        assert result == "srvtoolu_abc123"

    def test_to_server_tool_id_already_server(self):
        """Leaves srvtoolu_ prefix unchanged."""
        result = WebFetchService._to_server_tool_id("srvtoolu_abc123")
        assert result == "srvtoolu_abc123"

    def test_to_server_tool_id_no_prefix(self):
        """Prepends srvtoolu_ when no known prefix exists."""
        result = WebFetchService._to_server_tool_id("randomid")
        assert result == "srvtoolu_randomid"


class TestConvertToServerToolUse:
    """Tests for WebFetchService._convert_to_server_tool_use()."""

    def test_convert_to_server_tool_use(self):
        """Converts web_fetch tool_use blocks to server_tool_use."""
        service = WebFetchService()
        content = [
            {"type": "text", "text": "I will fetch that page for you."},
            {
                "type": "tool_use",
                "id": "toolu_abc123",
                "name": "web_fetch",
                "input": {"url": "https://example.com"},
            },
        ]
        converted = service._convert_to_server_tool_use(content)

        assert len(converted) == 2
        # Text block should be unchanged
        assert converted[0]["type"] == "text"
        assert converted[0]["text"] == "I will fetch that page for you."
        # tool_use should become server_tool_use
        assert converted[1]["type"] == "server_tool_use"
        assert converted[1]["id"] == "srvtoolu_abc123"
        assert converted[1]["name"] == "web_fetch"
        assert converted[1]["input"] == {"url": "https://example.com"}

    def test_convert_leaves_non_intercepted_tool_use(self):
        """Does not convert tool_use blocks for tools not intercepted by the proxy."""
        service = WebFetchService()
        content = [
            {
                "type": "tool_use",
                "id": "toolu_xyz",
                "name": "get_weather",
                "input": {"location": "NYC"},
            },
        ]
        converted = service._convert_to_server_tool_use(content)

        assert len(converted) == 1
        assert converted[0]["type"] == "tool_use"
        assert converted[0]["name"] == "get_weather"


class TestCheckDomainAllowed:
    """Tests for WebFetchService._check_domain_allowed()."""

    def test_check_domain_allowed(self):
        """Domain filtering works for allowed domains."""
        service = WebFetchService()
        from app.schemas.web_fetch import WebFetchToolDefinition

        config = WebFetchToolDefinition(
            type="web_fetch_20250910",
            allowed_domains=["example.com"],
        )
        assert service._check_domain_allowed("https://example.com/page", config) is True
        assert service._check_domain_allowed("https://docs.example.com/page", config) is True
        assert service._check_domain_allowed("https://other.com/page", config) is False

    def test_check_domain_blocked(self):
        """Domain blocking works for blocked domains."""
        service = WebFetchService()
        from app.schemas.web_fetch import WebFetchToolDefinition

        config = WebFetchToolDefinition(
            type="web_fetch_20250910",
            blocked_domains=["evil.com"],
        )
        assert service._check_domain_allowed("https://evil.com/page", config) is False
        assert service._check_domain_allowed("https://sub.evil.com/page", config) is False
        assert service._check_domain_allowed("https://good.com/page", config) is True

    def test_check_domain_no_filters(self):
        """All domains allowed when no filters configured."""
        service = WebFetchService()
        from app.schemas.web_fetch import WebFetchToolDefinition

        config = WebFetchToolDefinition(type="web_fetch_20250910")
        assert service._check_domain_allowed("https://anything.com/page", config) is True
