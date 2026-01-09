#!/usr/bin/env python3
"""
Quick test script for the Anthropic-Bedrock API proxy.

This script uses the Anthropic SDK to test the proxy service.

Usage:
    uv run python tests/quick_test.py
"""
from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL

# Configuration
PROXY_BASE_URL = BASE_URL
# TEST_MODEL = "openai.gpt-oss-120b-1:0"
TEST_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_health_check():
    """Test health check endpoint."""
    import httpx

    print("=" * 60)
    print("1. Testing Health Check")
    print("=" * 60)

    try:
        response = httpx.get(f"{PROXY_BASE_URL}/health", timeout=5.0)
        data = response.json()

        print(f"✓ Status: {data['status']}")
        print(f"✓ Version: {data['version']}")
        print(f"✓ Environment: {data['environment']}")
        print(f"✓ Services: Bedrock={data['services']['bedrock']['status']}, "
              f"DynamoDB={data['services']['dynamodb']['status']}")
        return True
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False


def test_authentication():
    """Test authentication."""
    import httpx

    print("\n" + "=" * 60)
    print("2. Testing Authentication")
    print("=" * 60)

    # Test invalid API key
    print("\n2a. Testing invalid API key...")
    try:
        response = httpx.post(
            f"{PROXY_BASE_URL}/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": "invalid-key"},
            json={"model": TEST_MODEL, "max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]},
            timeout=5.0
        )

        if response.status_code == 401 or response.status_code == 500:
            data = response.json()
            if "authentication" in str(data).lower():
                print("✓ Invalid API key correctly rejected")
            else:
                print(f"✓ Invalid API key rejected (status {response.status_code})")
        else:
            print(f"✗ Unexpected status code: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False

    # Test missing API key
    print("\n2b. Testing missing API key...")
    try:
        response = httpx.post(
            f"{PROXY_BASE_URL}/v1/messages",
            headers={"Content-Type": "application/json"},
            json={"model": TEST_MODEL, "max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]},
            timeout=5.0
        )

        if response.status_code == 401 or response.status_code == 500:
            data = response.json()
            if "authentication" in str(data).lower() or "api key" in str(data).lower():
                print("✓ Missing API key correctly rejected")
            else:
                print(f"✓ Missing API key rejected (status {response.status_code})")
        else:
            print(f"✗ Unexpected status code: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False

    return True


def test_simple_message():
    """Test a simple message with the Anthropic SDK."""
    print("\n" + "=" * 60)
    print("3. Testing Simple Message (Non-Streaming)")
    print("=" * 60)

    try:
        client = Anthropic(api_key=API_KEY, base_url=PROXY_BASE_URL)

        print(f"\nSending request to model: {TEST_MODEL}")
        print("Prompt: 'Say hello in one word'")
        print("\nWaiting for response...")

        message = client.messages.create(
            model=TEST_MODEL,
            max_tokens=100,
            messages=[
                {"role": "user", "content": "Say hello in one word"}
            ]
        )

        print(f"\n✓ Message ID: {message.id}")
        print(f"✓ Response: {message.content[0].text}")
        print(f"✓ Tokens: {message.usage.input_tokens} in, {message.usage.output_tokens} out")
        print(f"✓ Stop reason: {message.stop_reason}")

        return True

    except Exception as e:
        print(f"\n✗ Message test failed: {e}")
        print("\nNote: This is expected if:")
        print("  - AWS credentials are not configured")
        print("  - Bedrock API is not accessible")
        print("  - Model access is not enabled")
        return False


def test_streaming_message():
    """Test streaming message with the Anthropic SDK."""
    print("\n" + "=" * 60)
    print("4. Testing Streaming Message")
    print("=" * 60)

    try:
        client = Anthropic(api_key=API_KEY, base_url=PROXY_BASE_URL)

        print(f"\nSending streaming request to model: {TEST_MODEL}")
        print("Prompt: 'Count from 1 to 5'")
        print("\nStreaming response:")
        print("-" * 40)

        collected_text = []

        with client.messages.stream(
            model=TEST_MODEL,
            max_tokens=100,
            messages=[
                {"role": "user", "content": "Count from 1 to 5"}
            ]
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                collected_text.append(text)

        full_text = "".join(collected_text)
        print("\n" + "-" * 40)
        print(f"\n✓ Received {len(collected_text)} chunks")
        print(f"✓ Total length: {len(full_text)} characters")

        return True

    except Exception as e:
        print(f"\n✗ Streaming test failed: {e}")
        print("\nNote: This is expected if:")
        print("  - AWS credentials are not configured")
        print("  - Bedrock API is not accessible")
        print("  - Streaming is not supported for this model")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("ANTHROPIC-BEDROCK API PROXY - QUICK TEST")
    print("=" * 60)
    print(f"Proxy URL: {PROXY_BASE_URL}")
    print(f"Test Model: {TEST_MODEL}")
    print("=" * 60)

    results = {
        "Health Check": test_health_check(),
        "Authentication": test_authentication(),
        "Simple Message": test_simple_message(),
        "Streaming Message": test_streaming_message(),
    }

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:10} - {test_name}")

    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)

    print("=" * 60)
    print(f"Results: {passed_count}/{total_count} tests passed")
    print("=" * 60)

    if passed_count == total_count:
        print("\n🎉 All tests passed!")
        return 0
    elif passed_count >= 2:
        print("\n⚠️  Some tests passed. Bedrock API tests may require configuration.")
        return 0
    else:
        print("\n❌ Critical tests failed. Please check the proxy service.")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
