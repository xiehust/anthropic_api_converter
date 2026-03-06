"""
Property-based tests for KeyPoolManager.

Feature: multi-provider-routing-gateway
"""
import time
from collections import Counter
from unittest.mock import patch

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, assume

from app.keypool.manager import KeyPoolManager, KeyState


# ---------------------------------------------------------------------------
# Helpers / Strategies
# ---------------------------------------------------------------------------

_provider_name = st.just("test-provider")
_model_id = st.just("test-model")

# Strategy for generating a list of unique key IDs (1..10 keys)
_key_ids = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=4, max_size=12),
    min_size=1,
    max_size=10,
    unique=True,
)


def _build_manager(key_ids: list[str], provider: str = "test-provider",
                   model: str = "test-model") -> KeyPoolManager:
    """Build a KeyPoolManager with N enabled keys, no encryption."""
    mgr = KeyPoolManager(encryption=None)
    items = [
        {
            "key_id": kid,
            "provider": provider,
            "encrypted_api_key": f"enc-{kid}",
            "models": [model],
            "is_enabled": True,
        }
        for kid in key_ids
    ]
    mgr.load_keys_from_items(items)
    return mgr


# ---------------------------------------------------------------------------
# Property 5: Round-Robin even distribution
# ---------------------------------------------------------------------------


class TestRoundRobinEvenDistribution:
    """
    **Property 5: Round-Robin even distribution**

    For any pool with N available keys, calling get_available_key N times
    returns each key exactly once.

    **Validates: Requirements 4.1, 4.2**
    """

    @given(key_ids=_key_ids)
    @settings(max_examples=100)
    def test_n_calls_return_each_key_once(self, key_ids: list[str]):
        """
        **Validates: Requirements 4.1, 4.2**

        Calling get_available_key N times on a fresh pool of N keys
        returns each key exactly once (round-robin).
        """
        mgr = _build_manager(key_ids)
        n = len(key_ids)

        returned_key_ids: list[str] = []
        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(n):
                result = mgr.get_available_key("test-provider", "test-model")
                assert result is not None
                _key_value, key_id = result
                returned_key_ids.append(key_id)

        counts = Counter(returned_key_ids)
        assert set(returned_key_ids) == set(key_ids), (
            f"Expected all keys returned; got {set(returned_key_ids)}"
        )
        for kid in key_ids:
            assert counts[kid] == 1, f"Key {kid} returned {counts[kid]} times, expected 1"

    @given(key_ids=_key_ids)
    @settings(max_examples=100)
    def test_round_robin_wraps_around(self, key_ids: list[str]):
        """
        **Validates: Requirements 4.1, 4.2**

        After N calls the cycle repeats — 2N calls gives each key exactly twice.
        """
        mgr = _build_manager(key_ids)
        n = len(key_ids)

        returned_key_ids: list[str] = []
        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(2 * n):
                result = mgr.get_available_key("test-provider", "test-model")
                assert result is not None
                returned_key_ids.append(result[1])

        counts = Counter(returned_key_ids)
        for kid in key_ids:
            assert counts[kid] == 2, f"Key {kid} returned {counts[kid]} times, expected 2"


# ---------------------------------------------------------------------------
# Property 6: Cooldown key skipping
# ---------------------------------------------------------------------------


class TestCooldownKeySkipping:
    """
    **Property 6: Cooldown key skipping**

    get_available_key never returns a key in cooldown;
    returns None when all keys are in cooldown.

    **Validates: Requirements 4.3, 4.4**
    """

    @given(key_ids=_key_ids)
    @settings(max_examples=100)
    def test_cooled_down_keys_are_skipped(self, key_ids: list[str]):
        """
        **Validates: Requirements 4.3**

        Put the first key in cooldown; it should never be returned.
        """
        assume(len(key_ids) >= 2)
        mgr = _build_manager(key_ids)
        cooled_key = key_ids[0]

        now = 1000.0
        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = now
            # Put first key in cooldown (until now + 120)
            for ks in mgr._keys["test-provider"]:
                if ks.provider_key_id == cooled_key:
                    ks.cooldown_until = now + 120
                    break

            # Call N times — cooled key should never appear
            for _ in range(len(key_ids) * 2):
                result = mgr.get_available_key("test-provider", "test-model")
                if result is not None:
                    assert result[1] != cooled_key, (
                        f"Cooled-down key {cooled_key} was returned"
                    )

    @given(key_ids=_key_ids)
    @settings(max_examples=100)
    def test_all_keys_in_cooldown_returns_none(self, key_ids: list[str]):
        """
        **Validates: Requirements 4.4**

        When all keys are in cooldown, get_available_key returns None.
        """
        mgr = _build_manager(key_ids)
        now = 1000.0

        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = now
            # Put all keys in cooldown
            for ks in mgr._keys["test-provider"]:
                ks.cooldown_until = now + 300

            result = mgr.get_available_key("test-provider", "test-model")
            assert result is None


