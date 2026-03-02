"""Tests for cache TTL support."""
import boto3
import pytest
from moto import mock_aws

from app.db.dynamodb import DynamoDBClient, APIKeyManager
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


from app.core.config import Settings


class TestCacheTTLConfig:
    def test_default_cache_ttl_none(self):
        s = Settings(DEFAULT_CACHE_TTL=None)
        assert s.default_cache_ttl is None

    def test_default_cache_ttl_5m(self):
        s = Settings(DEFAULT_CACHE_TTL="5m")
        assert s.default_cache_ttl == "5m"

    def test_default_cache_ttl_1h(self):
        s = Settings(DEFAULT_CACHE_TTL="1h")
        assert s.default_cache_ttl == "1h"


@mock_aws
class TestAPIKeyManagerCacheTTL:
    def _setup_table(self):
        """Create the API keys table in mocked DynamoDB."""
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="anthropic-proxy-api-keys",
            KeySchema=[{"AttributeName": "api_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "api_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client = DynamoDBClient()
        return APIKeyManager(client)

    def test_create_api_key_with_cache_ttl(self):
        manager = self._setup_table()
        api_key = manager.create_api_key(
            user_id="test-user",
            name="Test Key",
            cache_ttl="1h",
        )
        item = manager.get_api_key(api_key)
        assert item["cache_ttl"] == "1h"

    def test_create_api_key_without_cache_ttl(self):
        manager = self._setup_table()
        api_key = manager.create_api_key(
            user_id="test-user",
            name="Test Key",
        )
        item = manager.get_api_key(api_key)
        assert item.get("cache_ttl") is None

    def test_update_api_key_cache_ttl(self):
        manager = self._setup_table()
        api_key = manager.create_api_key(user_id="test-user", name="Test")
        manager.update_api_key(api_key, cache_ttl="1h")
        item = manager.get_api_key(api_key)
        assert item["cache_ttl"] == "1h"

    def test_update_api_key_clear_cache_ttl(self):
        manager = self._setup_table()
        api_key = manager.create_api_key(user_id="test-user", name="Test", cache_ttl="1h")
        manager.update_api_key(api_key, cache_ttl="none")
        item = manager.get_api_key(api_key)
        assert item.get("cache_ttl") is None

    def test_validate_api_key_returns_cache_ttl(self):
        manager = self._setup_table()
        api_key = manager.create_api_key(user_id="test-user", name="Test", cache_ttl="1h")
        result = manager.validate_api_key(api_key)
        assert result is not None
        assert result["cache_ttl"] == "1h"
