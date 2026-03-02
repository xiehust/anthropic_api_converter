"""Tests for cache TTL support."""
import pytest
from app.schemas.anthropic import CacheControl


class TestCacheControlSchema:
    def test_cache_control_default(self):
        cc = CacheControl()
        assert cc.type == "ephemeral"
        assert cc.ttl is None

    def test_cache_control_with_5m_ttl(self):
        cc = CacheControl(ttl="5m")
        assert cc.ttl == "5m"
        dumped = cc.model_dump(exclude_none=True)
        assert dumped == {"type": "ephemeral", "ttl": "5m"}

    def test_cache_control_with_1h_ttl(self):
        cc = CacheControl(ttl="1h")
        assert cc.ttl == "1h"
        dumped = cc.model_dump(exclude_none=True)
        assert dumped == {"type": "ephemeral", "ttl": "1h"}

    def test_cache_control_invalid_ttl(self):
        with pytest.raises(Exception):
            CacheControl(ttl="10m")

    def test_cache_control_none_ttl_excluded(self):
        cc = CacheControl()
        dumped = cc.model_dump(exclude_none=True)
        assert "ttl" not in dumped
