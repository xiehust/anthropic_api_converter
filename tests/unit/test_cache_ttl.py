"""Tests for cache TTL support."""
from decimal import Decimal

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


class TestCacheTTLPriorityIntegration:
    """Test the full priority chain: API key > client > proxy default."""

    def test_full_priority_api_key_wins(self):
        """API key TTL should override client TTL and proxy default."""
        from app.core.config import settings
        service = BedrockService()

        original = settings.default_cache_ttl
        settings.default_cache_ttl = "5m"
        try:
            body = {
                "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": "5m"}}],
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}],
            }
            service._apply_cache_ttl(body, api_key_cache_ttl="1h")
            assert body["system"][0]["cache_control"]["ttl"] == "1h"
            assert body["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
        finally:
            settings.default_cache_ttl = original

    def test_full_priority_client_preserved(self):
        """Without API key override, client TTL should be preserved."""
        from app.core.config import settings
        service = BedrockService()

        original = settings.default_cache_ttl
        settings.default_cache_ttl = "5m"
        try:
            body = {
                "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
                "messages": [],
            }
            service._apply_cache_ttl(body, api_key_cache_ttl=None)
            assert body["system"][0]["cache_control"]["ttl"] == "1h"
        finally:
            settings.default_cache_ttl = original

    def test_full_priority_default_fills_gap(self):
        """Proxy default fills blocks with cache_control but no TTL."""
        from app.core.config import settings
        service = BedrockService()

        original = settings.default_cache_ttl
        settings.default_cache_ttl = "1h"
        try:
            body = {
                "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}],
                "messages": [],
            }
            service._apply_cache_ttl(body, api_key_cache_ttl=None)
            assert body["system"][0]["cache_control"]["ttl"] == "1h"
        finally:
            settings.default_cache_ttl = original


from app.api.messages import _get_effective_cache_ttl
from app.schemas.anthropic import MessageRequest, SystemMessage, Message, CacheControl


class TestGetEffectiveCacheTTL:
    """Test the _get_effective_cache_ttl helper for billing TTL determination."""

    def _make_request(self, system=None, messages=None, tools=None):
        return MessageRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=100,
            system=system,
            messages=messages or [{"role": "user", "content": "hello"}],
            tools=tools,
        )

    def test_api_key_override_wins(self):
        req = self._make_request(
            system=[{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": "5m"}}]
        )
        assert _get_effective_cache_ttl("1h", req) == "1h"

    def test_client_ttl_from_system(self):
        req = self._make_request(
            system=[{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
        )
        assert _get_effective_cache_ttl(None, req) == "1h"

    def test_client_ttl_from_messages(self):
        req = self._make_request(
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
            ]}]
        )
        assert _get_effective_cache_ttl(None, req) == "1h"

    def test_client_ttl_from_tools(self):
        req = self._make_request(
            tools=[{
                "name": "tool1",
                "description": "test",
                "input_schema": {"type": "object", "properties": {}},
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }]
        )
        assert _get_effective_cache_ttl(None, req) == "1h"

    def test_proxy_default_fallback(self):
        from app.core.config import settings
        req = self._make_request()
        original = settings.default_cache_ttl
        settings.default_cache_ttl = "1h"
        try:
            assert _get_effective_cache_ttl(None, req) == "1h"
        finally:
            settings.default_cache_ttl = original

    def test_returns_none_when_no_ttl(self):
        from app.core.config import settings
        req = self._make_request()
        original = settings.default_cache_ttl
        settings.default_cache_ttl = None
        try:
            assert _get_effective_cache_ttl(None, req) is None
        finally:
            settings.default_cache_ttl = original


from unittest.mock import patch
from app.db.dynamodb import UsageTracker, UsageStatsManager


def _create_mock_dynamodb_client():
    """Create a DynamoDBClient that works with moto (no endpoint_url override)."""
    with patch("app.db.dynamodb.settings") as mock_settings:
        mock_settings.aws_region = "us-east-1"
        mock_settings.dynamodb_endpoint_url = None
        mock_settings.aws_access_key_id = None
        mock_settings.aws_secret_access_key = None
        mock_settings.aws_session_token = None
        mock_settings.dynamodb_api_keys_table = "anthropic-proxy-api-keys"
        mock_settings.dynamodb_usage_table = "anthropic-proxy-usage"
        mock_settings.dynamodb_model_mapping_table = "anthropic-proxy-model-mapping"
        mock_settings.dynamodb_model_pricing_table = "anthropic-proxy-model-pricing"
        mock_settings.dynamodb_usage_stats_table = "anthropic-proxy-usage-stats"
        mock_settings.usage_ttl_days = 0
        return DynamoDBClient()


@mock_aws
class TestUsageRecordCacheTTL:
    """Test that cache_ttl is stored in usage records."""

    def _setup(self):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="anthropic-proxy-usage",
            KeySchema=[
                {"AttributeName": "api_key", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "api_key", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client = _create_mock_dynamodb_client()
        tracker = UsageTracker(client)
        table = dynamodb.Table("anthropic-proxy-usage")
        return tracker, table

    def test_record_usage_stores_cache_ttl(self):
        tracker, table = self._setup()
        with patch("app.db.dynamodb.settings") as mock_settings:
            mock_settings.usage_ttl_days = 0
            tracker.record_usage(
                api_key="sk-test",
                request_id="req-1",
                model="claude-sonnet-4-5-20250929",
                input_tokens=100,
                output_tokens=50,
                cache_write_input_tokens=200,
                cache_ttl="1h",
            )
        items = table.scan()["Items"]
        assert len(items) == 1
        assert items[0]["cache_ttl"] == "1h"

    def test_record_usage_no_cache_ttl(self):
        tracker, table = self._setup()
        with patch("app.db.dynamodb.settings") as mock_settings:
            mock_settings.usage_ttl_days = 0
            tracker.record_usage(
                api_key="sk-test",
                request_id="req-1",
                model="claude-sonnet-4-5-20250929",
                input_tokens=100,
                output_tokens=50,
            )
        items = table.scan()["Items"]
        assert len(items) == 1
        assert "cache_ttl" not in items[0]


@mock_aws
class TestAggregationCacheWritePricing:
    """Test that aggregation uses correct cache write pricing based on TTL."""

    def _setup(self):
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        # Create usage table
        dynamodb.create_table(
            TableName="anthropic-proxy-usage",
            KeySchema=[
                {"AttributeName": "api_key", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "api_key", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Create usage-stats table
        dynamodb.create_table(
            TableName="anthropic-proxy-usage-stats",
            KeySchema=[{"AttributeName": "api_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "api_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client = _create_mock_dynamodb_client()
        stats_manager = UsageStatsManager(client)
        usage_table = dynamodb.Table("anthropic-proxy-usage")
        return stats_manager, usage_table

    def _insert_usage(self, table, api_key, timestamp, model, cache_write_tokens, cache_ttl=None):
        item = {
            "api_key": api_key,
            "timestamp": str(timestamp),
            "request_id": f"req-{timestamp}",
            "model": model,
            "input_tokens": 1000,
            "output_tokens": 500,
            "cached_tokens": 0,
            "cache_write_input_tokens": cache_write_tokens,
            "total_tokens": 1500,
            "success": True,
        }
        if cache_ttl:
            item["cache_ttl"] = cache_ttl
        table.put_item(Item=item)

    def test_5m_ttl_uses_cache_write_price(self):
        """5m TTL should use the cache_write_price field (1.25x input)."""
        stats_manager, usage_table = self._setup()
        model = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        self._insert_usage(usage_table, "sk-test", 1000, model, 10000, cache_ttl="5m")

        pricing_cache = {
            model: {
                "input_price": Decimal("3.00"),
                "output_price": Decimal("15.00"),
                "cache_read_price": Decimal("0.30"),
                "cache_write_price": Decimal("3.75"),  # 1.25x input
            }
        }
        result = stats_manager.aggregate_usage_for_key("sk-test", pricing_cache=pricing_cache)
        # 5m cache_write cost uses cache_write_price (3.75 = 1.25x input)
        expected_total = (1000 * 3.0 + 500 * 15.0 + 0 * 0.30 + 10000 * 3.75) / 1_000_000
        assert abs(result["total_cost"] - expected_total) < 1e-9

    def test_1h_ttl_uses_2x_input_price(self):
        """1h TTL should use 2x input_price instead of cache_write_price."""
        stats_manager, usage_table = self._setup()
        model = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        self._insert_usage(usage_table, "sk-test", 1000, model, 10000, cache_ttl="1h")

        pricing_cache = {
            model: {
                "input_price": Decimal("3.00"),
                "output_price": Decimal("15.00"),
                "cache_read_price": Decimal("0.30"),
                "cache_write_price": Decimal("3.75"),  # 1.25x input (should NOT be used for 1h)
            }
        }
        result = stats_manager.aggregate_usage_for_key("sk-test", pricing_cache=pricing_cache)
        # 1h cache_write cost = 10000 * (3.00 * 2.0) / 1_000_000 = 0.06
        expected_total = (1000 * 3.0 + 500 * 15.0 + 0 * 0.30 + 10000 * 6.0) / 1_000_000
        assert abs(result["total_cost"] - expected_total) < 1e-9

    def test_no_ttl_uses_cache_write_price(self):
        """No TTL (legacy records) should use cache_write_price (5m rate)."""
        stats_manager, usage_table = self._setup()
        model = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        self._insert_usage(usage_table, "sk-test", 1000, model, 10000, cache_ttl=None)

        pricing_cache = {
            model: {
                "input_price": Decimal("3.00"),
                "output_price": Decimal("15.00"),
                "cache_read_price": Decimal("0.30"),
                "cache_write_price": Decimal("3.75"),
            }
        }
        result = stats_manager.aggregate_usage_for_key("sk-test", pricing_cache=pricing_cache)
        expected_total = (1000 * 3.0 + 500 * 15.0 + 0 * 0.30 + 10000 * 3.75) / 1_000_000
        assert abs(result["total_cost"] - expected_total) < 1e-9

    def test_mixed_ttl_records(self):
        """Batch with mixed TTLs should use correct pricing per record."""
        stats_manager, usage_table = self._setup()
        model = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        # Record 1: 5m TTL
        self._insert_usage(usage_table, "sk-test", 1000, model, 10000, cache_ttl="5m")
        # Record 2: 1h TTL
        self._insert_usage(usage_table, "sk-test", 2000, model, 10000, cache_ttl="1h")

        pricing_cache = {
            model: {
                "input_price": Decimal("3.00"),
                "output_price": Decimal("15.00"),
                "cache_read_price": Decimal("0.30"),
                "cache_write_price": Decimal("3.75"),
            }
        }
        result = stats_manager.aggregate_usage_for_key("sk-test", pricing_cache=pricing_cache)
        # Record 1 (5m): (1000*3.0 + 500*15.0 + 10000*3.75) / 1M
        cost_5m = (1000 * 3.0 + 500 * 15.0 + 10000 * 3.75) / 1_000_000
        # Record 2 (1h): (1000*3.0 + 500*15.0 + 10000*6.0) / 1M
        cost_1h = (1000 * 3.0 + 500 * 15.0 + 10000 * 6.0) / 1_000_000
        expected_total = cost_5m + cost_1h
        assert abs(result["total_cost"] - expected_total) < 1e-9
