"""
Property-based tests for RuleEngine.

Feature: multi-provider-routing-gateway
"""
import re

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, assume

from app.routing.rules import RuleEngine, RoutingRule, RuleMatch


# ---------------------------------------------------------------------------
# Helpers / Strategies
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

_rule_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=4,
    max_size=12,
)


def _make_keyword_rule(
    rule_id: str, keyword: str, target_model: str, priority: int = 0
) -> RoutingRule:
    return RoutingRule(
        rule_id=rule_id,
        rule_name=f"kw-{rule_id}",
        rule_type="keyword",
        pattern=keyword,
        target_model=target_model,
        priority=priority,
    )


def _make_regex_rule(
    rule_id: str, pattern: str, target_model: str, priority: int = 0
) -> RoutingRule:
    return RoutingRule(
        rule_id=rule_id,
        rule_name=f"re-{rule_id}",
        rule_type="regex",
        pattern=pattern,
        target_model=target_model,
        priority=priority,
    )


def _make_model_rule(
    rule_id: str, source_models: str, target_model: str, priority: int = 0
) -> RoutingRule:
    return RoutingRule(
        rule_id=rule_id,
        rule_name=f"mdl-{rule_id}",
        rule_type="model",
        pattern=source_models,
        target_model=target_model,
        priority=priority,
    )


# ---------------------------------------------------------------------------
# Property 10: Rule matching correctness
# ---------------------------------------------------------------------------


