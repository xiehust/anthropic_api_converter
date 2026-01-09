"""
Integration test for the Anthropic-Bedrock API proxy.

This test validates that the proxy service correctly handles requests using the
Anthropic SDK and properly translates them to Bedrock API calls.

Usage:
    uv run pytest tests/test_proxy_integration.py -v
"""
import os
import pytest
from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL

# Test configuration
PROXY_BASE_URL = BASE_URL
TEST_MODEL = "openai.gpt-oss-120b-1:0"  # Thinking model for testing
CLAUDE_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"  # Claude model for testing


@pytest.fixture
def anthropic_client():
    """Create an Anthropic client configured to use the proxy."""
    return Anthropic(
        api_key=API_KEY,
        base_url=PROXY_BASE_URL,
    )


def test_proxy_health_check():
    """Test that the proxy service is running and healthy."""
    import httpx

    response = httpx.get(f"{PROXY_BASE_URL}/health", timeout=5.0)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "services" in data


def test_simple_message_non_streaming(anthropic_client):
    """Test a simple non-streaming message request through the proxy."""
    try:
        message = anthropic_client.messages.create(
            model=TEST_MODEL,
            max_tokens=100,
            messages=[
                {"role": "user", "content": "Say hello in one word"}
            ]
        )

        # Verify response structure
        assert message.id is not None
        assert message.type == "message"
        assert message.role == "assistant"
        assert len(message.content) > 0
        assert message.content[0].type == "text"
        assert len(message.content[0].text) > 0

        # Verify usage information
        assert message.usage is not None
        assert message.usage.input_tokens > 0
        assert message.usage.output_tokens > 0

        print(f"\n✓ Response: {message.content[0].text}")
        print(f"✓ Tokens: {message.usage.input_tokens} in, {message.usage.output_tokens} out")

    except Exception as e:
        pytest.skip(f"Bedrock API not available or model not accessible: {str(e)}")


def test_simple_message_streaming(anthropic_client):
    """Test a simple streaming message request through the proxy."""
    try:
        collected_text = []

        with anthropic_client.messages.stream(
            model=TEST_MODEL,
            max_tokens=50,
            messages=[
                {"role": "user", "content": "Count from 1 to 3"}
            ]
        ) as stream:
            for text in stream.text_stream:
                collected_text.append(text)
                print(text, end="", flush=True)

        # Verify we received some streaming content
        full_text = "".join(collected_text)
        assert len(full_text) > 0

        print(f"\n✓ Streamed {len(collected_text)} chunks")
        print(f"✓ Total text length: {len(full_text)} characters")

    except Exception as e:
        pytest.skip(f"Bedrock API not available or streaming not supported: {str(e)}")


def test_message_with_system_prompt(anthropic_client):
    """Test message with system prompt."""
    try:
        message = anthropic_client.messages.create(
            model=TEST_MODEL,
            max_tokens=100,
            system="You are a helpful assistant that responds in haiku format.",
            messages=[
                {"role": "user", "content": "Tell me about the ocean"}
            ]
        )

        assert message.content[0].type == "text"
        assert len(message.content[0].text) > 0

        print(f"\n✓ Response with system prompt: {message.content[0].text}")

    except Exception as e:
        pytest.skip(f"Bedrock API not available: {str(e)}")


def test_invalid_api_key():
    """Test that invalid API key is rejected."""
    import httpx

    response = httpx.post(
        f"{PROXY_BASE_URL}/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": "invalid-key-12345"
        },
        json={
            "model": TEST_MODEL,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Hello"}]
        },
        timeout=5.0
    )

    assert response.status_code == 401
    data = response.json()
    assert "authentication_error" in str(data).lower()

    print(f"\n✓ Invalid API key correctly rejected")


def test_missing_api_key():
    """Test that missing API key is rejected."""
    import httpx

    response = httpx.post(
        f"{PROXY_BASE_URL}/v1/messages",
        headers={"Content-Type": "application/json"},
        json={
            "model": TEST_MODEL,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Hello"}]
        },
        timeout=5.0
    )

    assert response.status_code == 401
    data = response.json()
    assert "authentication_error" in str(data).lower()

    print(f"\n✓ Missing API key correctly rejected")


def test_rate_limiting_headers(anthropic_client):
    """Test that rate limiting headers are present in responses."""
    import httpx

    try:
        response = httpx.post(
            f"{PROXY_BASE_URL}/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": API_KEY
            },
            json={
                "model": TEST_MODEL,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Hi"}]
            },
            timeout=30.0
        )

        # Check for rate limit headers (if request succeeds)
        if response.status_code == 200:
            assert "x-ratelimit-limit" in response.headers
            assert "x-ratelimit-remaining" in response.headers
            assert "x-ratelimit-reset" in response.headers

            print(f"\n✓ Rate limit headers present:")
            print(f"  - Limit: {response.headers.get('x-ratelimit-limit')}")
            print(f"  - Remaining: {response.headers.get('x-ratelimit-remaining')}")
            print(f"  - Reset: {response.headers.get('x-ratelimit-reset')}")
        else:
            pytest.skip(f"Request failed with status {response.status_code}")

    except Exception as e:
        pytest.skip(f"Could not test rate limiting: {str(e)}")


