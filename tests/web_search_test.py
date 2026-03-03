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
from config import API_KEY, BASE_URL, MODEL_ID,ANTHROPIC_API_KEY

client=None

# ==================== Pretty-print helpers ====================

def _block_to_dict(block):
    """Convert SDK object or dict to a plain dict for printing."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return {"_raw": str(block)}


def _truncate(s: str, max_len: int = 200) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...({len(s)} chars)"


def print_separator(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def print_response_json(response):
    """Print the full response as formatted JSON (the raw API shape)."""
    if hasattr(response, "model_dump"):
        d = response.model_dump(exclude_none=True)
    else:
        d = dict(response)
    print(json.dumps(d, indent=2, ensure_ascii=False, default=str))


def print_content_block(block, index: int):
    """Print a single content block with detailed fields."""
    d = _block_to_dict(block)
    btype = d.get("type", "?")

    if btype == "text":
        text = d.get("text", "")
        citations = d.get("citations")
        print(f"    [{index}] type=text")
        print(f"        text: {_truncate(text, 300)}")
        if citations:
            print(f"        citations ({len(citations)}):")
            for ci, c in enumerate(citations):
                print(f"          [{ci}] type={c.get('type')}")
                print(f"              url: {c.get('url', '')}")
                print(f"              title: {c.get('title', '')}")
                cited = c.get("cited_text", "")
                print(f"              cited_text: {_truncate(cited, 100)}")
                enc_idx = c.get("encrypted_index", "")
                print(f"              encrypted_index: {_truncate(enc_idx, 40)}")

    elif btype == "server_tool_use":
        print(f"    [{index}] type=server_tool_use")
        print(f"        id: {d.get('id', '')}")
        print(f"        name: {d.get('name', '')}")
        print(f"        input: {json.dumps(d.get('input', {}), ensure_ascii=False)}")

    elif btype == "web_search_tool_result":
        content = d.get("content", [])
        print(f"    [{index}] type=web_search_tool_result")
        print(f"        tool_use_id: {d.get('tool_use_id', '')}")
        if isinstance(content, list):
            print(f"        results: {len(content)}")
            for ri, r in enumerate(content):
                rtype = r.get("type", "?") if isinstance(r, dict) else getattr(r, "type", "?")
                if rtype == "web_search_result":
                    rd = r if isinstance(r, dict) else _block_to_dict(r)
                    print(f"          [{ri}] url: {rd.get('url', '')}")
                    print(f"               title: {rd.get('title', '')}")
                    enc = rd.get("encrypted_content", "")
                    print(f"               encrypted_content: {_truncate(enc, 60)}")
                    if rd.get("page_age"):
                        print(f"               page_age: {rd['page_age']}")
                else:
                    print(f"          [{ri}] {r}")
        elif isinstance(content, dict):
            # Error
            print(f"        ERROR: {content.get('error_code', content)}")
        else:
            print(f"        content: {content}")

    elif btype == "bash_code_execution_tool_result":
        print(f"    [{index}] type=bash_code_execution_tool_result")
        print(f"        tool_use_id: {d.get('tool_use_id', '')}")
        c = d.get("content", {})
        if isinstance(c, dict):
            rc = c.get("return_code", "?")
            print(f"        return_code: {rc}")
            stdout = c.get("stdout", "")
            if stdout:
                print(f"        stdout: {_truncate(stdout, 400)}")
            stderr = c.get("stderr", "")
            if stderr:
                print(f"        stderr: {_truncate(stderr, 200)}")

    elif btype == "tool_use":
        print(f"    [{index}] type=tool_use")
        print(f"        id: {d.get('id', '')}")
        print(f"        name: {d.get('name', '')}")
        print(f"        input: {json.dumps(d.get('input', {}), ensure_ascii=False)}")

    elif btype == "thinking":
        thinking = d.get("thinking", "")
        print(f"    [{index}] type=thinking")
        print(f"        thinking: {_truncate(thinking, 200)}")

    else:
        print(f"    [{index}] type={btype}")
        print(f"        {json.dumps(d, ensure_ascii=False, default=str)}")


def print_response(response):
    """Print a full message response with structured detail."""
    print(f"  --- Response ---")
    print(f"  id: {response.id}")
    print(f"  model: {response.model}")
    print(f"  stop_reason: {response.stop_reason}")

    # Usage
    u = response.usage
    print(f"  usage:")
    print(f"    input_tokens: {u.input_tokens}")
    print(f"    output_tokens: {u.output_tokens}")
    if getattr(u, "cache_creation_input_tokens", None):
        print(f"    cache_creation_input_tokens: {u.cache_creation_input_tokens}")
    if getattr(u, "cache_read_input_tokens", None):
        print(f"    cache_read_input_tokens: {u.cache_read_input_tokens}")
    server_tool_use = getattr(u, "server_tool_use", None)
    if server_tool_use:
        print(f"    server_tool_use: {server_tool_use}")

    # Content blocks
    print(f"  content: ({len(response.content)} blocks)")
    for i, block in enumerate(response.content):
        print_content_block(block, i)
    print()


def verify_response(response, label: str):
    """Verify and print structural checks on the response."""
    content = response.content
    checks = []

    # 1. Has server_tool_use?
    server_tool_uses = [
        b for b in content
        if (getattr(b, "type", None) or _block_to_dict(b).get("type")) == "server_tool_use"
    ]
    checks.append(("server_tool_use blocks", len(server_tool_uses)))

    # 2. Has web_search_tool_result?
    ws_results = [
        b for b in content
        if (getattr(b, "type", None) or _block_to_dict(b).get("type")) == "web_search_tool_result"
    ]
    checks.append(("web_search_tool_result blocks", len(ws_results)))

    # 3. Has text?
    text_blocks = [
        b for b in content
        if (getattr(b, "type", None) or _block_to_dict(b).get("type")) == "text"
    ]
    checks.append(("text blocks", len(text_blocks)))

    # 4. Has citations on any text block?
    cited_blocks = []
    for b in text_blocks:
        d = _block_to_dict(b)
        if d.get("citations"):
            cited_blocks.append(d)
    checks.append(("text blocks with citations", len(cited_blocks)))

    # 5. server_tool_use IDs start with srvtoolu_?
    bad_ids = []
    for b in server_tool_uses:
        bid = getattr(b, "id", None) or _block_to_dict(b).get("id", "")
        if not bid.startswith("srvtoolu_"):
            bad_ids.append(bid)
    checks.append(("server_tool_use IDs with srvtoolu_ prefix", len(server_tool_uses) - len(bad_ids)))

    # 6. tool_use_id in web_search_tool_result matches a server_tool_use id?
    stu_ids = {
        getattr(b, "id", None) or _block_to_dict(b).get("id", "")
        for b in server_tool_uses
    }
    matched = 0
    for b in ws_results:
        d = _block_to_dict(b)
        if d.get("tool_use_id") in stu_ids:
            matched += 1
    checks.append(("web_search_tool_result.tool_use_id matches server_tool_use.id", f"{matched}/{len(ws_results)}"))

    # 7. server_tool_use in usage?
    stu_usage = getattr(response.usage, "server_tool_use", None)
    checks.append(("usage.server_tool_use present", bool(stu_usage)))

    # Print
    print(f"  --- Verification: {label} ---")
    all_ok = True
    for name, val in checks:
        ok = bool(val) and val != "0/0"
        status = "OK" if ok else "WARN"
        if not ok:
            all_ok = False
        print(f"    [{status}] {name}: {val}")

    # Must have text
    assert len(text_blocks) > 0, f"[{label}] Expected at least one text block"
    print(f"  --- {'PASSED' if all_ok else 'PASSED (with warnings)'} ---\n")


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
    verify_response(response, "basic_non_stream")

    # Also dump full JSON for inspection
    print("  --- Full JSON Response ---")
    print_response_json(response)


def test_basic_stream():
    """Basic web search - streaming."""
    print_separator("Test: Basic Web Search (streaming)")

    all_events = []

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
            # Capture raw event
            event_dict = {}
            if hasattr(event, "type"):
                event_dict["type"] = event.type

            if event.type == "message_start":
                msg = event.message
                event_dict["message"] = {
                    "id": msg.id, "model": msg.model, "stop_reason": msg.stop_reason,
                    "usage": {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
                    if msg.usage else None,
                }
                print(f"  [message_start] id={msg.id}, model={msg.model}")

            elif event.type == "content_block_start":
                cb = event.content_block
                cbd = _block_to_dict(cb)
                event_dict["index"] = event.index
                event_dict["content_block"] = cbd
                btype = cbd.get("type", "?")
                if btype == "server_tool_use":
                    print(f"  [content_block_start:{event.index}] server_tool_use id={cbd.get('id')} name={cbd.get('name')}")
                elif btype == "web_search_tool_result":
                    content = cbd.get("content", [])
                    n = len(content) if isinstance(content, list) else 0
                    print(f"  [content_block_start:{event.index}] web_search_tool_result ({n} results)")
                    if isinstance(content, list):
                        for ri, r in enumerate(content):
                            rd = r if isinstance(r, dict) else _block_to_dict(r)
                            print(f"    [{ri}] {rd.get('title', '')} - {rd.get('url', '')}")
                elif btype == "text":
                    cits = cbd.get("citations")
                    cit_str = f" (citations: {len(cits)})" if cits else ""
                    print(f"  [content_block_start:{event.index}] text{cit_str}")
                else:
                    print(f"  [content_block_start:{event.index}] {btype}")

            elif event.type == "content_block_delta":
                delta = event.delta
                dd = _block_to_dict(delta)
                dtype = dd.get("type", "")
                if dtype == "text_delta":
                    text = dd.get("text", "")
                    print(text, end="", flush=True)
                elif dtype == "input_json_delta":
                    print(f"  [delta:{event.index}] input_json: {dd.get('partial_json', '')[:100]}")

            elif event.type == "content_block_stop":
                print(f"\n  [content_block_stop:{event.index}]")

            elif event.type == "message_delta":
                dd = _block_to_dict(event.delta)
                usage = _block_to_dict(event.usage) if event.usage else {}
                print(f"  [message_delta] stop_reason={dd.get('stop_reason')}, usage={usage}")

            elif event.type == "message_stop":
                print("  [message_stop]")

            all_events.append(event_dict)

        final = stream.get_final_message()

    print(f"\n  --- Final Message ---")
    print_response(final)
    verify_response(final, "basic_stream")


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
    verify_response(response, "domain_filtering")

    # Check result URLs
    for block in response.content:
        d = _block_to_dict(block)
        if d.get("type") == "web_search_tool_result" and isinstance(d.get("content"), list):
            for r in d["content"]:
                rd = r if isinstance(r, dict) else _block_to_dict(r)
                url = rd.get("url", "")
                in_domain = "python.org" in url
                print(f"    Domain check: {url} -> {'OK' if in_domain else 'OUT OF DOMAIN'}")


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

    # Count successful vs error results
    success_count = 0
    error_count = 0
    for block in response.content:
        d = _block_to_dict(block)
        if d.get("type") == "web_search_tool_result":
            content = d.get("content")
            if isinstance(content, list):
                success_count += 1
            elif isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
                error_count += 1
                print(f"    max_uses error: {content.get('error_code')}")

    print(f"  Successful searches: {success_count}")
    print(f"  max_uses_exceeded errors: {error_count}")
    verify_response(response, "max_uses")


def test_multi_turn():
    """Multi-turn conversation with web search results passed back."""
    print_separator("Test: Multi-Turn Conversation")

    # First turn
    print("  --- Turn 1: Initial search ---")
    response1 = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[
            {"role": "user", "content": "Search for the latest FastAPI release version."}
        ],
    )
    print_response(response1)

    # Build multi-turn messages (pass back all content from turn 1)
    content_for_assistant = []
    for block in response1.content:
        if hasattr(block, "model_dump"):
            content_for_assistant.append(block.model_dump(exclude_none=True))
        elif isinstance(block, dict):
            content_for_assistant.append(block)

    # Second turn
    print("  --- Turn 2: Follow-up ---")
    response2 = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[
            {"role": "user", "content": "Search for the latest FastAPI release version."},
            {"role": "assistant", "content": content_for_assistant},
            {"role": "user", "content": "Based on those search results, what are the key highlights of that release? You can search again if needed."},
        ],
    )
    print_response(response2)
    verify_response(response2, "multi_turn")


def test_dynamic_filtering_non_stream():
    """Dynamic filtering (web_search_20260209) - non-streaming."""
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
                    "Search for the current prices of AAPL and GOOGL, then calculate which has a better P/E ratio."
                    # "Search the web for the top 3 most popular Python web frameworks in 2025. "
                    # "For each framework, find its latest version number and a one-sentence description. "
                    # "Present the results as a numbered list."
                    # "search when Claude Shannon was born?"
                ),
            }
        ],
    )

    print_response(response)
    verify_response(response, "dynamic_non_stream")

    # Check for bash execution results (dynamic filtering evidence)
    bash_results = [
        b for b in response.content
        if (getattr(b, "type", None) or _block_to_dict(b).get("type")) == "bash_code_execution_tool_result"
    ]
    if bash_results:
        print(f"  Dynamic filtering: {len(bash_results)} bash execution(s) found")
    else:
        print(f"  Dynamic filtering: No bash executions (Claude may not have used code filtering)")

    # Dump full JSON
    print("\n  --- Full JSON Response ---")
    print_response_json(response)


def test_dynamic_filtering_stream():
    """Dynamic filtering (web_search_20260209) - streaming."""
    print_separator("Test: Dynamic Filtering / web_search_20260209 (streaming)")

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
                "content": "Search for the current prices of AAPL and GOOGL, then calculate which has a better P/E ratio.",
            }
        ],
    ) as stream:
        for event in stream:
            if event.type == "message_start":
                msg = event.message
                print(f"  [message_start] id={msg.id}, model={msg.model}")

            elif event.type == "content_block_start":
                cb = event.content_block
                cbd = _block_to_dict(cb)
                btype = cbd.get("type", "?")
                if btype == "server_tool_use":
                    print(f"  [content_block_start:{event.index}] server_tool_use name={cbd.get('name')}")
                elif btype == "web_search_tool_result":
                    content = cbd.get("content", [])
                    n = len(content) if isinstance(content, list) else 0
                    print(f"  [content_block_start:{event.index}] web_search_tool_result ({n} results)")
                elif btype == "bash_code_execution_tool_result":
                    print(f"  [content_block_start:{event.index}] bash_code_execution_tool_result")
                elif btype == "text":
                    cits = cbd.get("citations")
                    cit_str = f" (citations: {len(cits)})" if cits else ""
                    print(f"  [content_block_start:{event.index}] text{cit_str}")
                else:
                    print(f"  [content_block_start:{event.index}] {btype}")

            elif event.type == "content_block_delta":
                dd = _block_to_dict(event.delta)
                dtype = dd.get("type", "")
                if dtype == "text_delta":
                    print(dd.get("text", ""), end="", flush=True)
                elif dtype == "input_json_delta":
                    pj = dd.get("partial_json", "")
                    print(f"  [delta:{event.index}] input_json: {_truncate(pj, 100)}")

            elif event.type == "content_block_stop":
                print(f"\n  [content_block_stop:{event.index}]")

            elif event.type == "message_delta":
                dd = _block_to_dict(event.delta)
                usage = _block_to_dict(event.usage) if event.usage else {}
                print(f"  [message_delta] stop_reason={dd.get('stop_reason')}, usage={usage}")

            elif event.type == "message_stop":
                print("  [message_stop]")

        final = stream.get_final_message()

    print(f"\n  --- Final Message ---")
    print_response(final)
    verify_response(final, "dynamic_stream")


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
    verify_response(response, "dynamic_location")


def test_multi_turn_dynamic():
    """Multi-turn conversation with dynamic filtering (web_search_20260209) results passed back."""
    print_separator("Test: Multi-Turn Dynamic Filtering (web_search_20260209)")

    tool_def = {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 3,
    }

    # Turn 1: initial search
    print("  --- Turn 1: Initial search ---")
    response1 = client.messages.create(
        model=MODEL_ID,
        max_tokens=8192,
        tools=[tool_def],
        messages=[
            {"role": "user", "content": "Search for Claude Shannon's birth date."}
        ],
    )
    print_response(response1)
    verify_response(response1, "multi_turn_dynamic_t1")

    # Build assistant content from turn 1
    content_for_assistant = []
    for block in response1.content:
        if hasattr(block, "model_dump"):
            content_for_assistant.append(block.model_dump(exclude_none=True))
        elif isinstance(block, dict):
            content_for_assistant.append(block)

    print(f"  Assistant content types: {[b.get('type', '?') for b in content_for_assistant]}")

    # Turn 2: follow-up referencing turn 1
    print("  --- Turn 2: Follow-up ---")
    response2 = client.messages.create(
        model=MODEL_ID,
        max_tokens=8192,
        tools=[tool_def],
        messages=[
            {"role": "user", "content": "Search for Claude Shannon's birth date."},
            {"role": "assistant", "content": content_for_assistant},
            {
                "role": "user",
                "content": "Now search for what Claude Shannon is most famous for. Summarize briefly.",
            },
        ],
    )
    print_response(response2)
    verify_response(response2, "multi_turn_dynamic_t2")

    # Dump full JSON for both turns
    print("  --- Full JSON Response (Turn 2) ---")
    print_response_json(response2)


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
    verify_response(response, "with_other_tools")


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
    parser.add_argument("--multi-turn-dynamic", action="store_true", help="Run multi-turn dynamic filtering test")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--official", action="store_false", help="Run all tests")
    args = parser.parse_args()


    if args.official:
        print("===========use proxy api===========")
        client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
    else:
        print("===========use anthropic official api===========")
        client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # If no specific flag, run basic non-stream + stream
    any_specific = (
        args.stream or args.no_stream or args.domains or args.max_uses
        or args.multi_turn or args.with_tools or args.dynamic
        or args.dynamic_stream or args.dynamic_location
        or args.multi_turn_dynamic or args.all
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

        if args.all or args.multi_turn_dynamic:
            test_multi_turn_dynamic()

        print(f"\n{'=' * 70}")
        print("  All tests passed!")
        print(f"{'=' * 70}")

    except Exception as e:
        print(f"\n  FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
