"""
Property-based tests for RoutingEngine.

Feature: multi-provider-routing-gateway
Properties: 12, 23, 14, 19
"""
from unittest.mock import MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, assume

from app.core.exceptions import NoProviderAvailableError
from app.routing.engine import RoutingEngine, RoutingDecision
from app.routing.rules import RuleEngine


# ---------------------------------------------------------------------------
# Helpers / Strategies
# ---------------------------------------------------------------------------

_model_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=3,
    max_size=20,
)

_positive_price = st.floats(
    min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False,
)

_user_message = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ",
    min_size=1,
    max_size=50,
)


def _pricing_item(model_id: str, input_price: float, output_price: float,
                  provider: str = "bedrock", status: str = "active") -> dict:
    return {
        "model_id": model_id,
        "input_price": input_price,
        "output_price": output_price,
        "provider": provider,
        "status": status,
    }


def _std_cost(input_price: float, output_price: float) -> float:
    """Standardized cost: 1000 input + 500 output tokens."""
    return (1000 * input_price + 500 * output_price) / 1_000_000


def _make_engine(pricing_items: list, available_models: set | None = None,
                 smart_router=None) -> RoutingEngine:
    """Build a RoutingEngine with mocked pricing and registry."""
    pricing_manager = MagicMock()
    pricing_manager.list_all_pricing.return_value = {"items": pricing_items}

    registry = MagicMock()
    if available_models is not None:
        registry.get_providers_for_model.side_effect = (
            lambda m: [MagicMock()] if m in available_models else []
        )
    else:
        registry.get_providers_for_model.return_value = [MagicMock()]

    rule_engine = RuleEngine()  # no rules loaded

    return RoutingEngine(
        rule_engine=rule_engine,
        smart_router=smart_router,
        provider_registry=registry,
        pricing_manager=pricing_manager,
        cache_aware_routing=False,
    )


# ---------------------------------------------------------------------------
# Composite strategies
# ---------------------------------------------------------------------------

@st.composite
def _pricing_items_with_availability(draw):
    """
    Generate 2-8 pricing items with unique model IDs, distinct costs,
    and a random subset marked as available (at least one).
    """
    n = draw(st.integers(min_value=2, max_value=8))
    models = draw(st.lists(_model_id, min_size=n, max_size=n, unique=True))
    prices = draw(
        st.lists(st.tuples(_positive_price, _positive_price), min_size=n, max_size=n)
    )
    costs = [_std_cost(ip, op) for ip, op in prices]
    assume(len(set(costs)) == n)

    items = [
        _pricing_item(models[i], prices[i][0], prices[i][1])
        for i in range(n)
    ]

    avail_flags = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    assume(any(avail_flags))

    available = {models[i] for i in range(n) if avail_flags[i]}
    return items, available


@st.composite
def _all_unavailable_pricing(draw):
    """Generate pricing items where NO model has available keys."""
    n = draw(st.integers(min_value=1, max_value=5))
    models = draw(st.lists(_model_id, min_size=n, max_size=n, unique=True))
    prices = draw(
        st.lists(st.tuples(_positive_price, _positive_price), min_size=n, max_size=n)
    )
    items = [
        _pricing_item(models[i], prices[i][0], prices[i][1])
        for i in range(n)
    ]
    return items


# ---------------------------------------------------------------------------
# Property 12: Cost routing selects cheapest available model
# ---------------------------------------------------------------------------


class TestCostRoutingCheapestAvailable:
    """
    **Property 12: Cost routing selects cheapest available model**

    Cost routing selects model with lowest standardized cost
    (1000 input + 500 output tokens) that has available keys;
    raises NoProviderAvailableError if none.

    **Validates: Requirements 8.1, 8.2, 8.3, 8.4**
    """

    @given(data=_pricing_items_with_availability())
    @settings(max_examples=100)
    def test_cost_routing_selects_cheapest_available(self, data):
        """
        **Validates: Requirements 8.1, 8.2, 8.3**

        For any set of models with pricing and availability,
        cost routing selects the available model with the lowest
        standardized cost.
        """
        items, available = data
        engine = _make_engine(items, available)

        api_key_info = {"routing_strategy": "cost"}
        decision = engine.route("any-model", "hello", api_key_info)

        # Compute expected: cheapest available non-deprecated model
        available_with_cost = []
        for p in items:
            if p["model_id"] in available and p.get("status") != "deprecated":
                cost = _std_cost(p["input_price"], p["output_price"])
                available_with_cost.append((cost, p["model_id"]))
        available_with_cost.sort(key=lambda x: x[0])

        assert len(available_with_cost) > 0
        expected_model = available_with_cost[0][1]
        assert decision.model == expected_model

    @given(items=_all_unavailable_pricing())
    @settings(max_examples=100)
    def test_cost_routing_raises_when_none_available(self, items):
        """
        **Validates: Requirements 8.4**

        When no model has available keys, cost routing raises
        NoProviderAvailableError.
        """
        engine = _make_engine(items, available_models=set())

        api_key_info = {"routing_strategy": "cost"}
        with pytest.raises(NoProviderAvailableError):
            engine.route("any-model", "hello", api_key_info)