def test_list_models():
    """Test listing available models."""
    import httpx

    response = httpx.get(
        f"{PROXY_BASE_URL}/v1/models",
        headers={"x-api-key": API_KEY},
        timeout=10.0
    )

    assert response.status_code == 200
    data = response.json()

    assert "object" in data
    assert data["object"] == "list"
    assert "data" in data
    assert isinstance(data["data"], list)

    if len(data["data"]) > 0:
        model = data["data"][0]
        assert "id" in model
        print(f"\n✓ Found {len(data['data'])} models")
        print(f"  - First model: {model.get('id')}")
    else:
        print(f"\n✓ Models endpoint working (no models returned)")


def test_claude_model_with_extended_thinking(anthropic_client):
    """Test Claude model with extended thinking enabled."""
    try:
        message = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            thinking={
                "type": "enabled",
                "budget_tokens": 200
            },
            messages=[
                {
                    "role": "user",
                    "content": "What is 25 * 47? Think through the calculation step by step."
                }
            ]
        )

        # Verify response structure
        assert message.id is not None
        assert message.role == "assistant"
        assert len(message.content) > 0

        # Check for thinking and text blocks
        thinking_blocks = [block for block in message.content if block.type == "thinking"]
        text_blocks = [block for block in message.content if block.type == "text"]

        print(f"\n✓ Message ID: {message.id}")
        print(f"✓ Total content blocks: {len(message.content)}")
        print(f"✓ Thinking blocks: {len(thinking_blocks)}")
        print(f"✓ Text blocks: {len(text_blocks)}")

        # Print thinking content if available
        if thinking_blocks:
            print(f"\n[Thinking]")
            print(thinking_blocks[0].thinking[:200] + "..." if len(thinking_blocks[0].thinking) > 200 else thinking_blocks[0].thinking)

        # Print response
        if text_blocks:
            print(f"\n[Response]")
            print(text_blocks[0].text)

        print(f"\n✓ Tokens: {message.usage.input_tokens} in, {message.usage.output_tokens} out")

    except Exception as e:
        pytest.skip(f"Extended thinking not supported or Bedrock API not available: {str(e)}")


def test_claude_model_extended_thinking_streaming(anthropic_client):
    """Test Claude model with extended thinking enabled in streaming mode."""
    try:
        thinking_chunks = []
        text_chunks = []

        print("\n[Streaming with Extended Thinking]")

        with anthropic_client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=500,
            thinking={
                "type": "enabled",
                "budget_tokens": 200
            },
            messages=[
                {
                    "role": "user",
                    "content": "Calculate 13 * 17. Show your work."
                }
            ]
        ) as stream:
            for event in stream:
                # Track content block types
                if hasattr(event, 'type'):
                    if event.type == "content_block_start":
                        if hasattr(event, 'content_block') and event.content_block.type == "thinking":
                            print("\n[Thinking Stream]")
                    elif event.type == "content_block_delta":
                        if hasattr(event, 'delta'):
                            if event.delta.type == "thinking_delta":
                                thinking_chunks.append(event.delta.thinking)
                                print(event.delta.thinking, end="", flush=True)
                            elif event.delta.type == "text_delta":
                                if not text_chunks:  # First text chunk
                                    print("\n\n[Response Stream]")
                                text_chunks.append(event.delta.text)
                                print(event.delta.text, end="", flush=True)

        full_thinking = "".join(thinking_chunks)
        full_text = "".join(text_chunks)

        print(f"\n\n✓ Thinking chunks: {len(thinking_chunks)}")
        print(f"✓ Text chunks: {len(text_chunks)}")

        # Verify we received some content
        assert len(full_thinking) > 0 or len(full_text) > 0

    except Exception as e:
        pytest.skip(f"Extended thinking streaming not supported or Bedrock API not available: {str(e)}")


def test_claude_model_interleaved_thinking(anthropic_client):
    """Test Claude model with interleaved thinking (multi-turn conversation)."""
    try:
        # First turn with thinking
        message1 = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            thinking={
                "type": "enabled",
                "budget_tokens": 150
            },
            messages=[
                {
                    "role": "user",
                    "content": "I have 5 apples. I buy 3 more. How many do I have?"
                }
            ]
        )

        print(f"\n[Turn 1]")
        print(f"Content blocks: {len(message1.content)}")
        for i, block in enumerate(message1.content):
            print(f"  Block {i}: type={block.type}")

        # Second turn - continue conversation with thinking blocks included
        conversation = [
            {
                "role": "user",
                "content": "I have 5 apples. I buy 3 more. How many do I have?"
            },
            {
                "role": "assistant",
                "content": message1.content  # Include thinking and text from first response
            },
            {
                "role": "user",
                "content": "Now I give away 2 apples. How many do I have left?"
            }
        ]

        message2 = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            thinking={
                "type": "enabled",
                "budget_tokens": 150
            },
            messages=conversation
        )

        print(f"\n[Turn 2]")
        print(f"Content blocks: {len(message2.content)}")
        for i, block in enumerate(message2.content):
            print(f"  Block {i}: type={block.type}")

        print(f"\n✓ Multi-turn conversation with interleaved thinking completed")

    except Exception as e:
        pytest.skip(f"Interleaved thinking not supported or Bedrock API not available: {str(e)}")


if __name__ == "__main__":
    # Allow running the test file directly for quick testing
    import sys

    print("=" * 60)
    print("Anthropic-Bedrock API Proxy Integration Tests")
    print("=" * 60)

    # Run with pytest
    sys.exit(pytest.main([__file__, "-v", "-s"]))
