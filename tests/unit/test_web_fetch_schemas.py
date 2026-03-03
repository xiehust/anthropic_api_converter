"""
Unit tests for web fetch Pydantic schemas.

Tests the models defined in app/schemas/web_fetch.py.
"""
import re

from app.schemas.web_fetch import (
    WEB_FETCH_TOOL_TYPES,
    WEB_FETCH_BETA_HEADERS,
    WebFetchCitationConfig,
    WebFetchToolDefinition,
    WebFetchDocumentSource,
    WebFetchDocument,
    WebFetchResult,
    WebFetchToolResultError,
    WebFetchToolResultContent,
    now_iso,
)


class TestWebFetchToolTypes:
    """Tests for WEB_FETCH_TOOL_TYPES constant."""

    def test_web_fetch_tool_types(self):
        """Verify both web_fetch tool type identifiers exist."""
        assert "web_fetch_20250910" in WEB_FETCH_TOOL_TYPES
        assert "web_fetch_20260209" in WEB_FETCH_TOOL_TYPES
        assert len(WEB_FETCH_TOOL_TYPES) == 2


class TestWebFetchBetaHeaders:
    """Tests for WEB_FETCH_BETA_HEADERS constant."""

    def test_web_fetch_beta_headers(self):
        """Verify both beta headers exist."""
        assert "web-fetch-2025-09-10" in WEB_FETCH_BETA_HEADERS
        assert "web-fetch-2026-02-09" in WEB_FETCH_BETA_HEADERS
        assert len(WEB_FETCH_BETA_HEADERS) == 2


class TestWebFetchToolDefinition:
    """Tests for WebFetchToolDefinition model."""

    def test_web_fetch_tool_definition_minimal(self):
        """Test minimal definition with defaults."""
        defn = WebFetchToolDefinition(type="web_fetch_20250910")
        assert defn.type == "web_fetch_20250910"
        assert defn.name == "web_fetch"
        assert defn.max_uses is None
        assert defn.allowed_domains is None
        assert defn.blocked_domains is None
        assert defn.citations is None
        assert defn.max_content_tokens is None

    def test_web_fetch_tool_definition_full(self):
        """Test definition with all parameters set."""
        defn = WebFetchToolDefinition(
            type="web_fetch_20260209",
            name="web_fetch",
            max_uses=10,
            allowed_domains=["example.com", "docs.python.org"],
            blocked_domains=["evil.com"],
            citations=WebFetchCitationConfig(enabled=True),
            max_content_tokens=5000,
        )
        assert defn.type == "web_fetch_20260209"
        assert defn.name == "web_fetch"
        assert defn.max_uses == 10
        assert defn.allowed_domains == ["example.com", "docs.python.org"]
        assert defn.blocked_domains == ["evil.com"]
        assert defn.citations.enabled is True
        assert defn.max_content_tokens == 5000


class TestWebFetchResult:
    """Tests for WebFetchResult model."""

    def test_web_fetch_result(self):
        """Create a valid web fetch result."""
        source = WebFetchDocumentSource(
            type="text",
            media_type="text/plain",
            data="Hello, world!",
        )
        document = WebFetchDocument(
            source=source,
            title="Test Page",
            citations=WebFetchCitationConfig(enabled=True),
        )
        result = WebFetchResult(
            url="https://example.com",
            content=document,
            retrieved_at="2026-03-03T12:00:00Z",
        )
        assert result.type == "web_fetch_result"
        assert result.url == "https://example.com"
        assert result.content.source.data == "Hello, world!"
        assert result.content.title == "Test Page"
        assert result.retrieved_at == "2026-03-03T12:00:00Z"


class TestWebFetchToolResultError:
    """Tests for WebFetchToolResultError model."""

    def test_web_fetch_error(self):
        """Create an error with each known error code."""
        error_codes = [
            "invalid_input",
            "url_too_long",
            "url_not_allowed",
            "url_not_accessible",
            "too_many_requests",
            "unsupported_content_type",
            "max_uses_exceeded",
            "unavailable",
        ]
        for code in error_codes:
            error = WebFetchToolResultError(error_code=code)
            assert error.type == "web_fetch_tool_error"
            assert error.error_code == code


class TestWebFetchToolResultContent:
    """Tests for WebFetchToolResultContent model."""

    def test_with_result(self):
        """WebFetchToolResultContent wrapping a successful result."""
        source = WebFetchDocumentSource(
            type="text", media_type="text/plain", data="Page content"
        )
        doc = WebFetchDocument(source=source)
        result = WebFetchResult(
            url="https://example.com",
            content=doc,
            retrieved_at="2026-03-03T00:00:00Z",
        )
        content = WebFetchToolResultContent(
            tool_use_id="srvtoolu_abc123",
            content=result,
        )
        assert content.type == "web_fetch_tool_result"
        assert content.tool_use_id == "srvtoolu_abc123"
        assert content.content.type == "web_fetch_result"

    def test_with_error(self):
        """WebFetchToolResultContent wrapping an error."""
        error = WebFetchToolResultError(error_code="url_not_accessible")
        content = WebFetchToolResultContent(
            tool_use_id="srvtoolu_xyz789",
            content=error,
        )
        assert content.type == "web_fetch_tool_result"
        assert content.content.type == "web_fetch_tool_error"
        assert content.content.error_code == "url_not_accessible"


class TestNowIso:
    """Tests for now_iso() helper."""

    def test_now_iso(self):
        """Returns ISO 8601 format with Z suffix."""
        ts = now_iso()
        # Should match pattern like 2026-03-03T12:34:56Z
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts), (
            f"Timestamp '{ts}' does not match expected ISO 8601 format"
        )