# ---------------------------------------------------------------------------
# Property 23: Quality routing selects most expensive available model
# ---------------------------------------------------------------------------


class TestQualityRoutingMostExpensive:
    """
    **Property 23: Quality routing selects most expensive available model**

    Quality routing selects model with highest standardized cost
    that has available keys.

    **Validates: Requirements 11.3**
    """

    @given(data=_pricing_items_with_availability())
    @settings(max_examples=100)
    def test_quality_routing_selects_most_expensive_available(self, data):
        """
        **Validates: Requirements 11.3**

        For any set of models with pricing and availability,
        quality routing selects the available model with the highest
        standardized cost (most expensive = highest quality proxy).
        """
        items, available = data
        engine = _make_engine(items, available)

        api_key_info = {"routing_strategy": "quality"}
        decision = engine.route("any-model", "hello", api_key_info)

        # Compute expected: most expensive available non-deprecated model
        available_with_cost = []
        for p in items:
            if p["model_id"] in available and p.get("status") != "deprecated":
                cost = _std_cost(p["input_price"], p["output_price"])
                available_with_cost.append((cost, p["model_id"]))
        available_with_cost.sort(key=lambda x: x[0], reverse=True)

        assert len(available_with_cost) > 0
        expected_model = available_with_cost[0][1]
        assert decision.model == expected_model

    @given(items=_all_unavailable_pricing())
    @settings(max_examples=100)
    def test_quality_routing_raises_when_none_available(self, items):
        """
        **Validates: Requirements 11.3**

        When no model has available keys, quality routing raises
        NoProviderAvailableError.
        """
        engine = _make_engine(items, available_models=set())

        api_key_info = {"routing_strategy": "quality"}
        with pytest.raises(NoProviderAvailableError):
            engine.route("any-model", "hello", api_key_info)


# ---------------------------------------------------------------------------
# Property 14: Budget-aware degradation
# ---------------------------------------------------------------------------


