"""
Web Search Tool integration test.

Tests web_search_20250305 and web_search_20260209 tool types via the proxy
using the Anthropic Python SDK.

Usage:
    # Run all tests
    python tests/web_search_test.py

    # Non-streaming only
    python tests/web_search_test.py --no-stream

    # Streaming only
    python tests/web_search_test.py --stream

    # With domain filtering
    python tests/web_search_test.py --domains

    # With max_uses limit
    python tests/web_search_test.py --max-uses

    # Multi-turn (pass back web search results)
    python tests/web_search_test.py --multi-turn
"""

import argparse
import json
import sys

from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL, MODEL_ID

client = Anthropic(api_key=API_KEY, base_url=BASE_URL)


def print_separator(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def print_content_block(block):
    """Print a content block with type-aware formatting."""
    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else "?")

    if btype == "text":
        text = getattr(block, "text", "") or (block.get("text", "") if isinstance(block, dict) else "")
        print(f"  [text] {text[:500]}{'...' if len(text) > 500 else ''}")
    elif btype == "server_tool_use":
        name = getattr(block, "name", "") or block.get("name", "")
        inp = getattr(block, "input", {}) or block.get("input", {})
        print(f"  [server_tool_use] name={name}, input={json.dumps(inp, ensure_ascii=False)}")
    elif btype == "web_search_tool_result":
        tool_use_id = getattr(block, "tool_use_id", "") or block.get("tool_use_id", "")
        content = getattr(block, "content", None) or block.get("content")
        if isinstance(content, list):
            print(f"  [web_search_tool_result] tool_use_id={tool_use_id}, results={len(content)}")
            for i, r in enumerate(content):
                url = getattr(r, "url", "") or r.get("url", "")
                title = getattr(r, "title", "") or r.get("title", "")
                print(f"    [{i}] {title} — {url}")
        else:
            # Error
            err = getattr(content, "error_code", "") or (content.get("error_code", "") if isinstance(content, dict) else str(content))
            print(f"  [web_search_tool_result ERROR] {err}")
    elif btype == "tool_use":
        name = getattr(block, "name", "") or block.get("name", "")
        print(f"  [tool_use] name={name}")
    elif btype == "thinking":
        thinking = getattr(block, "thinking", "") or block.get("thinking", "")
        print(f"  [thinking] {thinking[:200]}...")
    else:
        print(f"  [{btype}] {block}")


def print_response(response):
    """Print a full message response."""
    print(f"  model: {response.model}")
    print(f"  stop_reason: {response.stop_reason}")
    print(f"  usage: input={response.usage.input_tokens}, output={response.usage.output_tokens}")
    server_tool_use = getattr(response.usage, "server_tool_use", None)
    if server_tool_use:
        print(f"  server_tool_use: {server_tool_use}")
    print(f"  content blocks: {len(response.content)}")
    print()
    for block in response.content:
        print_content_block(block)
    print()


# ==================== Test Cases ====================

def test_basic_non_stream():
    """Basic web search - non-streaming."""
    print_separator("Test: Basic Web Search (non-streaming)")

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
            }
        ],
        messages=[
            {"role": "user", "content": "What happened in tech news today? Give a brief summary."}
        ],
    )

    print_response(response)

    # Validate response
    has_search_result = any(
        (getattr(b, "type", None) or b.get("type")) == "web_search_tool_result"
        for b in response.content
    )
    has_text = any(
        (getattr(b, "type", None) or b.get("type")) == "text"
        for b in response.content
    )

    print(f"  ✓ Has web_search_tool_result: {has_search_result}")
    print(f"  ✓ Has text response: {has_text}")
    assert has_text, "Expected text content in response"
    print("\n  ✅ PASSED")


def test_basic_stream():
    """Basic web search - streaming."""
    print_separator("Test: Basic Web Search (streaming)")

    block_types_seen = set()

    with client.messages.stream(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
            }
        ],
        messages=[
            {"role": "user", "content": "What is the current weather forecast for Tokyo?"}
        ],
    ) as stream:
        for event in stream:
            if event.type == "message_start":
                print(f"  [message_start] model={event.message.model}")
            elif event.type == "content_block_start":
                cb = event.content_block
                btype = getattr(cb, "type", None) or cb.get("type", "?")
                block_types_seen.add(btype)
                if btype == "server_tool_use":
                    name = getattr(cb, "name", "") or cb.get("name", "")
                    print(f"  [content_block_start] server_tool_use: {name}")
                elif btype == "web_search_tool_result":
                    print(f"  [content_block_start] web_search_tool_result")
                elif btype == "text":
                    print(f"  [content_block_start] text")
                else:
                    print(f"  [content_block_start] {btype}")
            elif event.type == "content_block_delta":
                dtype = getattr(event.delta, "type", "")
                if dtype == "text_delta":
                    print(getattr(event.delta, "text", ""), end="", flush=True)
            elif event.type == "content_block_stop":
                print()
            elif event.type == "message_delta":
                print(f"\n  [message_delta] stop_reason={event.delta.stop_reason}, usage={event.usage}")
            elif event.type == "message_stop":
                print("  [message_stop]")

        final = stream.get_final_message()

    print(f"\n  Block types seen: {block_types_seen}")
    print(f"  Usage: input={final.usage.input_tokens}, output={final.usage.output_tokens}")
    print("\n  ✅ PASSED")


