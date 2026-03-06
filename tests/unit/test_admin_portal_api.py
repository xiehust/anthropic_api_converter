"""
Property-based and unit tests for Admin Portal backend APIs.

Tests DynamoDB persistence round-trips for routing rules, failover chains,
provider keys, and smart routing config using moto mock.

Feature: multi-provider-routing-gateway
"""
from decimal import Decimal

import boto3
import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from moto import mock_aws

from app.db.dynamodb import (
    DynamoDBClient,
    FailoverConfigManager,
    ProviderKeyManager,
    RoutingConfigManager,
    SmartRoutingConfigManager,
)
from app.keypool.encryption import KeyEncryption


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

REGION = "us-east-1"
ROUTING_TABLE = "test-routing-rules"
FAILOVER_TABLE = "test-failover-chains"
SMART_TABLE = "test-smart-routing-config"
PROVIDER_KEYS_TABLE = "test-provider-keys"


def _create_routing_rules_table(dynamodb):
    dynamodb.create_table(
        TableName=ROUTING_TABLE,
        KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_failover_chains_table(dynamodb):
    dynamodb.create_table(
        TableName=FAILOVER_TABLE,
        KeySchema=[{"AttributeName": "source_model", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "source_model", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_smart_routing_config_table(dynamodb):
    dynamodb.create_table(
        TableName=SMART_TABLE,
        KeySchema=[{"AttributeName": "config_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "config_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_provider_keys_table(dynamodb):
    dynamodb.create_table(
        TableName=PROVIDER_KEYS_TABLE,
        KeySchema=[{"AttributeName": "key_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "key_id", "AttributeType": "S"},
            {"AttributeName": "provider", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "provider-index",
                "KeySchema": [{"AttributeName": "provider", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _make_dynamodb_client(dynamodb_resource):
    """Create a DynamoDBClient-like object with the mocked resource."""
    client = object.__new__(DynamoDBClient)
    client.dynamodb = dynamodb_resource
    client.routing_rules_table_name = ROUTING_TABLE
    client.failover_chains_table_name = FAILOVER_TABLE
    client.smart_routing_config_table_name = SMART_TABLE
    client.provider_keys_table_name = PROVIDER_KEYS_TABLE
    return client


@pytest.fixture
def dynamodb_setup():
    """Provide a moto-mocked DynamoDB with all required tables."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _create_routing_rules_table(dynamodb)
        _create_failover_chains_table(dynamodb)
        _create_smart_routing_config_table(dynamodb)
        _create_provider_keys_table(dynamodb)
        db_client = _make_dynamodb_client(dynamodb)
        yield db_client


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

rule_types = st.sampled_from(["keyword", "regex", "model"])
providers = st.sampled_from(["bedrock", "openai", "anthropic", "deepseek"])
safe_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), max_codepoint=0x7E),
    min_size=1,
    max_size=50,
)
model_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=0x30, max_codepoint=0x7A),
    min_size=3,
    max_size=40,
)


# ---------------------------------------------------------------------------
# Property 21: Routing rule persistence round-trip (Task 16.9)
# ---------------------------------------------------------------------------


class TestRoutingRulePersistenceRoundTrip:
    """
    **Property 21: Routing rule persistence round-trip**

    Creating a rule via API then retrieving it returns equivalent rule
    with all fields preserved.

    **Validates: Requirements 18.9**
    """

    @given(
        rule_name=safe_text,
        rule_type=rule_types,
        pattern=safe_text,
        target_model=model_ids,
        target_provider=providers,
        priority=st.integers(min_value=0, max_value=1000),
        is_enabled=st.booleans(),
    )
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_create_then_get_preserves_all_fields(
        self,
        dynamodb_setup,
        rule_name,
        rule_type,
        pattern,
        target_model,
        target_provider,
        priority,
        is_enabled,
    ):
        """
        **Validates: Requirements 18.9**

        For any valid routing rule, creating it then retrieving it
        returns an equivalent rule with all fields preserved.
        """
        mgr = RoutingConfigManager(dynamodb_setup)

        created = mgr.create_rule(
            rule_name=rule_name,
            rule_type=rule_type,
            pattern=pattern,
            target_model=target_model,
            target_provider=target_provider,
            priority=priority,
            is_enabled=is_enabled,
        )

        retrieved = mgr.get_rule(created["rule_id"])
        assert retrieved is not None
        assert retrieved["rule_id"] == created["rule_id"]
        assert retrieved["rule_name"] == rule_name
        assert retrieved["rule_type"] == rule_type
        assert retrieved["pattern"] == pattern
        assert retrieved["target_model"] == target_model
        assert retrieved["target_provider"] == target_provider
        assert retrieved["priority"] == priority
        assert retrieved["is_enabled"] == is_enabled
        assert "created_at" in retrieved
        assert "updated_at" in retrieved


# ---------------------------------------------------------------------------
# Property 22: Failover chain persistence and order preservation (Task 16.10)
# ---------------------------------------------------------------------------


class TestFailoverChainPersistenceOrderPreservation:
    """
    **Property 22: Failover chain persistence and order preservation**

    Storing a failover chain and retrieving it preserves exact target order.

    **Validates: Requirements 20.4, 20.5**
    """

    @given(
        source_model=model_ids,
        targets=st.lists(
            st.fixed_dictionaries(
                {"provider": providers, "model": model_ids}
            ),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_create_then_get_preserves_target_order(
        self,
        dynamodb_setup,
        source_model,
        targets,
    ):
        """
        **Validates: Requirements 20.4, 20.5**

        For any failover chain with ordered targets, storing it to DynamoDB
        and retrieving it preserves the exact order of targets.
        """
        mgr = FailoverConfigManager(dynamodb_setup)

        created = mgr.create_chain(source_model=source_model, targets=targets)
        retrieved = mgr.get_chain(source_model)

        assert retrieved is not None
        assert retrieved["source_model"] == source_model
        assert len(retrieved["targets"]) == len(targets)
        for i, (expected, actual) in enumerate(zip(targets, retrieved["targets"])):
            assert actual["provider"] == expected["provider"], f"Target {i} provider mismatch"
            assert actual["model"] == expected["model"], f"Target {i} model mismatch"


# ---------------------------------------------------------------------------
# Unit tests for Admin Portal backend APIs (Task 16.11)
# ---------------------------------------------------------------------------


class TestProviderKeyCRUD:
    """
    Unit tests for Provider Key CRUD operations.

    Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6
    """

    def test_create_key_encrypts_and_stores(self, dynamodb_setup):
        """Create stores encrypted key and returns item with key_id."""
        enc = KeyEncryption("test-secret")
        mgr = ProviderKeyManager(dynamodb_setup)

        encrypted = enc.encrypt("sk-live-abc123xyz789")
        created = mgr.create_key(
            provider="openai",
            encrypted_api_key=encrypted,
            models=["gpt-4", "gpt-3.5-turbo"],
        )

        assert "key_id" in created
        assert created["provider"] == "openai"
        assert created["encrypted_api_key"] == encrypted
        assert created["models"] == ["gpt-4", "gpt-3.5-turbo"]
        assert created["is_enabled"] is True
        assert created["status"] == "available"

        # Verify we can decrypt the stored key
        assert enc.decrypt(created["encrypted_api_key"]) == "sk-live-abc123xyz789"

    def test_list_keys_masks_api_key(self, dynamodb_setup):
        """List returns keys; caller can mask the encrypted key for display."""
        enc = KeyEncryption("test-secret")
        mgr = ProviderKeyManager(dynamodb_setup)

        original_key = "sk-live-abc123xyz789"
        encrypted = enc.encrypt(original_key)
        mgr.create_key(provider="openai", encrypted_api_key=encrypted, models=["gpt-4"])

        keys = mgr.list_keys()
        assert len(keys) == 1
        # Masking is done at the API layer; verify the raw key is present
        assert keys[0]["encrypted_api_key"] == encrypted
        # Verify mask utility works on the original key
        assert KeyEncryption.mask(original_key) == "sk-l****z789"

    def test_delete_key_removes_from_table(self, dynamodb_setup):
        """Delete removes the key from DynamoDB."""
        mgr = ProviderKeyManager(dynamodb_setup)

        created = mgr.create_key(
            provider="anthropic",
            encrypted_api_key="enc-data",
            models=["claude-3"],
        )
        key_id = created["key_id"]

        assert mgr.get_key(key_id) is not None
        assert mgr.delete_key(key_id) is True
        assert mgr.get_key(key_id) is None

    def test_update_key_models_and_enabled(self, dynamodb_setup):
        """Update modifies models list and is_enabled flag."""
        mgr = ProviderKeyManager(dynamodb_setup)

        created = mgr.create_key(
            provider="openai",
            encrypted_api_key="enc-data",
            models=["gpt-4"],
        )
        key_id = created["key_id"]

        mgr.update_key(key_id, models=["gpt-4", "gpt-4o"], is_enabled=False)
        updated = mgr.get_key(key_id)
        assert updated["models"] == ["gpt-4", "gpt-4o"]
        assert updated["is_enabled"] is False


class TestRoutingRuleCRUD:
    """
    Unit tests for routing rule CRUD and reorder.

    Requirements: 18.2, 18.9
    """

    def test_create_rule_auto_priority(self, dynamodb_setup):
        """Creating rules without explicit priority auto-assigns incrementing values."""
        mgr = RoutingConfigManager(dynamodb_setup)

        r1 = mgr.create_rule("rule-a", "keyword", "hello", "model-a")
        r2 = mgr.create_rule("rule-b", "regex", ".*test.*", "model-b")

        assert r1["priority"] == 0
        assert r2["priority"] == 1

    def test_list_rules_sorted_by_priority(self, dynamodb_setup):
        """list_rules returns rules sorted by priority ascending."""
        mgr = RoutingConfigManager(dynamodb_setup)

        mgr.create_rule("low", "keyword", "x", "m1", priority=10)
        mgr.create_rule("high", "keyword", "y", "m2", priority=1)
        mgr.create_rule("mid", "keyword", "z", "m3", priority=5)

        rules = mgr.list_rules()
        priorities = [r["priority"] for r in rules]
        assert priorities == sorted(priorities)
        assert rules[0]["rule_name"] == "high"

    def test_update_rule_fields(self, dynamodb_setup):
        """update_rule modifies specified fields."""
        mgr = RoutingConfigManager(dynamodb_setup)

        created = mgr.create_rule("orig", "keyword", "test", "model-a")
        rule_id = created["rule_id"]

        mgr.update_rule(rule_id, rule_name="updated", pattern="new-pattern")
        updated = mgr.get_rule(rule_id)
        assert updated["rule_name"] == "updated"
        assert updated["pattern"] == "new-pattern"
        # Unchanged fields preserved
        assert updated["rule_type"] == "keyword"
        assert updated["target_model"] == "model-a"

    def test_delete_rule(self, dynamodb_setup):
        """delete_rule removes the rule from the table."""
        mgr = RoutingConfigManager(dynamodb_setup)

        created = mgr.create_rule("to-delete", "keyword", "x", "m1")
        rule_id = created["rule_id"]

        assert mgr.delete_rule(rule_id) is True
        assert mgr.get_rule(rule_id) is None

    def test_reorder_rules(self, dynamodb_setup):
        """reorder_rules updates priorities to match the given order."""
        mgr = RoutingConfigManager(dynamodb_setup)

        r1 = mgr.create_rule("first", "keyword", "a", "m1", priority=0)
        r2 = mgr.create_rule("second", "keyword", "b", "m2", priority=1)
        r3 = mgr.create_rule("third", "keyword", "c", "m3", priority=2)

        # Reverse the order
        new_order = [r3["rule_id"], r2["rule_id"], r1["rule_id"]]
        assert mgr.reorder_rules(new_order) is True

        rules = mgr.list_rules()
        assert rules[0]["rule_name"] == "third"
        assert rules[1]["rule_name"] == "second"
        assert rules[2]["rule_name"] == "first"


class TestFailoverChainCRUD:
    """
    Unit tests for failover chain CRUD.

    Requirements: 20.1, 20.5
    """

    def test_create_and_get_chain(self, dynamodb_setup):
        """Create a chain and retrieve it by source_model."""
        mgr = FailoverConfigManager(dynamodb_setup)

        targets = [
            {"provider": "bedrock", "model": "claude-haiku"},
            {"provider": "openai", "model": "gpt-3.5-turbo"},
        ]
        created = mgr.create_chain("claude-sonnet", targets)

        assert created["source_model"] == "claude-sonnet"
        assert len(created["targets"]) == 2

        retrieved = mgr.get_chain("claude-sonnet")
        assert retrieved is not None
        assert retrieved["targets"][0]["model"] == "claude-haiku"
        assert retrieved["targets"][1]["model"] == "gpt-3.5-turbo"

    def test_update_chain_targets(self, dynamodb_setup):
        """update_chain replaces the targets list."""
        mgr = FailoverConfigManager(dynamodb_setup)

        mgr.create_chain("model-a", [{"provider": "bedrock", "model": "m1"}])
        new_targets = [
            {"provider": "openai", "model": "gpt-4"},
            {"provider": "bedrock", "model": "m2"},
        ]
        assert mgr.update_chain("model-a", new_targets) is True

        updated = mgr.get_chain("model-a")
        assert len(updated["targets"]) == 2
        assert updated["targets"][0]["provider"] == "openai"

    def test_delete_chain(self, dynamodb_setup):
        """delete_chain removes the chain."""
        mgr = FailoverConfigManager(dynamodb_setup)

        mgr.create_chain("model-x", [{"provider": "bedrock", "model": "m1"}])
        assert mgr.delete_chain("model-x") is True
        assert mgr.get_chain("model-x") is None

    def test_list_chains(self, dynamodb_setup):
        """list_chains returns all stored chains."""
        mgr = FailoverConfigManager(dynamodb_setup)

        mgr.create_chain("model-a", [{"provider": "bedrock", "model": "m1"}])
        mgr.create_chain("model-b", [{"provider": "openai", "model": "gpt-4"}])

        chains = mgr.list_chains()
        source_models = {c["source_model"] for c in chains}
        assert source_models == {"model-a", "model-b"}


class TestSmartRoutingConfigCRUD:
    """
    Unit tests for smart routing config get/put.

    Requirements: 18.7
    """

    def test_put_and_get_config(self, dynamodb_setup):
        """put_config stores and get_config retrieves the global config."""
        mgr = SmartRoutingConfigManager(dynamodb_setup)

        result = mgr.put_config("claude-sonnet", "claude-haiku", threshold=0.7)
        assert result["strong_model"] == "claude-sonnet"
        assert result["weak_model"] == "claude-haiku"
        assert result["threshold"] == Decimal("0.7")

        retrieved = mgr.get_config()
        assert retrieved is not None
        assert retrieved["strong_model"] == "claude-sonnet"
        assert retrieved["weak_model"] == "claude-haiku"
        assert float(retrieved["threshold"]) == pytest.approx(0.7)

    def test_put_config_overwrites_existing(self, dynamodb_setup):
        """Calling put_config again overwrites the previous config."""
        mgr = SmartRoutingConfigManager(dynamodb_setup)

        mgr.put_config("model-a", "model-b", threshold=0.5)
        mgr.put_config("model-c", "model-d", threshold=0.3)

        config = mgr.get_config()
        assert config["strong_model"] == "model-c"
        assert config["weak_model"] == "model-d"

    def test_get_config_returns_none_when_empty(self, dynamodb_setup):
        """get_config returns None when no config has been stored."""
        mgr = SmartRoutingConfigManager(dynamodb_setup)
        assert mgr.get_config() is None
