"""
Property-based tests for RuleEngine — matching correctness and priority ordering.

Feature: multi-provider-routing-gateway
"""
import re

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, assume

from app.routing.rules import RuleEngine, RoutingRule, RuleMatch


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
    min_size=1,
    max_size=50,
)

_keyword = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz",
    min_size=2,
    max_size=10,
)

_model_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=3,
    max_size=20,
)

_target_model = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=3,
    max_size=20,
)


def _kw_rule(rid: str, keyword: str, target: str, priority: int = 0) -> RoutingRule:
    return RoutingRule(
        rule_id=rid, rule_name=f"kw-{rid}", rule_type="keyword",
        pattern=keyword, target_model=target, priority=priority,
    )


def _re_rule(rid: str, pattern: str, target: str, priority: int = 0) -> RoutingRule:
    return RoutingRule(
        rule_id=rid, rule_name=f"re-{rid}", rule_type="regex",
        pattern=pattern, target_model=target, priority=priority,
    )


def _mdl_rule(rid: str, sources: str, target: str, priority: int = 0) -> RoutingRule:
    return RoutingRule(
        rule_id=rid, rule_name=f"mdl-{rid}", rule_type="model",
        pattern=sources, target_model=target, priority=priority,
    )


# ---------------------------------------------------------------------------
# Property 10: Rule matching correctness
# ---------------------------------------------------------------------------


