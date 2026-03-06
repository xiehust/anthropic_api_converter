"""
Property-based tests and unit tests for SmartRouter.

Feature: multi-provider-routing-gateway
Properties: 13
"""
from unittest.mock import MagicMock, patch

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from app.routing.engine import RoutingEngine, RoutingDecision
from app.routing.rules import RuleEngine
from app.routing.smart import SmartRouter


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_model_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=3,
    max_size=20,
)

_user_message = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ",
    min_size=1,
    max_size=80,
)


# ---------------------------------------------------------------------------
# Property 13: Smart routing complexity mapping
# ---------------------------------------------------------------------------


class TestSmartRoutingComplexityMapping:
    """
    **Property 13: Smart routing complexity mapping**

    "high" classification → strong_model, "low" → weak_model.

    **Validates: Requirements 9.2, 9.3**
    """

    @given(
        strong_model=_model_id,
        weak_model=_model_id,
        user_msg=_user_message,
    )
    @settings(max_examples=100)
    def test_high_classification_selects_strong_model(self, strong_model, weak_model, user_msg):
        """
        **Validates: Requirements 9.2**

        When SmartRouter.classify returns "high", _route_by_smart
        selects the strong_model.
        """
        smart_router = SmartRouter(strong_model=strong_model, weak_model=weak_model)
        smart_router.classify = MagicMock(return_value="high")

        engine = RoutingEngine(
            rule_engine=RuleEngine(),
            smart_router=smart_router,
            provider_registry=None,
            pricing_manager=None,
            cache_aware_routing=False,
        )

        api_key_info = {"routing_strategy": "auto"}
        decision = engine.route("any-model", user_msg, api_key_info)

        assert decision.model == strong_model
        assert "high" in decision.reason

    @given(
        strong_model=_model_id,
        weak_model=_model_id,
        user_msg=_user_message,
    )
    @settings(max_examples=100)
    def test_low_classification_selects_weak_model(self, strong_model, weak_model, user_msg):
        """
        **Validates: Requirements 9.3**

        When SmartRouter.classify returns "low", _route_by_smart
        selects the weak_model.
        """
        smart_router = SmartRouter(strong_model=strong_model, weak_model=weak_model)
        smart_router.classify = MagicMock(return_value="low")

        engine = RoutingEngine(
            rule_engine=RuleEngine(),
            smart_router=smart_router,
            provider_registry=None,
            pricing_manager=None,
            cache_aware_routing=False,
        )

        api_key_info = {"routing_strategy": "auto"}
        decision = engine.route("any-model", user_msg, api_key_info)

        assert decision.model == weak_model
        assert "low" in decision.reason

    @given(
        strong_model=_model_id,
        weak_model=_model_id,
        classification=st.sampled_from(["high", "low"]),
        user_msg=_user_message,
    )
    @settings(max_examples=100)
    def test_classification_maps_to_correct_model(self, strong_model, weak_model, classification, user_msg):
        """
        **Validates: Requirements 9.2, 9.3**

        For any classification result, "high" → strong_model and
        "low" → weak_model.
        """
        smart_router = SmartRouter(strong_model=strong_model, weak_model=weak_model)
        smart_router.classify = MagicMock(return_value=classification)

        engine = RoutingEngine(
            rule_engine=RuleEngine(),
            smart_router=smart_router,
            provider_registry=None,
            pricing_manager=None,
            cache_aware_routing=False,
        )

        api_key_info = {"routing_strategy": "auto"}
        decision = engine.route("any-model", user_msg, api_key_info)

        expected = strong_model if classification == "high" else weak_model
        assert decision.model == expected


# ---------------------------------------------------------------------------
# Unit tests for SmartRouter (Task 10.3)
# ---------------------------------------------------------------------------


class TestSmartRouterUnit:
    """
    Unit tests for SmartRouter.

    **Validates: Requirements 9.5**
    """

    def test_router_not_loaded_initially(self):
        """
        SmartRouter uses lazy loading — _router is None until
        _ensure_loaded() or classify() is called.

        **Validates: Requirements 9.5**
        """
        router = SmartRouter(
            strong_model="strong-model",
            weak_model="weak-model",
            threshold=0.5,
        )
        assert router._router is None

    def test_routellm_not_imported_when_unavailable(self):
        """
        When routellm is not installed (ImportError), _ensure_loaded
        sets _router to "unavailable" and classify returns "high".

        **Validates: Requirements 9.5**
        """
        router = SmartRouter(
            strong_model="strong-model",
            weak_model="weak-model",
            threshold=0.5,
        )

        with patch.dict("sys.modules", {"routellm": None, "routellm.controller": None}):
            with patch("builtins.__import__", side_effect=ImportError("no routellm")):
                router._ensure_loaded()

        assert router._router == "unavailable"
        result = router.classify("any message")
        assert result == "high"

    def test_classification_failure_defaults_to_high(self):
        """
        When _router.completion raises an exception, classify
        returns "high" (fail-safe to strong model).

        **Validates: Requirements 9.5**
        """
        router = SmartRouter(
            strong_model="strong-model",
            weak_model="weak-model",
            threshold=0.5,
        )

        mock_controller = MagicMock()
        mock_controller.completion.side_effect = RuntimeError("classification error")
        router._router = mock_controller

        result = router.classify("some complex question")
        assert result == "high"

    def test_classify_returns_high_when_strong_model_chosen(self):
        """
        When RouteLLM completion returns the strong_model,
        classify returns "high".
        """
        router = SmartRouter(
            strong_model="claude-sonnet",
            weak_model="claude-haiku",
        )

        mock_result = MagicMock()
        mock_result.model = "claude-sonnet"
        mock_controller = MagicMock()
        mock_controller.completion.return_value = mock_result
        router._router = mock_controller

        assert router.classify("explain quantum computing") == "high"

    def test_classify_returns_low_when_weak_model_chosen(self):
        """
        When RouteLLM completion returns the weak_model,
        classify returns "low".
        """
        router = SmartRouter(
            strong_model="claude-sonnet",
            weak_model="claude-haiku",
        )

        mock_result = MagicMock()
        mock_result.model = "claude-haiku"
        mock_controller = MagicMock()
        mock_controller.completion.return_value = mock_result
        router._router = mock_controller

        assert router.classify("hello") == "low"
