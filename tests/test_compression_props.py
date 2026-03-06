"""
Property-based tests for ContextCompressor.

Feature: multi-provider-routing-gateway
"""
import copy

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, assume

from app.compression.context_compressor import ContextCompressor, CompressionStats


# ---------------------------------------------------------------------------
# Helpers / Strategies
# ---------------------------------------------------------------------------

# Strategy for generating random text content of varying lengths
_short_text = st.text(min_size=1, max_size=50)
_long_text = st.text(min_size=2001, max_size=5000)
_any_text = st.text(min_size=1, max_size=5000)

# Strategy for tool_result content that exceeds max_chars
_tool_content_over = st.text(min_size=2001, max_size=5000)
# Strategy for tool_result content within max_chars
_tool_content_under = st.text(min_size=1, max_size=2000)


def _make_tool_result_msg(content: str, tool_use_id: str = "tool_1",
                          cache_control: dict | None = None) -> dict:
    """Build a user message containing a single tool_result block."""
    block = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if cache_control is not None:
        block["cache_control"] = cache_control
    return {"role": "user", "content": [block]}


def _make_assistant_msg(content: str, cache_control: bool = False) -> dict:
    """Build an assistant message with string content."""
    msg = {"role": "assistant", "content": content}
    if cache_control:
        msg["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    return msg


def _make_user_msg(content: str) -> dict:
    """Build a simple user message with string content."""
    return {"role": "user", "content": content}


def _build_conversation(num_turns: int, assistant_text_len: int = 300) -> list[dict]:
    """Build a multi-turn conversation with alternating user/assistant messages."""
    msgs = []
    for i in range(num_turns):
        msgs.append(_make_user_msg(f"User message {i}"))
        msgs.append(_make_assistant_msg("A" * assistant_text_len))
    return msgs


# ---------------------------------------------------------------------------
# Property 15: Tool result truncation correctness
# ---------------------------------------------------------------------------


class TestToolResultTruncation:
    """
    **Property 15: Tool result truncation correctness**

    Content > max_chars → head + tail + omission marker with original count;
    content ≤ max_chars → unchanged.

    **Validates: Requirements 12.1, 12.2, 12.3**
    """

    @given(content=_tool_content_over)
    @settings(max_examples=100)
    def test_long_content_is_truncated(self, content: str):
        """Content > max_chars should be truncated with head + marker + tail."""
        assume(len(content) > 2000)
        compressor = ContextCompressor()
        msg = _make_tool_result_msg(content)

        result, stats = compressor.compress([msg], "conservative")

        block = result[0]["content"][0]
        truncated = block["content"]

        # Must start with head (first 500 chars)
        assert truncated.startswith(content[:500])
        # Must end with tail (last 500 chars)
        assert truncated.endswith(content[-500:])
        # Must contain omission marker with original char count
        assert f"已省略 {len(content)} 字符中的" in truncated
        # Omitted count should be correct
        omitted = len(content) - 500 - 500
        assert f"{omitted} 字符" in truncated
        # Truncated content should be shorter than original
        assert len(truncated) < len(content)

    @given(content=_tool_content_under)
    @settings(max_examples=100)
    def test_short_content_unchanged(self, content: str):
        """Content ≤ max_chars should remain unchanged."""
        assume(len(content) <= 2000)
        compressor = ContextCompressor()
        msg = _make_tool_result_msg(content)

        result, stats = compressor.compress([msg], "conservative")

        block = result[0]["content"][0]
        assert block["content"] == content



# ---------------------------------------------------------------------------
# Property 16: Compression strategy off passthrough
# ---------------------------------------------------------------------------


# Strategy for generating random message lists
_simple_messages = st.lists(
    st.one_of(
        st.builds(
            _make_user_msg,
            content=st.text(min_size=1, max_size=200),
        ),
        st.builds(
            _make_assistant_msg,
            content=st.text(min_size=1, max_size=200),
        ),
    ),
    min_size=1,
    max_size=20,
)


class TestCompressionOffPassthrough:
    """
    **Property 16: Compression strategy off passthrough**

    Strategy "off" returns messages unchanged with savings_ratio=0.0.

    **Validates: Requirements 12.4**
    """

    @given(messages=_simple_messages)
    @settings(max_examples=100)
    def test_off_strategy_returns_unchanged(self, messages: list[dict]):
        """Strategy 'off' should return messages unchanged with savings_ratio=0.0."""
        compressor = ContextCompressor()

        result, stats = compressor.compress(messages, "off")

        # Messages should be identical (same object, not even copied)
        assert result is messages
        # savings_ratio must be 0.0
        assert stats.savings_ratio == 0.0
        # original_chars == compressed_chars
        assert stats.original_chars == stats.compressed_chars


# ---------------------------------------------------------------------------
# Property 17: History folding strategy awareness
# ---------------------------------------------------------------------------


class TestHistoryFoldingStrategyAwareness:
    """
    **Property 17: History folding strategy awareness**

    aggressive/moderate fold old long assistant messages;
    conservative does not fold;
    messages ≤ fold_min_length never folded.

    **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5**
    """

    @given(
        num_extra_turns=st.integers(min_value=1, max_value=5),
        assistant_text_len=st.integers(min_value=201, max_value=500),
    )
    @settings(max_examples=100)
    def test_aggressive_folds_old_long_messages(
        self, num_extra_turns: int, assistant_text_len: int
    ):
        """Aggressive strategy folds old long assistant messages beyond fold boundary."""
        compressor = ContextCompressor(fold_after_turns=3, fold_min_length=200, fold_summary_length=150)
        # Build conversation: extra turns (will be folded) + 3 recent turns (kept)
        total_turns = num_extra_turns + 3
        msgs = _build_conversation(total_turns, assistant_text_len)

        result, stats = compressor.compress(msgs, "aggressive")

        # The fold boundary: keep last fold_after_turns*2 = 6 messages
        fold_boundary = len(msgs) - 6
        for i, msg in enumerate(result):
            if i < fold_boundary and msg.get("role") == "assistant":
                # Old assistant messages should be folded (truncated to summary)
                text = msg["content"] if isinstance(msg["content"], str) else ""
                if isinstance(msg["content"], list):
                    text = " ".join(
                        b.get("text", "") for b in msg["content"]
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                assert text.endswith("...")
                assert len(text) <= 154  # fold_summary_length + len("...")

    @given(
        num_extra_turns=st.integers(min_value=1, max_value=5),
        assistant_text_len=st.integers(min_value=201, max_value=500),
    )
    @settings(max_examples=100)
    def test_moderate_folds_old_long_messages(
        self, num_extra_turns: int, assistant_text_len: int
    ):
        """Moderate strategy also folds old long assistant messages."""
        compressor = ContextCompressor(fold_after_turns=3, fold_min_length=200, fold_summary_length=150)
        total_turns = num_extra_turns + 3
        msgs = _build_conversation(total_turns, assistant_text_len)

        result, stats = compressor.compress(msgs, "moderate")

        fold_boundary = len(msgs) - 6
        for i, msg in enumerate(result):
            if i < fold_boundary and msg.get("role") == "assistant":
                text = msg["content"] if isinstance(msg["content"], str) else ""
                if isinstance(msg["content"], list):
                    text = " ".join(
                        b.get("text", "") for b in msg["content"]
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                assert text.endswith("...")
                assert len(text) <= 154

    @given(
        num_extra_turns=st.integers(min_value=1, max_value=5),
        assistant_text_len=st.integers(min_value=201, max_value=500),
    )
    @settings(max_examples=100)
    def test_conservative_does_not_fold(
        self, num_extra_turns: int, assistant_text_len: int
    ):
        """Conservative strategy should NOT fold any assistant messages."""
        compressor = ContextCompressor(fold_after_turns=3, fold_min_length=200, fold_summary_length=150)
        total_turns = num_extra_turns + 3
        msgs = _build_conversation(total_turns, assistant_text_len)
        original_assistant_contents = [
            m["content"] for m in msgs if m.get("role") == "assistant"
        ]

        result, stats = compressor.compress(msgs, "conservative")

        result_assistant_contents = [
            m["content"] for m in result if m.get("role") == "assistant"
        ]
        # All assistant messages should be unchanged
        assert result_assistant_contents == original_assistant_contents

    @given(
        num_extra_turns=st.integers(min_value=1, max_value=5),
        assistant_text_len=st.integers(min_value=1, max_value=200),
        strategy=st.sampled_from(["aggressive", "moderate"]),
    )
    @settings(max_examples=100)
    def test_short_messages_never_folded(
        self, num_extra_turns: int, assistant_text_len: int, strategy: str
    ):
        """Messages ≤ fold_min_length should never be folded regardless of strategy."""
        compressor = ContextCompressor(fold_after_turns=3, fold_min_length=200, fold_summary_length=150)
        total_turns = num_extra_turns + 3
        msgs = _build_conversation(total_turns, assistant_text_len)
        original_assistant_contents = [
            m["content"] for m in msgs if m.get("role") == "assistant"
        ]

        result, stats = compressor.compress(msgs, strategy)

        result_assistant_contents = [
            m["content"] for m in result if m.get("role") == "assistant"
        ]
        # Short assistant messages should remain unchanged
        assert result_assistant_contents == original_assistant_contents


# ---------------------------------------------------------------------------
# Property 18: Compression stats accuracy
# ---------------------------------------------------------------------------


class TestCompressionStatsAccuracy:
    """
    **Property 18: Compression stats accuracy**

    original_chars >= compressed_chars,
    savings_ratio = 1 - compressed/original when original > 0.

    **Validates: Requirements 14.2**
    """

    @given(
        messages=st.lists(
            st.one_of(
                st.builds(
                    _make_user_msg,
                    content=st.text(min_size=1, max_size=500),
                ),
                st.builds(
                    _make_assistant_msg,
                    content=st.text(min_size=1, max_size=500),
                ),
            ),
            min_size=1,
            max_size=20,
        ),
        strategy=st.sampled_from(["aggressive", "moderate", "conservative"]),
    )
    @settings(max_examples=100)
    def test_stats_accuracy(self, messages: list[dict], strategy: str):
        """Compression stats should be accurate: original >= compressed, ratio correct."""
        compressor = ContextCompressor()

        result, stats = compressor.compress(messages, strategy)

        # original_chars >= compressed_chars
        assert stats.original_chars >= stats.compressed_chars

        # savings_ratio = 1 - compressed/original when original > 0
        if stats.original_chars > 0:
            expected_ratio = 1.0 - (stats.compressed_chars / stats.original_chars)
            assert abs(stats.savings_ratio - expected_ratio) < 1e-9
        else:
            assert stats.savings_ratio == 0.0
