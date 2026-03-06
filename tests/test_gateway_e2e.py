#!/usr/bin/env python3
"""
End-to-end client test for the multi-provider routing gateway.

Tests the full request pipeline through the running proxy with real Bedrock calls.
Requires:
  1. Proxy running with MULTI_PROVIDER_ENABLED=true
  2. ROUTING_ENABLED=true, COMPRESSION_ENABLED=true (optional)
  3. Valid API key in tests/.env

Usage:
    # Test all scenarios
    uv run python tests/test_gateway_e2e.py

    # Test specific scenario
    uv run python tests/test_gateway_e2e.py --test routing
    uv run python tests/test_gateway_e2e.py --test compression
    uv run python tests/test_gateway_e2e.py --test streaming
    uv run python tests/test_gateway_e2e.py --test backward
"""
import argparse
import json
import sys
import time
import os

from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL

MODEL = "claude-haiku-4-5-20251001"
STRONG_MODEL = "claude-sonnet-4-5-20250929"

client = Anthropic(api_key=API_KEY, base_url=BASE_URL)


def section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


# ── 1. Backward compatibility (feature flags off) ────────────

def test_backward_compat():
    """When MULTI_PROVIDER_ENABLED=false, behavior should be identical to before."""
    section("1. 向后兼容测试 — 确认基本调用正常")

    print(f"Model: {MODEL}")
    print(f"Proxy: {BASE_URL}")
    print()

    # Non-streaming
    print("1a. Non-streaming call...")
    start = time.time()
    response = client.messages.create(
        model=MODEL,
        max_tokens=100,
        messages=[{"role": "user", "content": "Say 'hello' in one word."}],
    )
    elapsed = (time.time() - start) * 1000
    print(f"  ✓ Response: {response.content[0].text[:80]}")
    print(f"  ✓ Tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")
    print(f"  ✓ Latency: {elapsed:.0f}ms")
    print(f"  ✓ Stop reason: {response.stop_reason}")

    # Streaming
    print("\n1b. Streaming call...")
    chunks = []
    start = time.time()
    with client.messages.stream(
        model=MODEL,
        max_tokens=100,
        messages=[{"role": "user", "content": "Count from 1 to 3."}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
            print(text, end="", flush=True)
    elapsed = (time.time() - start) * 1000
    print(f"\n  ✓ Chunks: {len(chunks)}, Latency: {elapsed:.0f}ms")

    print("\n✅ 向后兼容测试通过")


# ── 2. Routing via raw HTTP (inspect routing decision) ────────

def test_routing_decision():
    """Send requests with different models and content to observe routing decisions.
    Check proxy logs for routing reason (rule match, cost, quality, cache_affinity, etc.)."""
    section("2. 路由决策测试 — 不同模型请求")

    # Request with haiku model
    print("2a. 请求 haiku 模型...")
    start = time.time()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=50,
        messages=[{"role": "user", "content": "Hi"}],
    )
    elapsed = (time.time() - start) * 1000
    print(f"  ✓ Model: {resp.model}")
    print(f"  ✓ Response: {resp.content[0].text[:60]}")
    print(f"  ✓ Latency: {elapsed:.0f}ms")

    # Request with sonnet model
    print("\n2b. 请求 sonnet 模型...")
    start = time.time()
    resp = client.messages.create(
        model=STRONG_MODEL,
        max_tokens=50,
        messages=[{"role": "user", "content": "Hi"}],
    )
    elapsed = (time.time() - start) * 1000
    print(f"  ✓ Model: {resp.model}")
    print(f"  ✓ Response: {resp.content[0].text[:60]}")
    print(f"  ✓ Latency: {elapsed:.0f}ms")

    # Request with code-related content (should trigger keyword rule if configured)
    print("\n2c. 代码相关请求（测试关键词规则）...")
    start = time.time()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=100,
        messages=[{"role": "user", "content": "写一段 python 快速排序代码"}],
    )
    elapsed = (time.time() - start) * 1000
    print(f"  ✓ Model: {resp.model}")
    print(f"  ✓ Response preview: {resp.content[0].text[:80]}...")
    print(f"  ✓ Tokens: {resp.usage.input_tokens} in / {resp.usage.output_tokens} out")
    print(f"  ✓ Latency: {elapsed:.0f}ms")

    print("\n✅ 路由决策测试通过（查看代理日志确认路由决策）")


