"""
Property-based and unit tests for BedrockProvider.

Feature: multi-provider-routing-gateway
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import hypothesis.strategies as st
from hypothesis import given, settings as hypothesis_settings

from app.core.config import settings as app_settings
from app.services.bedrock_provider import BedrockProvider
from app.services.provider_base import ProviderResponse


# ---------------------------------------------------------------------------
# Property 3: Bedrock Provider model mapping coverage
# ---------------------------------------------------------------------------


class TestBedrockProviderModelMappingCoverage:
    """
    **Property 3: Bedrock Provider model mapping coverage**

    For any model ID present in default_model_mapping,
    BedrockProvider.supports_model should return true.

    **Validates: Requirements 2.4**
    """

    @given(
        model_id=st.sampled_from(list(app_settings.default_model_mapping.keys())),
    )
    @hypothesis_settings(max_examples=150)
    def test_supports_model_returns_true_for_all_default_mapping_models(
        self, model_id: str
    ):
        """
        **Validates: Requirements 2.4**

        For any model ID in default_model_mapping, supports_model returns True.
        """
        provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=None)
        assert provider.supports_model(model_id) is True


# ---------------------------------------------------------------------------
# Unit tests for BedrockProvider (Task 3.3)
# ---------------------------------------------------------------------------


class TestBedrockProviderInvoke:
    """
    Unit tests for BedrockProvider.invoke — wraps response correctly.

    Requirements: 2.1
    """

    @pytest.mark.asyncio
    async def test_invoke_returns_provider_response(self):
        """invoke wraps BedrockService response in a ProviderResponse."""
        mock_response = MagicMock()
        mock_service = MagicMock()
        mock_service.invoke_model = AsyncMock(return_value=mock_response)

        provider = BedrockProvider(bedrock_service=mock_service, pricing_manager=None)

        result = await provider.invoke(
            request=MagicMock(),
            model_id="claude-sonnet-4-5-20250929",
            api_key_info={"service_tier": "default"},
        )

        assert isinstance(result, ProviderResponse)
        assert result.response is mock_response
        assert result.provider_name == "bedrock"
        assert result.model_used == "claude-sonnet-4-5-20250929"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_invoke_delegates_kwargs_to_bedrock_service(self):
        """invoke passes request_id, service_tier, anthropic_beta, cache_ttl."""
        mock_service = MagicMock()
        mock_service.invoke_model = AsyncMock(return_value=MagicMock())

        provider = BedrockProvider(bedrock_service=mock_service, pricing_manager=None)

        request = MagicMock()
        await provider.invoke(
            request=request,
            model_id="test-model",
            api_key_info={"service_tier": "premium"},
            anthropic_beta="test-beta",
            cache_ttl=300,
            request_id="req-123",
        )

        mock_service.invoke_model.assert_called_once_with(
            request,
            request_id="req-123",
            service_tier="premium",
            anthropic_beta="test-beta",
            cache_ttl=300,
        )


class TestBedrockProviderInvokeStream:
    """
    Unit tests for BedrockProvider.invoke_stream — delegates to BedrockService.

    Requirements: 2.1
    """

    @pytest.mark.asyncio
    async def test_invoke_stream_yields_chunks_from_service(self):
        """invoke_stream yields chunks from BedrockService.invoke_model_stream."""
        chunks = ["chunk1", "chunk2", "chunk3"]

        async def mock_stream(*args, **kwargs):
            for c in chunks:
                yield c

        mock_service = MagicMock()
        mock_service.invoke_model_stream = mock_stream

        provider = BedrockProvider(bedrock_service=mock_service, pricing_manager=None)

        result_chunks = []
        async for chunk in provider.invoke_stream(
            request=MagicMock(),
            model_id="test-model",
            api_key_info={"service_tier": "default"},
        ):
            result_chunks.append(chunk)

        assert result_chunks == chunks


class TestBedrockProviderGetCost:
    """
    Unit tests for BedrockProvider.get_cost — calculation with mock pricing.

    Requirements: 2.6
    """

    def test_get_cost_calculates_correctly(self):
        """get_cost uses pricing_manager to compute cost in USD."""
        mock_pricing = MagicMock()
        mock_pricing.get_pricing.return_value = {
            "input_price": 3.0,
            "output_price": 15.0,
        }

        provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=mock_pricing)

        cost = provider.get_cost("claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=500)

        # (1000 * 3.0 + 500 * 15.0) / 1_000_000 = 10500 / 1_000_000 = 0.0105
        assert cost == pytest.approx(0.0105)
        mock_pricing.get_pricing.assert_called_once_with("claude-sonnet-4-5-20250929")

    def test_get_cost_returns_zero_when_no_pricing_manager(self):
        """get_cost returns 0.0 when pricing_manager is None."""
        provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=None)
        assert provider.get_cost("any-model", input_tokens=1000, output_tokens=500) == 0.0

    def test_get_cost_returns_zero_when_no_pricing_found(self):
        """get_cost returns 0.0 when pricing_manager returns None for model."""
        mock_pricing = MagicMock()
        mock_pricing.get_pricing.return_value = None

        provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=mock_pricing)
        assert provider.get_cost("unknown-model", input_tokens=1000, output_tokens=500) == 0.0

    def test_get_cost_handles_exception_gracefully(self):
        """get_cost returns 0.0 if pricing_manager raises an exception."""
        mock_pricing = MagicMock()
        mock_pricing.get_pricing.side_effect = Exception("DB error")

        provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=mock_pricing)
        assert provider.get_cost("any-model", input_tokens=1000, output_tokens=500) == 0.0
