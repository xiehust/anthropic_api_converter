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


from app.services.bedrock_service import BedrockService


class TestApplyCacheTTL:
    def _get_service(self):
        return BedrockService()

    def test_api_key_ttl_overrides_all(self):
        service = self._get_service()
        body = {
            "system": [
                {"type": "text", "text": "You are helpful", "cache_control": {"type": "ephemeral", "ttl": "5m"}}
            ],
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "Hello", "cache_control": {"type": "ephemeral", "ttl": "5m"}}
                ]}
            ],
        }
        service._apply_cache_ttl(body, api_key_cache_ttl="1h")
        assert body["system"][0]["cache_control"]["ttl"] == "1h"
        assert body["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"

    def test_no_override_keeps_client_ttl(self):
        service = self._get_service()
        body = {
            "system": [
                {"type": "text", "text": "You are helpful", "cache_control": {"type": "ephemeral", "ttl": "5m"}}
            ],
            "messages": [],
        }
        service._apply_cache_ttl(body, api_key_cache_ttl=None)
        assert body["system"][0]["cache_control"]["ttl"] == "5m"

    def test_default_ttl_fills_missing(self):
        service = self._get_service()
        body = {
            "system": [
                {"type": "text", "text": "Hello", "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [],
        }
        from app.core.config import settings
        original = settings.default_cache_ttl
        settings.default_cache_ttl = "1h"
        try:
            service._apply_cache_ttl(body, api_key_cache_ttl=None)
            assert body["system"][0]["cache_control"]["ttl"] == "1h"
        finally:
            settings.default_cache_ttl = original

    def test_no_cache_control_untouched(self):
        service = self._get_service()
        body = {
            "system": "Just a string",
            "messages": [
                {"role": "user", "content": "Hello"}
            ],
        }
        service._apply_cache_ttl(body, api_key_cache_ttl="1h")
        assert body["system"] == "Just a string"
        assert body["messages"][0]["content"] == "Hello"

    def test_tools_cache_control_updated(self):
        service = self._get_service()
        body = {
            "system": [],
            "messages": [],
            "tools": [
                {"name": "tool1", "cache_control": {"type": "ephemeral"}},
                {"name": "tool2"},
            ],
        }
        service._apply_cache_ttl(body, api_key_cache_ttl="1h")
        assert body["tools"][0]["cache_control"]["ttl"] == "1h"
        assert "cache_control" not in body["tools"][1]
