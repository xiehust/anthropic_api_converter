"""
Property-based tests for ProviderRegistry.

Feature: multi-provider-routing-gateway
"""
from typing import Any, AsyncIterator, Dict, List

import hypothesis.strategies as st
from hypothesis import given, settings

from app.services.provider_base import LLMProvider, ProviderResponse
from app.services.provider_registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Stub LLMProvider for testing
# ---------------------------------------------------------------------------

class StubProvider(LLMProvider):
    """Minimal LLMProvider stub with configurable name and model support."""

    def __init__(self, provider_name: str, supported_models: frozenset[str]):
        self._name = provider_name
        self._supported_models = supported_models

    @property
    def name(self) -> str:
        return self._name

    def supports_model(self, model_id: str) -> bool:
        return model_id in self._supported_models

    # --- abstract methods not exercised by registry tests ---

    async def invoke(self, request, model_id, api_key_info, **kwargs) -> ProviderResponse:
        raise NotImplementedError

    async def invoke_stream(self, request, model_id, api_key_info, **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError
        yield  # pragma: no cover

    def get_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        return 0.0

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": m} for m in self._supported_models]


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Generate a unique provider name
provider_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=20,
)

# Generate a set of model IDs
model_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_./"),
    min_size=1,
    max_size=40,
)

model_set_st = st.frozensets(model_id_st, min_size=0, max_size=5)


@st.composite
def provider_st(draw):
    """Draw a StubProvider with a unique name and a set of supported models."""
    name = draw(provider_name_st)
    models = draw(model_set_st)
    return StubProvider(provider_name=name, supported_models=models)


@st.composite
def providers_with_unique_names(draw):
    """Draw a list of providers whose names are all distinct."""
    providers = draw(st.lists(provider_st(), min_size=0, max_size=6))
    seen: set[str] = set()
    unique: list[StubProvider] = []
    for p in providers:
        if p.name not in seen:
            seen.add(p.name)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Property 1: Provider Registry model lookup correctness
# ---------------------------------------------------------------------------


class TestProviderRegistryModelLookup:
    """
    **Property 1: Provider Registry model lookup correctness**

    For any set of registered providers with known model support and any model
    query, `get_providers_for_model` returns exactly the providers whose
    `supports_model` returns true, and empty list when none match.

    **Validates: Requirements 1.2, 1.3, 1.4**
    """

    @given(
        providers=providers_with_unique_names(),
        query_model=model_id_st,
    )
    @settings(max_examples=150)
    def test_get_providers_for_model_returns_exactly_supporting_providers(
        self, providers: list[StubProvider], query_model: str
    ):
        """
        **Validates: Requirements 1.2, 1.3, 1.4**

        get_providers_for_model returns exactly the providers whose
        supports_model returns true for the queried model.
        """
        registry = ProviderRegistry()
        for p in providers:
            registry.register(p)

        result = registry.get_providers_for_model(query_model)

        # Build expected set from ground truth
        expected_names = {p.name for p in providers if p.supports_model(query_model)}
        actual_names = {p.name for p in result}

        assert actual_names == expected_names

    @given(
        providers=providers_with_unique_names(),
        query_model=model_id_st,
    )
    @settings(max_examples=150)
    def test_empty_list_when_no_provider_supports_model(
        self, providers: list[StubProvider], query_model: str
    ):
        """
        **Validates: Requirements 1.4**

        When no provider supports the queried model, the result is an empty list.
        """
        # Filter to only providers that do NOT support the query model
        non_supporting = [p for p in providers if not p.supports_model(query_model)]

        registry = ProviderRegistry()
        for p in non_supporting:
            registry.register(p)

        result = registry.get_providers_for_model(query_model)
        assert result == []


# ---------------------------------------------------------------------------
# Property 2: Provider Registry register/unregister round-trip
# ---------------------------------------------------------------------------


class TestProviderRegistryRoundTrip:
    """
    **Property 2: Provider Registry register/unregister round-trip**

    For any provider, registering then unregistering should remove it from the
    registry.

    **Validates: Requirements 1.6**
    """

    @given(provider=provider_st())
    @settings(max_examples=150)
    def test_register_then_unregister_removes_provider(self, provider: StubProvider):
        """
        **Validates: Requirements 1.6**

        After register → unregister, the provider is no longer in the registry.
        """
        registry = ProviderRegistry()
        registry.register(provider)

        # Confirm it's there
        assert registry.get_provider(provider.name) is provider

        registry.unregister(provider.name)

        # Confirm it's gone
        assert registry.get_provider(provider.name) is None

    @given(provider=provider_st(), query_model=model_id_st)
    @settings(max_examples=150)
    def test_unregistered_provider_not_returned_for_model_query(
        self, provider: StubProvider, query_model: str
    ):
        """
        **Validates: Requirements 1.6**

        After unregistering, querying models should no longer return the
        unregistered provider.
        """
        registry = ProviderRegistry()
        registry.register(provider)
        registry.unregister(provider.name)

        result = registry.get_providers_for_model(query_model)
        result_names = {p.name for p in result}
        assert provider.name not in result_names