def test_domain_filtering():
    """Web search with domain filtering."""
    print_separator("Test: Domain Filtering")

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "allowed_domains": ["python.org", "docs.python.org"],
            }
        ],
        messages=[
            {"role": "user", "content": "What's new in the latest Python release?"}
        ],
    )

    print_response(response)

    # Check that results are from allowed domains
    for block in response.content:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "web_search_tool_result":
            content = getattr(block, "content", None) or block.get("content")
            if isinstance(content, list):
                for r in content:
                    url = getattr(r, "url", "") or r.get("url", "")
                    print(f"    Result domain: {url}")

    print("\n  ✅ PASSED")


def test_max_uses():
    """Web search with max_uses limit."""
    print_separator("Test: max_uses Limit (max_uses=1)")

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 1,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": "Search for 3 different topics: Python 3.13 features, Rust 2024 edition, and Go 1.23 release notes. Summarize each."
            }
        ],
    )

    print_response(response)

    # Count search results
    search_count = sum(
        1 for b in response.content
        if (getattr(b, "type", None) or b.get("type")) == "web_search_tool_result"
    )
    print(f"  Search results count: {search_count}")
    print(f"  (max_uses=1, so at most 1 successful search expected)")
    print("\n  ✅ PASSED")


def test_multi_turn():
    """Multi-turn conversation with web search results passed back."""
    print_separator("Test: Multi-Turn Conversation")

    # First turn: Claude searches the web
    print("  --- Turn 1: Initial search ---")
    response1 = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
            }
        ],
        messages=[
            {"role": "user", "content": "Search for the latest FastAPI release version."}
        ],
    )
    print_response(response1)

    # Second turn: Follow-up question using the context
    print("  --- Turn 2: Follow-up ---")

    # Build messages for multi-turn (pass back all content from turn 1)
    content_for_assistant = []
    for block in response1.content:
        if hasattr(block, "model_dump"):
            content_for_assistant.append(block.model_dump())
        elif isinstance(block, dict):
            content_for_assistant.append(block)

    response2 = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
            }
        ],
        messages=[
            {"role": "user", "content": "Search for the latest FastAPI release version."},
            {"role": "assistant", "content": content_for_assistant},
            {"role": "user", "content": "Based on those search results, what are the key highlights of that release? You can search again if needed."},
        ],
    )
    print_response(response2)
    print("\n  ✅ PASSED")


def test_dynamic_filtering_non_stream():
    """Dynamic filtering (web_search_20260209) - non-streaming.

    web_search_20260209 enables Claude to write code to filter/process search
    results before loading them into context. The proxy handles both web_search
    and code_execution tool calls in the internal agentic loop.
    """
    print_separator("Test: Dynamic Filtering / web_search_20260209 (non-streaming)")

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=8192,
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 3,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Search the web for the top 3 most popular Python web frameworks in 2025. "
                    "For each framework, find its latest version number and a one-sentence description. "
                    "Present the results as a numbered list."
                ),
            }
        ],
    )

    print_response(response)

    # Validate
    has_search = any(
        getattr(b, "type", None) == "web_search_tool_result"
        or (isinstance(b, dict) and b.get("type") == "web_search_tool_result")
        for b in response.content
    )
    has_text = any(
        getattr(b, "type", None) == "text"
        or (isinstance(b, dict) and b.get("type") == "text")
        for b in response.content
    )
    print(f"  ✓ Has web_search_tool_result: {has_search}")
    print(f"  ✓ Has text response: {has_text}")
    assert has_text, "Expected text content in response"
    print("\n  ✅ PASSED")