class TestRuleMatchingCorrectness:
    """
    **Property 10: Rule matching correctness**

    - Keyword matches iff message contains keyword (case-insensitive)
    - Regex matches iff pattern matches
    - Model matches iff request model in source list

    **Validates: Requirements 7.2, 7.3, 7.4, 7.5**
    """

    # --- Keyword matching ---

    @given(keyword=_keyword, prefix=_safe_text, suffix=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_keyword_matches_when_present(
        self, keyword: str, prefix: str, suffix: str, target: str
    ):
        """
        **Validates: Requirements 7.2, 7.5**

        A keyword rule matches when the message contains the keyword
        (case-insensitive), and returns the configured target model.
        """
        engine = RuleEngine()
        rule = _make_keyword_rule("r1", keyword, target)
        engine.load_rules([rule])

        # Embed keyword in message (possibly with different case)
        message = prefix + keyword.upper() + suffix
        result = engine.match(message, "any-model")

        assert result is not None, f"Expected match for keyword '{keyword}' in '{message}'"
        assert result.target_model == target

    @given(keyword=_keyword, message=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_keyword_no_match_when_absent(
        self, keyword: str, message: str, target: str
    ):
        """
        **Validates: Requirements 7.2**

        A keyword rule does NOT match when the message does not contain
        the keyword (case-insensitive).
        """
        assume(keyword.lower() not in message.lower())

        engine = RuleEngine()
        rule = _make_keyword_rule("r1", keyword, target)
        engine.load_rules([rule])

        result = engine.match(message, "any-model")
        assert result is None, (
            f"Expected no match for keyword '{keyword}' in '{message}'"
        )

    # --- Regex matching ---

    @given(word=_keyword, prefix=_safe_text, suffix=_safe_text, target=_target_model)
    @settings(max_examples=100)
    def test_regex_matches_when_pattern_found(
        self, word: str, prefix: str, suffix: str, target: str
    ):
        """
        **Validates: Requirements 7.3, 7.5**

        A regex rule matches when the pattern is found in the message,
        and returns the configured target model.
        """
        # Use a simple literal pattern (escaped) to guarantee valid regex
        pattern = re.escape(word)
        engine = RuleEngine()
        rule = _make_regex_rule("r1", pattern, target)
        engine.load_rules([rule])

        message = prefix + word + suffix
        result = engine.match(message, "any-model")

        assert result is not None, (
            f"Expected regex match for pattern '{pattern}' in '{message}'"
        )
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
        rule = _make_regex_rule("r1", pattern, target)
        engine.load_rules([rule])

        result = engine.match(message, "any-model")
        assert result is None, (
            f"Expected no regex match for pattern '{pattern}' in '{message}'"
        )

    # --- Model matching ---

    @given(
        source_model=_model_id,
        other_models=st.lists(_model_id, min_size=0, max_size=3),
        target=_target_model,
    )
    @settings(max_examples=100)
    def test_model_matches_when_in_source_list(
        self, source_model: str, other_models: list[str], target: str
    ):
        """
        **Validates: Requirements 7.4, 7.5**

        A model rule matches when the request model is in the source
        model list, and returns the configured target model.
        """
        all_sources = [source_model] + other_models
        pattern = ",".join(all_sources)

        engine = RuleEngine()
        rule = _make_model_rule("r1", pattern, target)
        engine.load_rules([rule])

        result = engine.match("any message", source_model)
        assert result is not None, (
            f"Expected model match for '{source_model}' in '{pattern}'"
        )
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

        A model rule does NOT match when the request model is not in
        the source model list.
        """
        assume(request_model not in source_models)

        pattern = ",".join(source_models)
        engine = RuleEngine()
        rule = _make_model_rule("r1", pattern, target)
        engine.load_rules([rule])

        result = engine.match("any message", request_model)
        assert result is None, (
            f"Expected no model match for '{request_model}' in '{pattern}'"
        )


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
            min_size=2,
            max_size=8,
            unique=True,
        ),
        keyword=_keyword,
    )
    @settings(max_examples=100)
    def test_lowest_priority_keyword_rule_wins(
        self, priorities: list[int], keyword: str
    ):
        """
        **Validates: Requirements 7.1, 7.6**

        Given multiple keyword rules that all match the same message,
        the rule with the lowest priority value is returned.
        """
        rules = []
        for i, prio in enumerate(priorities):
            rules.append(
                _make_keyword_rule(
                    rule_id=f"r{i}",
                    keyword=keyword,
                    target_model=f"model-prio-{prio}",
                    priority=prio,
                )
            )

        engine = RuleEngine()
        engine.load_rules(rules)

        # Message contains the keyword so all rules match
        message = f"please use {keyword} for this task"
        result = engine.match(message, "any-model")

        expected_prio = min(priorities)
        assert result is not None
        assert result.target_model == f"model-prio-{expected_prio}", (
            f"Expected rule with priority {expected_prio}, "
            f"got target_model={result.target_model}"
        )

    @given(
        priorities=st.lists(
            st.integers(min_value=0, max_value=1000),
            min_size=2,
            max_size=8,
            unique=True,
        ),
        source_model=_model_id,
    )
    @settings(max_examples=100)
    def test_lowest_priority_model_rule_wins(
        self, priorities: list[int], source_model: str
    ):
        """
        **Validates: Requirements 7.1, 7.6**

        Given multiple model rules that all match the same request model,
        the rule with the lowest priority value is returned.
        """
        rules = []
        for i, prio in enumerate(priorities):
            rules.append(
                _make_model_rule(
                    rule_id=f"r{i}",
                    source_models=source_model,
                    target_model=f"model-prio-{prio}",
                    priority=prio,
                )
            )

        engine = RuleEngine()
        engine.load_rules(rules)

        result = engine.match("any message", source_model)

        expected_prio = min(priorities)
        assert result is not None
        assert result.target_model == f"model-prio-{expected_prio}", (
            f"Expected rule with priority {expected_prio}, "
            f"got target_model={result.target_model}"
        )

    @given(
        priorities=st.lists(
            st.integers(min_value=0, max_value=1000),
            min_size=2,
            max_size=8,
            unique=True,
        ),
    )
    @settings(max_examples=100)
    def test_mixed_rule_types_priority_ordering(
        self, priorities: list[int],
    ):
        """
        **Validates: Requirements 7.1, 7.6**

        Given a mix of keyword and model rules that all match,
        the rule with the lowest priority value wins regardless of type.
        """
        common_keyword = "testword"
        common_model = "test-model"

        rules = []
        for i, prio in enumerate(priorities):
            if i % 2 == 0:
                rules.append(
                    _make_keyword_rule(
                        rule_id=f"r{i}",
                        keyword=common_keyword,
                        target_model=f"model-prio-{prio}",
                        priority=prio,
                    )
                )
            else:
                rules.append(
                    _make_model_rule(
                        rule_id=f"r{i}",
                        source_models=common_model,
                        target_model=f"model-prio-{prio}",
                        priority=prio,
                    )
                )

        engine = RuleEngine()
        engine.load_rules(rules)

        # Message contains keyword AND request model matches
        message = f"please use {common_keyword} here"
        result = engine.match(message, common_model)

        expected_prio = min(priorities)
        assert result is not None
        assert result.target_model == f"model-prio-{expected_prio}", (
            f"Expected rule with priority {expected_prio}, "
            f"got target_model={result.target_model}"
        )