class TestBudgetAwareDegradation:
    """
    **Property 14: Budget-aware degradation**

    When budget_used_mtd/monthly_budget >= 0.8 and budget > 0,
    routing forces weak_model; strategy "off" skips budget check.

    **Validates: Requirements 10.1, 10.2, 10.3**
    """

    @given(
        budget=st.floats(min_value=1.0, max_value=10000.0,
                         allow_nan=False, allow_infinity=False),
        ratio=st.floats(min_value=0.81, max_value=5.0,
                        allow_nan=False, allow_infinity=False),
        request_model=_model_id,
        weak_model=_model_id,
        user_msg=_user_message,
        strategy=st.sampled_from(["cost", "quality", "auto"]),
    )
    @settings(max_examples=100)
    def test_degrades_to_weak_model_when_budget_threshold_met(
        self, budget, ratio, request_model, weak_model, user_msg, strategy
    ):
        """
        **Validates: Requirements 10.1, 10.2**

        When budget_used_mtd / monthly_budget >= 0.8 and budget > 0,
        routing forces the weak_model regardless of strategy.
        """
        used = budget * ratio  # ratio > 0.8 so used/budget > 0.8

        smart_router = MagicMock()
        smart_router.weak_model = weak_model
        smart_router.strong_model = "strong-model"

        engine = _make_engine([], available_models=set(), smart_router=smart_router)

        api_key_info = {
            "routing_strategy": strategy,
            "monthly_budget": budget,
            "budget_used_mtd": used,
        }
        decision = engine.route(request_model, user_msg, api_key_info)
        assert decision.model == weak_model
        assert decision.reason == "budget_degradation"

    @given(
        budget=st.floats(min_value=1.0, max_value=10000.0,
                         allow_nan=False, allow_infinity=False),
        ratio=st.floats(min_value=0.0, max_value=0.79,
                        allow_nan=False, allow_infinity=False),
        request_model=_model_id,
    )
    @settings(max_examples=100)
    def test_no_degradation_below_threshold(self, budget, ratio, request_model):
        """
        **Validates: Requirements 10.1, 10.2**

        When budget usage is below 80%, no degradation occurs.
        """
        used = budget * ratio

        smart_router = MagicMock()
        smart_router.weak_model = "weak-model"

        items = [_pricing_item("cheap-model", 0.01, 0.01)]
        engine = _make_engine(items, available_models={"cheap-model"},
                              smart_router=smart_router)

        api_key_info = {
            "routing_strategy": "cost",
            "monthly_budget": budget,
            "budget_used_mtd": used,
        }
        decision = engine.route(request_model, "hello", api_key_info)
        assert decision.reason != "budget_degradation"

    @given(
        budget=st.floats(min_value=1.0, max_value=10000.0,
                         allow_nan=False, allow_infinity=False),
        ratio=st.floats(min_value=0.8, max_value=5.0,
                        allow_nan=False, allow_infinity=False),
        request_model=_model_id,
    )
    @settings(max_examples=100)
    def test_strategy_off_skips_budget_check(self, budget, ratio, request_model):
        """
        **Validates: Requirements 10.3**

        When strategy is "off", budget degradation is never applied,
        even when budget threshold is exceeded.
        """
        used = budget * ratio

        api_key_info = {
            "routing_strategy": "off",
            "monthly_budget": budget,
            "budget_used_mtd": used,
        }

        engine = _make_engine([], available_models=set())
        decision = engine.route(request_model, "hello", api_key_info)

        assert decision.model == request_model
        assert decision.reason == "routing_off"

    @given(
        request_model=_model_id,
        used=st.floats(min_value=0.0, max_value=10000.0,
                       allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_zero_budget_never_degrades(self, request_model, used):
        """
        **Validates: Requirements 10.1**

        When monthly_budget is 0 or negative, degradation never triggers.
        """
        items = [_pricing_item("some-model", 0.01, 0.01)]
        engine = _make_engine(items, available_models={"some-model"})

        for budget_val in [0, -1, 0.0]:
            api_key_info = {
                "routing_strategy": "cost",
                "monthly_budget": budget_val,
                "budget_used_mtd": used,
            }
            decision = engine.route(request_model, "hello", api_key_info)
            assert decision.reason != "budget_degradation"


# ---------------------------------------------------------------------------
# Property 19: Routing strategy off passthrough
# ---------------------------------------------------------------------------


class TestRoutingStrategyOffPassthrough:
    """
    **Property 19: Routing strategy off passthrough**

    When strategy is "off", returns original model without
    rule/cost/smart routing.

    **Validates: Requirements 11.2, 10.3**
    """

    @given(request_model=_model_id, user_msg=_user_message)
    @settings(max_examples=100)
    def test_off_returns_original_model(self, request_model, user_msg):
        """
        **Validates: Requirements 11.2**

        When routing strategy is "off", the routing engine returns
        the original request model unchanged.
        """
        engine = _make_engine([], available_models=set())

        api_key_info = {"routing_strategy": "off"}
        decision = engine.route(request_model, user_msg, api_key_info)

        assert decision.model == request_model
        assert decision.provider == "bedrock"
        assert decision.reason == "routing_off"

    @given(request_model=_model_id, user_msg=_user_message)
    @settings(max_examples=100)
    def test_off_does_not_invoke_rule_engine(self, request_model, user_msg):
        """
        **Validates: Requirements 11.2**

        When strategy is "off", the rule engine is never consulted.
        """
        rule_engine = MagicMock(spec=RuleEngine)
        engine = RoutingEngine(
            rule_engine=rule_engine,
            smart_router=None,
            provider_registry=None,
            pricing_manager=None,
        )

        api_key_info = {"routing_strategy": "off"}
        decision = engine.route(request_model, user_msg, api_key_info)

        rule_engine.match.assert_not_called()
        assert decision.model == request_model

    @given(request_model=_model_id)
    @settings(max_examples=100)
    def test_off_does_not_invoke_pricing_or_smart(self, request_model):
        """
        **Validates: Requirements 11.2, 10.3**

        When strategy is "off", neither pricing manager nor smart router
        is consulted, and budget check is skipped.
        """
        pricing = MagicMock()
        smart = MagicMock()
        registry = MagicMock()

        engine = RoutingEngine(
            rule_engine=RuleEngine(),
            smart_router=smart,
            provider_registry=registry,
            pricing_manager=pricing,
        )

        api_key_info = {
            "routing_strategy": "off",
            "monthly_budget": 100,
            "budget_used_mtd": 95,  # over 80% threshold
        }
        decision = engine.route(request_model, "hello", api_key_info)

        pricing.list_all_pricing.assert_not_called()
        smart.classify.assert_not_called()
        registry.get_providers_for_model.assert_not_called()
        assert decision.model == request_model
        assert decision.reason == "routing_off"

    @given(request_model=_model_id)
    @settings(max_examples=100)
    def test_missing_strategy_defaults_to_off(self, request_model):
        """
        **Validates: Requirements 11.2**

        When api_key_info has no routing_strategy key, it defaults to "off".
        """
        engine = _make_engine([], available_models=set())

        api_key_info = {}  # no routing_strategy
        decision = engine.route(request_model, "hello", api_key_info)

        assert decision.model == request_model
        assert decision.reason == "routing_off"
