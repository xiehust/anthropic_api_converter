"""Tests for critical security fixes: SSRF protection and timing-safe auth."""
import hmac
import socket
from unittest.mock import patch

import pytest

from app.services.web_fetch.providers import (
    FetchError,
    _is_private_ip,
    _validate_url,
    _validate_url_ssrf,
)


class TestSSRFProtection:
    """Tests for SSRF IP validation."""

    @pytest.mark.parametrize("ip", [
        "127.0.0.1",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.1",
        "192.168.1.100",
        "169.254.169.254",  # AWS EC2 metadata
        "169.254.170.2",    # ECS metadata
        "0.0.0.0",
        "::1",              # IPv6 loopback
        "fe80::1",          # IPv6 link-local
        "fc00::1",          # IPv6 unique local
    ])
    def test_blocks_private_ips(self, ip):
        assert _is_private_ip(ip) is True

    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",    # example.com
        "2606:4700::1",     # Cloudflare IPv6
    ])
    def test_allows_public_ips(self, ip):
        assert _is_private_ip(ip) is False

    def test_blocks_localhost_hostname(self):
        with pytest.raises(FetchError, match="ssrf_blocked"):
            _validate_url_ssrf("http://localhost/secret")

    def test_blocks_metadata_hostname(self):
        with pytest.raises(FetchError, match="ssrf_blocked"):
            _validate_url_ssrf("http://metadata.google.internal/")

    @patch("socket.getaddrinfo")
    def test_blocks_resolved_private_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        with pytest.raises(FetchError, match="ssrf_blocked"):
            _validate_url_ssrf("http://evil.example.com/")

    @patch("socket.getaddrinfo")
    def test_blocks_aws_metadata_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0)),
        ]
        with pytest.raises(FetchError, match="ssrf_blocked"):
            _validate_url_ssrf("http://evil.example.com/")

    @patch("socket.getaddrinfo")
    def test_allows_public_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        # Should not raise
        _validate_url_ssrf("http://example.com/")

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("Name resolution failed"))
    def test_blocks_unresolvable_hostname(self, mock_getaddrinfo):
        with pytest.raises(FetchError, match="url_not_accessible"):
            _validate_url_ssrf("http://nonexistent.invalid/")

    def test_validate_url_integrates_ssrf_check(self):
        """_validate_url calls SSRF check."""
        with patch("app.services.web_fetch.providers._validate_url_ssrf") as mock_ssrf:
            _validate_url("http://example.com")
            mock_ssrf.assert_called_once_with("http://example.com")

    def test_validate_url_still_rejects_invalid_schemes(self):
        with pytest.raises(FetchError, match="invalid_input"):
            _validate_url("ftp://example.com")

    def test_validate_url_still_rejects_long_urls(self):
        with pytest.raises(FetchError, match="url_too_long"):
            _validate_url("https://example.com/" + "a" * 250)


class TestTimingSafeAuth:
    """Tests that master API key uses constant-time comparison."""

    def test_auth_uses_hmac_compare_digest(self):
        """Verify the auth module imports and uses hmac.compare_digest."""
        import inspect
        from app.middleware import auth

        source = inspect.getsource(auth)
        assert "hmac.compare_digest" in source, (
            "auth.py must use hmac.compare_digest for master key comparison"
        )
        assert "import hmac" in source

    def test_hmac_compare_digest_behavior(self):
        """Sanity check that hmac.compare_digest works correctly."""
        assert hmac.compare_digest("secret-key-123", "secret-key-123") is True
        assert hmac.compare_digest("secret-key-123", "wrong-key-456") is False
        assert hmac.compare_digest("", "") is True