# ---------------------------------------------------------------------------
# Property 7: Rate limit marking and cooldown time
# ---------------------------------------------------------------------------


class TestRateLimitMarkingAndCooldownTime:
    """
    **Property 7: Rate limit marking and cooldown time**

    mark_rate_limited with retry_after sets cooldown to that value;
    None defaults to 60s; same for preemptive cooldown.

    **Validates: Requirements 5.1, 5.2, 5.3, 5.5**
    """

    @given(
        key_ids=_key_ids,
        retry_after=st.integers(min_value=1, max_value=3600),
    )
    @settings(max_examples=100)
    def test_mark_rate_limited_with_retry_after(self, key_ids: list[str],
                                                 retry_after: int):
        """
        **Validates: Requirements 5.1, 5.2**

        mark_rate_limited(retry_after=X) sets cooldown_until = now + X.
        """
        mgr = _build_manager(key_ids)
        target_key = key_ids[0]
        now = 5000.0

        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = now
            mgr.mark_rate_limited("test-provider", target_key, retry_after=retry_after)

        ks = next(k for k in mgr._keys["test-provider"]
                  if k.provider_key_id == target_key)
        assert ks.cooldown_until == pytest.approx(now + retry_after)

    @given(key_ids=_key_ids)
    @settings(max_examples=100)
    def test_mark_rate_limited_default_60s(self, key_ids: list[str]):
        """
        **Validates: Requirements 5.3**

        mark_rate_limited(retry_after=None) defaults to 60s cooldown.
        """
        mgr = _build_manager(key_ids)
        target_key = key_ids[0]
        now = 5000.0

        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = now
            mgr.mark_rate_limited("test-provider", target_key, retry_after=None)

        ks = next(k for k in mgr._keys["test-provider"]
                  if k.provider_key_id == target_key)
        assert ks.cooldown_until == pytest.approx(now + 60)

    @given(key_ids=_key_ids)
    @settings(max_examples=100)
    def test_preemptive_cooldown_sets_60s(self, key_ids: list[str]):
        """
        **Validates: Requirements 5.5**

        mark_preemptive_cooldown sets cooldown_until = now + 60.
        """
        mgr = _build_manager(key_ids)
        target_key = key_ids[0]
        now = 5000.0

        with patch("app.keypool.manager.time") as mock_time:
            mock_time.time.return_value = now
            mgr.mark_preemptive_cooldown("test-provider", target_key)

        ks = next(k for k in mgr._keys["test-provider"]
                  if k.provider_key_id == target_key)
        assert ks.cooldown_until == pytest.approx(now + 60)


# ---------------------------------------------------------------------------
# Property 8: Cooldown recovery round-trip
# ---------------------------------------------------------------------------


class TestCooldownRecovery:
    """
    **Property 8: Cooldown recovery round-trip**

    After cooldown time T elapses, the key becomes available again.

    **Validates: Requirements 5.4**
    """

    @given(
        key_ids=_key_ids,
        cooldown_secs=st.integers(min_value=1, max_value=3600),
    )
    @settings(max_examples=100)
    def test_key_recovers_after_cooldown(self, key_ids: list[str],
                                         cooldown_secs: int):
        """
        **Validates: Requirements 5.4**

        After marking a key rate-limited with cooldown T, the key is
        unavailable before T elapses and available again after T elapses.
        """
        mgr = _build_manager(key_ids)
        target_key = key_ids[0]
        now = 5000.0

        with patch("app.keypool.manager.time") as mock_time:
            # Mark rate limited
            mock_time.time.return_value = now
            mgr.mark_rate_limited("test-provider", target_key,
                                  retry_after=cooldown_secs)

            # During cooldown — key should not be returned
            mock_time.time.return_value = now + cooldown_secs - 0.5
            returned_during = set()
            for _ in range(len(key_ids) * 2):
                r = mgr.get_available_key("test-provider", "test-model")
                if r:
                    returned_during.add(r[1])
            assert target_key not in returned_during, (
                "Key returned while still in cooldown"
            )

            # After cooldown — key should be available again
            mock_time.time.return_value = now + cooldown_secs + 1.0
            # Reset round-robin index to ensure we cycle through all keys
            mgr._rr_index["test-provider"] = 0
            returned_after = set()
            for _ in range(len(key_ids) * 2):
                r = mgr.get_available_key("test-provider", "test-model")
                if r:
                    returned_after.add(r[1])
            assert target_key in returned_after, (
                "Key not returned after cooldown expired"
            )