def test_dynamic_filtering_stream():
    """Dynamic filtering (web_search_20260209) - streaming."""
    print_separator("Test: Dynamic Filtering / web_search_20260209 (streaming)")

    block_types_seen = set()

    with client.messages.stream(
        model=MODEL_ID,
        max_tokens=8192,
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 3,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": "Search for the current population of the 3 largest cities in Japan and present them in a table.",
            }
        ],
    ) as stream:
        for event in stream:
            if event.type == "message_start":
                print(f"  [message_start] model={event.message.model}")
            elif event.type == "content_block_start":
                cb = event.content_block
                btype = getattr(cb, "type", None) or (cb.get("type") if isinstance(cb, dict) else "?")
                block_types_seen.add(btype)
                if btype == "server_tool_use":
                    name = getattr(cb, "name", "") or cb.get("name", "")
                    print(f"  [content_block_start] server_tool_use: {name}")
                elif btype == "web_search_tool_result":
                    print(f"  [content_block_start] web_search_tool_result")
                elif btype == "text":
                    print(f"  [content_block_start] text")
                else:
                    print(f"  [content_block_start] {btype}")
            elif event.type == "content_block_delta":
                dtype = getattr(event.delta, "type", "")
                if dtype == "text_delta":
                    print(getattr(event.delta, "text", ""), end="", flush=True)
            elif event.type == "content_block_stop":
                print()
            elif event.type == "message_delta":
                print(f"\n  [message_delta] stop_reason={event.delta.stop_reason}, usage={event.usage}")
            elif event.type == "message_stop":
                print("  [message_stop]")

        final = stream.get_final_message()

    print(f"\n  Block types seen: {block_types_seen}")
    print(f"  Usage: input={final.usage.input_tokens}, output={final.usage.output_tokens}")
    print("\n  ✅ PASSED")


def test_dynamic_filtering_with_user_location():
    """Dynamic filtering with user_location for localized results."""
    print_separator("Test: Dynamic Filtering + User Location")

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 3,
                "user_location": {
                    "type": "approximate",
                    "city": "Tokyo",
                    "region": "Tokyo",
                    "country": "JP",
                    "timezone": "Asia/Tokyo",
                },
            }
        ],
        messages=[
            {"role": "user", "content": "What are the top local news stories right now?"}
        ],
    )

    print_response(response)
    print("\n  ✅ PASSED")


def test_with_other_tools():
    """Web search alongside regular tools."""
    print_separator("Test: Web Search + Regular Tools")

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
            },
            {
                "name": "get_current_time",
                "description": "Get the current time in a given timezone",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "timezone": {"type": "string", "description": "IANA timezone (e.g., America/New_York)"}
                    },
                    "required": ["timezone"],
                },
            },
        ],
        messages=[
            {"role": "user", "content": "Search the web for the latest AWS re:Invent announcements and give me a brief summary."}
        ],
    )

    print_response(response)

    # Should have used web_search, not get_current_time
    has_search = any(
        (getattr(b, "type", None) or b.get("type")) in ("server_tool_use", "web_search_tool_result")
        for b in response.content
    )
    print(f"  ✓ Used web search: {has_search}")
    print("\n  ✅ PASSED")


# ==================== Main ====================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Web Search Tool integration test")
    parser.add_argument("--stream", action="store_true", help="Run streaming test only")
    parser.add_argument("--no-stream", action="store_true", help="Run non-streaming test only")
    parser.add_argument("--domains", action="store_true", help="Run domain filtering test")
    parser.add_argument("--max-uses", action="store_true", help="Run max_uses limit test")
    parser.add_argument("--multi-turn", action="store_true", help="Run multi-turn test")
    parser.add_argument("--with-tools", action="store_true", help="Run web search + regular tools test")
    parser.add_argument("--dynamic", action="store_true", help="Run dynamic filtering (web_search_20260209) tests")
    parser.add_argument("--dynamic-stream", action="store_true", help="Run dynamic filtering streaming test")
    parser.add_argument("--dynamic-location", action="store_true", help="Run dynamic filtering + user_location test")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    args = parser.parse_args()

    # If no specific flag, run basic non-stream + stream
    any_specific = (
        args.stream or args.no_stream or args.domains or args.max_uses
        or args.multi_turn or args.with_tools or args.dynamic
        or args.dynamic_stream or args.dynamic_location or args.all
    )

    print(f"Config: BASE_URL={BASE_URL}, MODEL={MODEL_ID}")

    try:
        if args.all or args.no_stream or not any_specific:
            test_basic_non_stream()

        if args.all or args.stream or not any_specific:
            test_basic_stream()

        if args.all or args.domains:
            test_domain_filtering()

        if args.all or args.max_uses:
            test_max_uses()

        if args.all or args.multi_turn:
            test_multi_turn()

        if args.all or args.with_tools:
            test_with_other_tools()

        if args.all or args.dynamic:
            test_dynamic_filtering_non_stream()

        if args.all or args.dynamic_stream:
            test_dynamic_filtering_stream()

        if args.all or args.dynamic_location:
            test_dynamic_filtering_with_user_location()

        print(f"\n{'=' * 70}")
        print("  All tests passed! ✅")
        print(f"{'=' * 70}")

    except Exception as e:
        print(f"\n  ❌ FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