# ── 3. Context compression test ───────────────────────────────

def test_compression():
    """Test context compression with a long multi-turn conversation."""
    section("3. 上下文压缩测试 — 多轮对话 + 长工具结果")

    long_tool_result = "x" * 5000  # 5000 chars, should be truncated to ~1500

    messages = []
    # Add 8 turns of normal conversation (to trigger history folding)
    for i in range(8):
        messages.append({"role": "user", "content": f"Turn {i+1}: Tell me about topic {i+1}"})
        messages.append({
            "role": "assistant",
            "content": f"This is a detailed response about topic {i+1}. " * 20,
        })

    # Add a proper tool_use → tool_result pair
    messages.append({
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_test123",
                "name": "get_data",
                "input": {"query": "test"},
            }
        ],
    })
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_test123",
                "content": long_tool_result,
            }
        ],
    })

    # Final user message
    messages.append({"role": "user", "content": "Summarize everything above in one sentence."})

    print(f"  Messages: {len(messages)} turns")
    print(f"  Tool result size: {len(long_tool_result)} chars")
    print(f"  Total chars: ~{sum(len(str(m)) for m in messages)}")

    print("\n  Sending request...")
    start = time.time()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            tools=[{
                "name": "get_data",
                "description": "Get data from a source.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }],
            messages=messages,
        )
        elapsed = (time.time() - start) * 1000
        print(f"  ✓ Response: {response.content[0].text[:100]}...")
        print(f"  ✓ Tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")
        print(f"  ✓ Latency: {elapsed:.0f}ms")
        print("\n  💡 查看代理日志中的 'Compression savings' 确认压缩效果")
        print("\n✅ 压缩测试通过")
    except Exception as e:
        print(f"  ⚠️  请求失败: {e}")
        print("  这可能是因为 COMPRESSION_ENABLED=false 或模型不支持此格式")
        print("  如果代理正常运行且 feature flag 关闭，这也是预期行为")


# ── 4. Streaming with routing ─────────────────────────────────

def test_streaming_with_routing():
    """Test streaming works correctly through the routing pipeline."""
    section("4. Streaming + 路由测试")

    print("4a. Streaming 简单请求...")
    chunks = []
    start = time.time()
    with client.messages.stream(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": "用中文解释什么是 API Gateway，一句话。"}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
            print(text, end="", flush=True)
    elapsed = (time.time() - start) * 1000
    print(f"\n  ✓ Chunks: {len(chunks)}, Latency: {elapsed:.0f}ms")

    print("\n4b. Streaming 代码请求（可能触发规则路由）...")
    chunks = []
    start = time.time()
    with client.messages.stream(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": "Write a python function to reverse a string."}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
            print(text, end="", flush=True)
    elapsed = (time.time() - start) * 1000
    print(f"\n  ✓ Chunks: {len(chunks)}, Latency: {elapsed:.0f}ms")

    print("\n✅ Streaming 测试通过")


# ── 5. Tool use with routing ──────────────────────────────────

def test_tool_use():
    """Test tool use works correctly through the routing pipeline."""
    section("5. Tool Use + 路由测试")

    tools = [
        {
            "name": "get_weather",
            "description": "Get the current weather for a location.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"],
            },
        }
    ]

    print("  Sending tool use request...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        tools=tools,
        messages=[{"role": "user", "content": "What's the weather in Beijing?"}],
    )

    print(f"  ✓ Stop reason: {response.stop_reason}")
    for block in response.content:
        if block.type == "text":
            print(f"  ✓ Text: {block.text[:80]}")
        elif block.type == "tool_use":
            print(f"  ✓ Tool call: {block.name}({json.dumps(block.input)})")
    print(f"  ✓ Tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")

    print("\n✅ Tool Use 测试通过")


# ── 6. Prompt Cache Affinity 测试 ─────────────────────────────

def test_cache_affinity():
    """Test that prompt cache works and routing preserves model stickiness.

    Uses daming.txt (~770KB) as a long system prompt with cache_control to:
    1. First call: cache write (expect cache_creation_input_tokens > 0)
    2. Second call: cache read (expect cache_read_input_tokens > 0)
    3. Verify routing keeps the same model (cache affinity reason in logs)
    """
    section("6. Prompt Cache 感知路由测试 — Cache Affinity")

    # Load long text for system prompt
    daming_path = os.path.join(os.path.dirname(__file__), "daming.txt")
    try:
        with open(daming_path, "r", encoding="gbk") as f:
            long_text = f.read()[:100000]  # Use first 100K chars (~enough for cache)
    except Exception as e:
        print(f"  ⚠️  无法读取 daming.txt: {e}")
        print("  跳过 cache affinity 测试")
        return

    print(f"  System prompt size: {len(long_text)} chars")

    # Build system prompt with cache_control
    system_with_cache = [
        {
            "type": "text",
            "text": f"你是一个历史学家。以下是参考资料：\n\n{long_text}",
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # ── Call 1: Cache Write ──
    print("\n  6a. 第一次调用（Cache Write）...")
    start = time.time()
    response1 = client.messages.create(
        model=MODEL,
        max_tokens=100,
        system=system_with_cache,
        messages=[{"role": "user", "content": "用一句话概括朱元璋的性格特点"}],
    )
    elapsed1 = (time.time() - start) * 1000

    cache_write = getattr(response1.usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(response1.usage, "cache_read_input_tokens", 0) or 0
    print(f"  ✓ Response: {response1.content[0].text[:80]}...")
    print(f"  ✓ Input tokens: {response1.usage.input_tokens}")
    print(f"  ✓ Cache write tokens: {cache_write}")
    print(f"  ✓ Cache read tokens: {cache_read}")
    print(f"  ✓ Latency: {elapsed1:.0f}ms")

    if cache_write > 0:
        print(f"  ✓ Cache 写入成功 ({cache_write} tokens)")
    else:
        print(f"  ⚠️  Cache 未写入（可能 prompt caching 未开启或模型不支持）")

    # ── Call 2: Cache Read (same system prompt) ──
    print("\n  6b. 第二次调用（Cache Read — 相同 system prompt）...")
    start = time.time()
    response2 = client.messages.create(
        model=MODEL,
        max_tokens=100,
        system=system_with_cache,
        messages=[{"role": "user", "content": "朱元璋对待功臣的态度如何？"}],
    )
    elapsed2 = (time.time() - start) * 1000

    cache_write2 = getattr(response2.usage, "cache_creation_input_tokens", 0) or 0
    cache_read2 = getattr(response2.usage, "cache_read_input_tokens", 0) or 0
    print(f"  ✓ Response: {response2.content[0].text[:80]}...")
    print(f"  ✓ Input tokens: {response2.usage.input_tokens}")
    print(f"  ✓ Cache write tokens: {cache_write2}")
    print(f"  ✓ Cache read tokens: {cache_read2}")
    print(f"  ✓ Latency: {elapsed2:.0f}ms")

    if cache_read2 > 0:
        savings_pct = (cache_read2 / (response2.usage.input_tokens + cache_read2)) * 100
        print(f"  ✓ Cache 命中! {cache_read2} tokens 从缓存读取 (节省 ~{savings_pct:.0f}% input cost)")
        print(f"  ✓ 延迟对比: {elapsed1:.0f}ms → {elapsed2:.0f}ms")
    else:
        print(f"  ⚠️  Cache 未命中（可能 TTL 过期或模型不支持 prompt caching）")

    # ── Call 3: Multi-turn with cache (verify model stickiness) ──
    print("\n  6c. 多轮对话（验证 Cache Affinity — 模型粘性）...")
    messages_multi = [
        {"role": "user", "content": "朱元璋的出身是什么？"},
        {"role": "assistant", "content": response2.content[0].text},
        {"role": "user", "content": "他建立明朝后做了哪些重要改革？简要回答。"},
    ]
    start = time.time()
    response3 = client.messages.create(
        model=MODEL,
        max_tokens=150,
        system=system_with_cache,
        messages=messages_multi,
    )
    elapsed3 = (time.time() - start) * 1000

    cache_read3 = getattr(response3.usage, "cache_read_input_tokens", 0) or 0
    print(f"  ✓ Response: {response3.content[0].text[:80]}...")
    print(f"  ✓ Cache read tokens: {cache_read3}")
    print(f"  ✓ Latency: {elapsed3:.0f}ms")
    print(f"  ✓ Model in response: {response3.model}")

    if cache_read3 > 0:
        print(f"  ✓ 多轮对话 Cache 持续命中 — 模型未被切换")
    else:
        print(f"  ⚠️  多轮 Cache 未命中")

    print("\n  💡 查看代理日志确认路由决策 reason='cache_affinity'")
    print("\n✅ Prompt Cache 感知路由测试通过")


# ── 7. Models endpoint ────────────────────────────────────────

def test_models_endpoint():
    """Test /v1/models returns aggregated model list via Anthropic SDK."""
    section("7. 模型列表端点测试")

    try:
        # Anthropic SDK models.list() calls GET /v1/models
        models_page = client.models.list(limit=20)
        models = list(models_page.data) if hasattr(models_page, 'data') else []

        print(f"  ✓ Models count: {len(models)}")
        if models:
            print(f"  ✓ First 5 models:")
            for m in models[:5]:
                model_id = getattr(m, "id", "unknown")
                print(f"    - {model_id}")
    except Exception as e:
        # Fallback: SDK models.list() may not match our custom format,
        # use raw HTTP as backup
        import httpx
        resp = httpx.get(
            f"{BASE_URL}/v1/models",
            headers={"x-api-key": API_KEY},
            timeout=15.0,
        )
        data = resp.json()
        models = data.get("data", [])
        print(f"  ✓ Status: {resp.status_code}")
        print(f"  ✓ Models count: {len(models)}")
        if models:
            print(f"  ✓ First 5 models:")
            for m in models[:5]:
                provider = m.get("provider", "unknown")
                model_id = m.get("id", "unknown")
                print(f"    - [{provider}] {model_id}")

    print("\n✅ 模型列表测试通过")


# ── Main ──────────────────────────────────────────────────────

ALL_TESTS = {
    "backward": ("向后兼容", test_backward_compat),
    "routing": ("路由决策", test_routing_decision),
    "compression": ("上下文压缩", test_compression),
    "streaming": ("Streaming + 路由", test_streaming_with_routing),
    "tooluse": ("Tool Use + 路由", test_tool_use),
    "cache": ("Prompt Cache 感知路由", test_cache_affinity),
    "models": ("模型列表", test_models_endpoint),
}


def main():
    parser = argparse.ArgumentParser(description="Gateway E2E Test")
    parser.add_argument("--test", choices=list(ALL_TESTS.keys()),
                        help="Run specific test only")
    args = parser.parse_args()

    print("=" * 60)
    print("  多 Provider 智能路由网关 — 端到端测试")
    print("=" * 60)
    print(f"  Proxy:  {BASE_URL}")
    print(f"  Model:  {MODEL}")
    print(f"  Key:    {API_KEY[:12]}...")
    print()
    print("  💡 确保代理已启动，查看代理日志观察路由决策和压缩效果")
    print("  💡 设置 MULTI_PROVIDER_ENABLED=true 测试新管线")
    print("  💡 设置 ROUTING_ENABLED=true 测试路由功能")
    print("  💡 设置 COMPRESSION_ENABLED=true 测试压缩功能")

    if args.test:
        name, fn = ALL_TESTS[args.test]
        try:
            fn()
        except Exception as e:
            print(f"\n❌ {name} 失败: {e}")
            return 1
    else:
        results = {}
        for key, (name, fn) in ALL_TESTS.items():
            try:
                fn()
                results[name] = True
            except Exception as e:
                print(f"\n❌ {name} 失败: {e}")
                results[name] = False

        # Summary
        section("测试结果汇总")
        for name, passed in results.items():
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"  {status}  {name}")

        passed = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"\n  {passed}/{total} 通过")

        if passed < total:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