class TestRuleMatchingCorrectness:
    """
    **Property 10: Rule matching correctness**

    Keyword matches iff message contains keyword (case-insensitive);
    regex matches iff pattern matches; model matches iff request model
    in source list.

    **Validates: Requirements 7.2, 7.3, 7.4, 7.5**
    """

    # --- Keyword ---

    @given(keyword=_keyword, prefix=_safe_text, suffix=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_keyword_match_when_message_contains_keyword(
        self, keyword: str, prefix: str, suffix: str, target: str
    ):
        """
        **Validates: Requirements 7.2, 7.5**

        A keyword rule matches when the message contains the keyword
        (case-insensitive) and returns the configured target model.
        """
        engine = RuleEngine()
        engine.load_rules([_kw_rule("r1", keyword, target)])

        # Embed keyword in upper case to verify case-insensitivity
        message = prefix + keyword.upper() + suffix
        result = engine.match(message, "irrelevant-model")

        assert result is not None
        assert result.target_model == target

    @given(keyword=_keyword, message=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_keyword_no_match_when_absent(self, keyword: str, message: str, target: str):
        """
        **Validates: Requirements 7.2**

        A keyword rule does NOT match when the keyword is absent from the message.
        """
        assume(keyword.lower() not in message.lower())

        engine = RuleEngine()
        engine.load_rules([_kw_rule("r1", keyword, target)])

        assert engine.match(message, "irrelevant-model") is None

    # --- Regex ---

    @given(word=_keyword, prefix=_safe_text, suffix=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_regex_match_when_pattern_found(
        self, word: str, prefix: str, suffix: str, target: str
    ):
        """
        **Validates: Requirements 7.3, 7.5**

        A regex rule matches when the pattern is found in the message
        and returns the configured target model.
        """
        pattern = re.escape(word)
        engine = RuleEngine()
        engine.load_rules([_re_rule("r1", pattern, target)])

        message = prefix + word + suffix
        result = engine.match(message, "irrelevant-model")

        assert result is not None
        assert result.target_model == target

    @given(word=_keyword, message=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_regex_no_match_when_pattern_absent(
        self, word: str, message: str, target: str
    ):
        """
        **Validates: Requirements 7.3**

        A regex rule does NOT match when the pattern is not found.
        """
        assume(word not in message)

        pattern = re.escape(word)
        engine = RuleEngine()
        engine.load_rules([_re_rule("r1", pattern, target)])

        assert engine.match(message, "irrelevant-model") is None

    # --- Model ---

    @given(
        source_model=_model_id,
        extra_models=st.lists(_model_id, min_size=0, max_size=3),
        target=_target_model,
    )
    @settings(max_examples=100)
    def test_model_match_when_in_source_list(
        self, source_model: str, extra_models: list[str], target: str
    ):
        """
        **Validates: Requirements 7.4, 7.5**

        A model rule matches when the request model is in the source list
        and returns the configured target model.
        """
        sources = ",".join([source_model] + extra_models)
        engine = RuleEngine()
        engine.load_rules([_mdl_rule("r1", sources, target)])

        result = engine.match("any message", source_model)

        assert result is not None
        assert result.target_model == target

    @given(
        request_model=_model_id,
        source_models=st.lists(_model_id, min_size=1, max_size=5, unique=True),
        target=_target_model,
    )
    @settings(max_examples=100)
    def test_model_no_match_when_not_in_source_list(
        self, request_model: str, source_models: list[str], target: str
    ):
        """
        **Validates: Requirements 7.4**

        A model rule does NOT match when the request model is not in the source list.
        """
        assume(request_model not in source_models)

        sources = ",".join(source_models)
        engine = RuleEngine()
        engine.load_rules([_mdl_rule("r1", sources, target)])

        assert engine.match("any message", request_model) is None


# ---------------------------------------------------------------------------
# Property 11: Rule priority ordering
# ---------------------------------------------------------------------------


class TestRulePriorityOrdering:
    """
    **Property 11: Rule priority ordering**

    When multiple rules match, the one with lowest priority value is returned.

    **Validates: Requirements 7.1, 7.6**
    """

    @given(
        priorities=st.lists(
            st.integers(min_value=0, max_value=1000),
            min_size=2, max_size=8, unique=True,
        ),
        keyword=_keyword,
    )
    @settings(max_examples=100)
    def test_lowest_priority_keyword_rule_wins(
        self, priorities: list[int], keyword: str
    ):
        """
        **Validates: Requirements 7.1, 7.6**

        Given multiple keyword rules that all match, the rule with the
        lowest priority value is returned.
        """
        rules = [
            _kw_rule(f"r{i}", keyword, f"target-{prio}", priority=prio)
            for i, prio in enumerate(priorities)
        ]

        engine = RuleEngine()
        engine.load_rules(rules)

        message = f"message with {keyword} inside"
        result = engine.match(message, "any-model")

        expected_prio = min(priorities)
        assert result is not None
        assert result.target_model == f"target-{expected_prio}"

    @given(
        priorities=st.lists(
            st.integers(min_value=0, max_value=1000),
            min_size=2, max_size=8, unique=True,
        ),
        source_model=_model_id,
    )
    @settings(max_examples=100)
    def test_lowest_priority_model_rule_wins(
        self, priorities: list[int], source_model: str
    ):
        """
        **Validates: Requirements 7.1, 7.6**

        Given multiple model rules that all match, the rule with the
        lowest priority value is returned.
        """
        rules = [
            _mdl_rule(f"r{i}", source_model, f"target-{prio}", priority=prio)
            for i, prio in enumerate(priorities)
        ]

        engine = RuleEngine()
        engine.load_rules(rules)

        result = engine.match("any message", source_model)

        expected_prio = min(priorities)
        assert result is not None
        assert result.target_model == f"target-{expected_prio}"

    @given(
        priorities=st.lists(
            st.integers(min_value=0, max_value=1000),
            min_size=2, max_size=8, unique=True,
        ),
    )
    @settings(max_examples=100)
    def test_mixed_rule_types_lowest_priority_wins(
        self, priorities: list[int],
    ):
        """
        **Validates: Requirements 7.1, 7.6**

        Given a mix of keyword and model rules that all match the same
        input, the rule with the lowest priority value wins regardless
        of rule type.
        """
        common_kw = "testword"
        common_model = "test-model"

        rules = []
        for i, prio in enumerate(priorities):
            if i % 2 == 0:
                rules.append(_kw_rule(f"r{i}", common_kw, f"target-{prio}", priority=prio))
            else:
                rules.append(_mdl_rule(f"r{i}", common_model, f"target-{prio}", priority=prio))

        engine = RuleEngine()
        engine.load_rules(rules)

        # Message contains keyword AND request model matches
        result = engine.match(f"use {common_kw} here", common_model)

        expected_prio = min(priorities)
        assert result is not None
        assert result.target_model == f"target-{expected_prio}"
