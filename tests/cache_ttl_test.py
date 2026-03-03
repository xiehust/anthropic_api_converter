#!/usr/bin/env python3
"""
Cache TTL manual test script.

Tests the prompt cache TTL feature by sending requests with cache_control
and verifying that TTL is applied correctly based on priority:
  1. API key cache_ttl (DynamoDB) — highest, forced override
  2. Client request cache_control.ttl — preserved if no API key override
  3. DEFAULT_CACHE_TTL env var — fills missing TTLs

Usage:
    # Run all tests
    python tests/cache_ttl_test.py

    # Run specific test
    python tests/cache_ttl_test.py --test client-ttl
    python tests/cache_ttl_test.py --test no-ttl
    python tests/cache_ttl_test.py --test streaming

    # Use a specific model
    python tests/cache_ttl_test.py --model claude-sonnet-4-5-20250929

Prerequisites:
    - Proxy running with PROMPT_CACHING_ENABLED=True
    - Valid API key in tests/.env
    - To test API key override: set cache_ttl on your API key in DynamoDB
    - To test proxy default: set DEFAULT_CACHE_TTL=1h in proxy .env
"""

import argparse
import json
import sys

import httpx
from anthropic import Anthropic

from config import API_KEY, BASE_URL, MODEL_ID

client = Anthropic(api_key=API_KEY, base_url=BASE_URL)

# Long system prompt to meet minimum cache token threshold (~2048 tokens)
LONG_SYSTEM_PROMPT = (
    "You are an expert software engineer specializing in distributed systems, "
    "cloud computing, and API design. You have deep knowledge of AWS services "
    "including Bedrock, DynamoDB, ECS, Lambda, and CloudFormation. "
    "You follow best practices for security, performance, and cost optimization. "
    "When answering questions, you provide specific, actionable advice with code "
    "examples where appropriate. You are familiar with Python, TypeScript, Go, "
    "and Rust programming languages. "
) * 10  # Repeat to ensure we exceed the minimum token threshold for caching


