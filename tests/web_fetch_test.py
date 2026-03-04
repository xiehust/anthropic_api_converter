"""
Simple manual test for web fetch tool (web_fetch_20250910 / web_fetch_20260209).

Usage:
    cd tests
    python web_fetch_simple_test.py                          # default: web_fetch_20250910
    python web_fetch_simple_test.py --version 20260209       # dynamic filtering
    python web_fetch_simple_test.py --stream                 # streaming mode
    python web_fetch_simple_test.py --url https://example.com
"""
import argparse
import json
import sys
import warnings

# Suppress Pydantic serialization warnings from the Anthropic SDK
# (SDK doesn't recognize web_fetch_tool_result blocks — this is expected)
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")

from anthropic import Anthropic

from config import API_KEY, BASE_URL, MODEL_ID,ANTHROPIC_API_KEY


def run_web_fetch(version: str, url: str, stream: bool, citations: bool,official:bool):
    if official:
        print("=======use official anthropic==========")
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
    else:
        print("=======use api proxy==========")
        client = Anthropic(api_key=API_KEY, base_url=BASE_URL)

    tool_type = f"web_fetch_{version}"
    beta_header = f"web-fetch-{'2025-09-10' if version == '20250910' else '2026-02-09'}"

    tool_def = {
        "type": tool_type,
        "name": "web_fetch",
        "max_uses": 3,
        "max_content_tokens": 10000
    }
    if citations:
        tool_def["citations"] = {"enabled": True}

    print(f"=== Web Fetch Test ===")
    print(f"  Tool type:  {tool_type}")
    print(f"  Beta:       {beta_header}")
    print(f"  URL:        {url}")
    print(f"  Stream:     {stream}")
    print(f"  Citations:  {citations}")
    print(f"  Model:      {MODEL_ID}")
    print(f"  Base URL:   {BASE_URL}")
    print()

    messages = [
        {
            "role": "user",
            "content": f"Please fetch the content at {url} and count how many times 'hammer' appears",
        }
    ]

    if stream:
        print("--- Streaming Response ---")
        with client.messages.stream(
            model=MODEL_ID,
            max_tokens=4096,
            messages=messages,
            tools=[tool_def],
            extra_headers={"anthropic-beta": beta_header},
        ) as stream_resp:
            for event in stream_resp:
                event_dict = event.model_dump() if hasattr(event, "model_dump") else {}
                event_type = event_dict.get("type", type(event).__name__)

                if event_type == "content_block_start":
                    block = event_dict.get("content_block", {})
                    block_type = block.get("type", "")
                    if block_type == "server_tool_use":
                        print(f"\n[server_tool_use] name={block.get('name')} id={block.get('id')}")
                    elif block_type == "web_fetch_tool_result":
                        content = block.get("content", {})
                        content_type = content.get("type", "")
                        if content_type == "web_fetch_result":
                            doc = content.get("content", {})
                            source = doc.get("source", {})
                            data_len = len(source.get("data", ""))
                            print(f"\n[web_fetch_tool_result] url={content.get('url')} title={doc.get('title')!r} content_len={data_len}")
                        elif content_type == "web_fetch_tool_error":
                            print(f"\n[web_fetch_tool_error] code={content.get('error_code')}")
                    elif block_type == "text":
                        pass  # text deltas printed below

                elif event_type == "content_block_delta":
                    delta = event_dict.get("delta", {})
                    if delta.get("type") == "text_delta":
                        print(delta.get("text", ""), end="", flush=True)
                    elif delta.get("type") == "input_json_delta":
                        print(f"  input: {delta.get('partial_json', '')}", flush=True)

            print()
            final = stream_resp.get_final_message()
            print(f"\n--- Usage ---")
            print(f"  Input tokens:  {final.usage.input_tokens}")
            print(f"  Output tokens: {final.usage.output_tokens}")

    else:
        print("--- Non-Streaming Response ---")
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=4096,
            messages=messages,
            tools=[tool_def],
            extra_headers={"anthropic-beta": beta_header},
        )

        # Print content blocks
        for i, block in enumerate(response.content):
            block_dict = block.model_dump()
            block_type = block_dict.get("type", "")

            if block_type == "text":
                text = block_dict.get("text", "")
                cites = block_dict.get("citations", [])
                print(f"\n[text] {text[:500]}{'...' if len(text) > 500 else ''}")
                if cites:
                    print(f"  citations: {json.dumps(cites, indent=2)}")

            elif block_type == "server_tool_use":
                print(f"\n[server_tool_use] name={block_dict.get('name')} id={block_dict.get('id')}")
                print(f"  input: {json.dumps(block_dict.get('input', {}))}")

            elif block_type == "web_fetch_tool_result":
                content = block_dict.get("content", {})
                content_type = content.get("type", "")
                if content_type == "web_fetch_result":
                    doc = content.get("content", {})
                    source = doc.get("source", {})
                    data = source.get("data", "")
                    print(f"\n[web_fetch_tool_result]")
                    print(f"  url:          {content.get('url')}")
                    print(f"  title:        {doc.get('title')}")
                    print(f"  media_type:   {source.get('media_type')}")
                    print(f"  content_len:  {len(data)} chars")
                    print(f"  retrieved_at: {content.get('retrieved_at')}")
                    print(f"  preview:      {data[:200]}...")
                elif content_type == "web_fetch_tool_error":
                    print(f"\n[web_fetch_tool_error] code={content.get('error_code')}")

            else:
                print(f"\n[{block_type}] {json.dumps(block_dict)[:300]}")

        print(f"\n--- Usage ---")
        print(f"  Input tokens:  {response.usage.input_tokens}")
        print(f"  Output tokens: {response.usage.output_tokens}")
        print(f"  Stop reason:   {response.stop_reason}")

        # Also dump full JSON for debugging
        print(f"\n--- Full JSON ---")
        print(response.to_json())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual web fetch test")
    parser.add_argument("--version", choices=["20250910", "20260209"], default="20260209",
                        help="Web fetch tool version (default: 20260209)")
    parser.add_argument("--url", default="https://httpbin.org/html",
                        help="URL to fetch (default: https://httpbin.org/html)")
    parser.add_argument("--stream", action="store_true",
                        help="Use streaming mode")
    parser.add_argument("--official", action="store_true",
                        help="Use official")
    parser.add_argument("--citations", action="store_true",
                        help="Enable citations")
    args = parser.parse_args()

    run_web_fetch(args.version, args.url, args.stream, args.citations,args.official)
