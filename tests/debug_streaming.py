#!/usr/bin/env python3
"""
Debug streaming events to see the exact event structure.
"""
import httpx
import json

PROXY_BASE_URL = "http://localhost:8000"
API_KEY = "sk-"
TEST_MODEL = "openai.gpt-oss-120b-1:0"


def test_streaming_raw():
    """Test streaming with raw SSE parsing."""
    print("=" * 60)
    print("Testing Raw Streaming Events")
    print("=" * 60)

    with httpx.Client(timeout=30.0) as client:
        with client.stream(
            "POST",
            f"{PROXY_BASE_URL}/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": API_KEY,
            },
            json={
                "model": TEST_MODEL,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Count from 1 to 3"}],
                "stream": True,
            },
        ) as response:
            print(f"\nResponse status: {response.status_code}")
            print(f"Response headers: {dict(response.headers)}\n")

            if response.status_code != 200:
                print(f"Error: {response.text}")
                return

            event_count = 0
            for line in response.iter_lines():
                if not line:
                    continue

                # Parse SSE format
                if line.startswith("event: "):
                    event_type = line[7:]
                    print(f"\n[Event {event_count}] Type: {event_type}")
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        print(f"[Event {event_count}] Data: {json.dumps(data, indent=2)}")
                        event_count += 1
                    except json.JSONDecodeError as e:
                        print(f"[Event {event_count}] Failed to parse JSON: {e}")
                        print(f"Raw data: {line[6:]}")


if __name__ == "__main__":
    test_streaming_raw()