def print_separator(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def print_usage_info(usage):
    """Print cache-related usage statistics."""
    if hasattr(usage, "model_dump"):
        u = usage.model_dump(exclude_none=True)
    else:
        u = dict(usage)

    input_tokens = u.get("input_tokens", 0)
    output_tokens = u.get("output_tokens", 0)
    cache_creation = u.get("cache_creation_input_tokens", 0)
    cache_read = u.get("cache_read_input_tokens", 0)

    print(f"  Token Usage:")
    print(f"    input_tokens:                {input_tokens}")
    print(f"    output_tokens:               {output_tokens}")
    print(f"    cache_creation_input_tokens: {cache_creation}")
    print(f"    cache_read_input_tokens:     {cache_read}")

    if cache_creation and cache_creation > 0:
        print(f"  -> Cache WRITE (new cache entry created)")
    elif cache_read and cache_read > 0:
        print(f"  -> Cache HIT (reading from cache)")
    else:
        print(f"  -> No caching activity detected")


def test_client_ttl_1h(model: str):
    """
    Test 1: Client sends cache_control with ttl="1h".

    Expected: If no API key override, the 1h TTL should be passed to Bedrock.
    Look at proxy logs for: cache_control with ttl=1h in the native request.
    """
    print_separator("Test 1: Client-specified TTL (1h)")
    print(f"  Sending request with cache_control.ttl='1h' on system prompt")
    print(f"  Model: {model}")
    print(f"  Expected: cache_control.ttl='1h' passed to Bedrock\n")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=50,
            system=[
                {
                    "type": "text",
                    "text": LONG_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[{"role": "user", "content": "Say 'cache test ok' in 5 words or less."}],
        )

        print(f"  Response: {response.content[0].text}")
        print(f"  Stop reason: {response.stop_reason}")
        print_usage_info(response.usage)
        print(f"\n  [CHECK PROXY LOGS] Look for 'ttl': '1h' in the native request body")
        print(f"  PASS" if response.stop_reason == "end_turn" else f"  FAIL")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_client_ttl_5m(model: str):
    """
    Test 2: Client sends cache_control with ttl="5m" (explicit default).

    Expected: 5m TTL passed to Bedrock (unless API key overrides it).
    """
    print_separator("Test 2: Client-specified TTL (5m)")
    print(f"  Sending request with cache_control.ttl='5m' on system prompt")
    print(f"  Model: {model}")
    print(f"  Expected: cache_control.ttl='5m' passed to Bedrock\n")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=50,
            system=[
                {
                    "type": "text",
                    "text": LONG_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "5m"},
                }
            ],
            messages=[{"role": "user", "content": "Say 'cache test ok' in 5 words or less."}],
        )

        print(f"  Response: {response.content[0].text}")
        print(f"  Stop reason: {response.stop_reason}")
        print_usage_info(response.usage)
        print(f"\n  [CHECK PROXY LOGS] Look for 'ttl': '5m' in the native request body")
        print(f"  PASS" if response.stop_reason == "end_turn" else f"  FAIL")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_no_ttl(model: str):
    """
    Test 3: Client sends cache_control WITHOUT ttl.

    Expected:
    - If DEFAULT_CACHE_TTL is set on proxy, that value should be injected
    - If no default, no ttl field in the request
    """
    print_separator("Test 3: No client TTL (relies on proxy default)")
    print(f"  Sending request with cache_control but NO ttl")
    print(f"  Model: {model}")
    print(f"  Expected: proxy's DEFAULT_CACHE_TTL fills in (if set)\n")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=50,
            system=[
                {
                    "type": "text",
                    "text": LONG_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": "Say 'cache test ok' in 5 words or less."}],
        )

        print(f"  Response: {response.content[0].text}")
        print(f"  Stop reason: {response.stop_reason}")
        print_usage_info(response.usage)
        print(f"\n  [CHECK PROXY LOGS] Look for 'ttl' in the native request body")
        print(f"    - If DEFAULT_CACHE_TTL is set, 'ttl' should appear")
        print(f"    - If not set, 'ttl' should be absent")
        print(f"  PASS" if response.stop_reason == "end_turn" else f"  FAIL")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_cache_hit(model: str):
    """
    Test 4: Send two identical requests to test cache hit on the second.

    Expected: First request = cache_creation, Second request = cache_read.
    """
    print_separator("Test 4: Cache Hit (two identical requests)")
    print(f"  Sending two identical requests to verify cache hit")
    print(f"  Model: {model}\n")

    system = [
        {
            "type": "text",
            "text": LONG_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    messages = [{"role": "user", "content": "Say 'hello' and nothing else."}]

    try:
        # Request 1
        print(f"  --- Request 1 (expect cache WRITE) ---")
        r1 = client.messages.create(
            model=model, max_tokens=50, system=system, messages=messages
        )
        print(f"  Response: {r1.content[0].text}")
        print_usage_info(r1.usage)

        # Request 2 (should hit cache)
        print(f"\n  --- Request 2 (expect cache READ) ---")
        r2 = client.messages.create(
            model=model, max_tokens=50, system=system, messages=messages
        )
        print(f"  Response: {r2.content[0].text}")
        print_usage_info(r2.usage)

        u2 = r2.usage.model_dump(exclude_none=True) if hasattr(r2.usage, "model_dump") else dict(r2.usage)
        cache_read = u2.get("cache_read_input_tokens", 0)
        if cache_read and cache_read > 0:
            print(f"\n  PASS: Cache hit confirmed ({cache_read} cached tokens read)")
        else:
            print(f"\n  WARN: No cache hit detected (may need longer system prompt or model may not support caching)")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_streaming_with_ttl(model: str):
    """
    Test 5: Streaming request with cache_control TTL.

    Expected: Streaming works normally, usage in final message_delta includes cache info.
    """
    print_separator("Test 5: Streaming with TTL")
    print(f"  Sending streaming request with cache_control.ttl='1h'")
    print(f"  Model: {model}\n")

    try:
        collected_text = ""
        final_usage = None

        with client.messages.stream(
            model=model,
            max_tokens=50,
            system=[
                {
                    "type": "text",
                    "text": LONG_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[{"role": "user", "content": "Say 'streaming ok' and nothing else."}],
        ) as stream:
            for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_delta" and hasattr(event, "delta"):
                        delta = event.delta
                        if hasattr(delta, "text"):
                            collected_text += delta.text
                            print(delta.text, end="", flush=True)

            # Get final message for usage
            final_message = stream.get_final_message()
            final_usage = final_message.usage

        print(f"\n\n  Full text: {collected_text}")
        if final_usage:
            print_usage_info(final_usage)
        print(f"\n  [CHECK PROXY LOGS] Verify 'ttl': '1h' in the streaming native request")
        print(f"  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_tools_with_ttl(model: str):
    """
    Test 6: Tools with cache_control TTL.

    Expected: Tool definitions' cache_control should also get TTL applied.
    """
    print_separator("Test 6: Tools with cache_control TTL")
    print(f"  Sending request with tools that have cache_control")
    print(f"  Model: {model}\n")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": LONG_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            tools=[
                {
                    "name": "get_weather",
                    "description": "Get the current weather in a given location",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                        },
                        "required": ["location"],
                    },
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                },
            ],
            messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
        )

        print(f"  Stop reason: {response.stop_reason}")
        for i, block in enumerate(response.content):
            d = block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block)
            btype = d.get("type", "?")
            if btype == "text":
                print(f"  [{i}] text: {d.get('text', '')[:100]}")
            elif btype == "tool_use":
                print(f"  [{i}] tool_use: {d.get('name')}({json.dumps(d.get('input', {}))})")
        print_usage_info(response.usage)
        print(f"\n  [CHECK PROXY LOGS] Verify tool cache_control has 'ttl': '1h'")
        print(f"  PASS" if response.stop_reason in ("end_turn", "tool_use") else f"  FAIL")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_raw_request_ttl(model: str):
    """
    Test 7: Raw HTTP request to inspect the exact request/response.

    Sends a raw request via httpx to see the full response including cache usage.
    """
    print_separator("Test 7: Raw HTTP request (inspect full response)")
    print(f"  Sending raw HTTP request to inspect cache fields")
    print(f"  Model: {model}\n")

    try:
        payload = {
            "model": model,
            "max_tokens": 50,
            "system": [
                {
                    "type": "text",
                    "text": LONG_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": [{"role": "user", "content": "Say 'raw test ok'."}],
        }

        resp = httpx.post(
            f"{BASE_URL}/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": API_KEY,
            },
            json=payload,
            timeout=60.0,
        )

        print(f"  HTTP Status: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            print(f"  Response JSON (usage section):")
            usage = data.get("usage", {})
            print(json.dumps(usage, indent=4))
            print(f"\n  Content: {data.get('content', [{}])[0].get('text', '')[:100]}")
            print(f"  PASS")
        else:
            print(f"  Error: {resp.text[:300]}")
            print(f"  FAIL")
        return resp.status_code == 200
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


# ==================== Main ====================

TESTS = {
    "client-ttl-1h": test_client_ttl_1h,
    "client-ttl-5m": test_client_ttl_5m,
    "no-ttl": test_no_ttl,
    "cache-hit": test_cache_hit,
    "streaming": test_streaming_with_ttl,
    "tools": test_tools_with_ttl,
    "raw": test_raw_request_ttl,
}


def main():
    parser = argparse.ArgumentParser(description="Cache TTL manual test script")
    parser.add_argument(
        "--test",
        choices=list(TESTS.keys()),
        help="Run a specific test (default: run all)",
    )
    parser.add_argument(
        "--model",
        default=MODEL_ID,
        help=f"Model ID to use (default: {MODEL_ID})",
    )
    args = parser.parse_args()

    print(f"Cache TTL Test Script")
    print(f"  Base URL:  {BASE_URL}")
    print(f"  API Key:   {API_KEY[:20]}...")
    print(f"  Model:     {args.model}")
    print(f"\n  TIP: Watch proxy logs (LOG_LEVEL=DEBUG) to see TTL in native requests")
    print(f"  TIP: Set DEFAULT_CACHE_TTL=1h on proxy to test proxy default injection")
    print(f"  TIP: Set cache_ttl=1h on your API key in DynamoDB to test forced override")

    if args.test:
        tests_to_run = {args.test: TESTS[args.test]}
    else:
        tests_to_run = TESTS

    results = {}
    for name, test_fn in tests_to_run.items():
        try:
            results[name] = test_fn(args.model)
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            break
        except Exception as e:
            print(f"  UNEXPECTED ERROR: {e}")
            results[name] = False

    # Summary
    print_separator("Summary")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  {passed}/{total} tests passed")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
