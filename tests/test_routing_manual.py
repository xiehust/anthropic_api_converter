#!/usr/bin/env python3
"""
Manual test for the intelligent routing engine.

Tests all routing strategies without needing a running proxy or real LLM calls.
Uses mock objects to simulate providers, pricing, and smart routing.

Usage:
    uv run python tests/test_routing_manual.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routing.rules import RuleEngine, RoutingRule
from app.routing.engine import RoutingEngine, RoutingDecision
from app.routing.smart import SmartRouter
from app.services.provider_registry import ProviderRegistry
from app.services.provider_base import LLMProvider, ProviderResponse
from app.core.exceptions import NoProviderAvailableError


# ── Mock objects ──────────────────────────────────────────────

class MockProvider(LLMProvider):
    """Minimal mock provider for testing."""
    def __init__(self, name: str, models: list[str]):
        self._name = name
        self._models = models

    @property
    def name(self): return self._name
    async def invoke(self, *a, **kw): pass
    async def invoke_stream(self, *a, **kw): yield ""
    def supports_model(self, m): return m in self._models
    def get_cost(self, m, i, o): return 0.0
    def list_models(self): return [{"id": m} for m in self._models]


class MockPricingManager:
    """Mock pricing manager returning predefined pricing data."""
    def __init__(self, items: list[dict]):
        self._items = items

    def list_all_pricing(self):
        return {"items": self._items}

    def get_pricing(self, model_id):
        for item in self._items:
            if item["model_id"] == model_id:
                return item
        return None


class MockSmartRouter:
    """Mock smart router that returns predetermined complexity."""
    def __init__(self, strong_model: str, weak_model: str, responses: dict[str, str] = None):
        self.strong_model = strong_model
        self.weak_model = weak_model
        self._responses = responses or {}

    def classify(self, user_message: str) -> str:
        # Check if we have a predetermined response for this message
        for keyword, complexity in self._responses.items():
            if keyword.lower() in user_message.lower():
                return complexity
        return "low"  # default


# ── Test helpers ──────────────────────────────────────────────

PASS = 0
FAIL = 0

def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ── Build shared fixtures ─────────────────────────────────────

def build_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(MockProvider("bedrock", [
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-5-20251101",
    ]))
    return registry


def build_pricing() -> MockPricingManager:
    return MockPricingManager([
        {"model_id": "claude-haiku-4-5-20251001", "provider": "bedrock",
         "input_price": 1.0, "output_price": 5.0, "status": "active"},
        {"model_id": "claude-sonnet-4-5-20250929", "provider": "bedrock",
         "input_price": 3.0, "output_price": 15.0, "status": "active"},
        {"model_id": "claude-opus-4-5-20251101", "provider": "bedrock",
         "input_price": 5.0, "output_price": 25.0, "status": "active"},
        {"model_id": "deprecated-model", "provider": "bedrock",
         "input_price": 0.1, "output_price": 0.5, "status": "deprecated"},
    ])


def build_rules() -> RuleEngine:
    engine = RuleEngine()
    engine.load_rules([
        RoutingRule("r1", "code_to_sonnet", "keyword", "python,code,函数",
                    "claude-sonnet-4-5-20250929", priority=0),
        RoutingRule("r2", "translate_to_haiku", "keyword", "翻译,translate",
                    "claude-haiku-4-5-20251001", priority=1),
        RoutingRule("r3", "regex_json", "regex", r"\bjson\b",
                    "claude-sonnet-4-5-20250929", priority=2),
        RoutingRule("r4", "model_redirect", "model", "gpt-4o,gpt-4",
                    "claude-opus-4-5-20251101", priority=3),
        RoutingRule("r5", "disabled_rule", "keyword", "disabled",
                    "claude-haiku-4-5-20251001", priority=0, is_enabled=False),
    ])
    return engine


# ── Test 1: Strategy OFF ─────────────────────────────────────

def test_strategy_off():
    print("\n" + "=" * 60)
    print("1. 路由策略 OFF — 直接使用请求模型")
    print("=" * 60)

    engine = RoutingEngine(RuleEngine())
    api_key = {"routing_strategy": "off"}

    d = engine.route("claude-sonnet-4-5-20250929", "hello world", api_key)
    check("strategy=off 返回原始模型", d.model == "claude-sonnet-4-5-20250929")
    check("reason 为 routing_off", d.reason == "routing_off")
    check("provider 为 bedrock", d.provider == "bedrock")

    # None api_key_info should also default to off
    d2 = engine.route("claude-sonnet-4-5-20250929", "hello", None)
    check("api_key_info=None 也走 off", d2.reason == "routing_off")


# ── Test 2: Rule-based routing ────────────────────────────────

def test_rule_routing():
    print("\n" + "=" * 60)
    print("2. 规则路由 — 关键词/正则/模型名匹配")
    print("=" * 60)

    rules = build_rules()
    engine = RoutingEngine(rules, provider_registry=build_registry(), pricing_manager=build_pricing())
    api_key = {"routing_strategy": "cost"}  # strategy doesn't matter, rules take priority

    # Keyword match
    d = engine.route("any-model", "帮我写一段 python 代码", api_key)
    check("关键词 'python' 命中 code_to_sonnet", d.model == "claude-sonnet-4-5-20250929")
    check("reason 包含 rule:", "rule:" in d.reason)

    # Keyword match (Chinese)
    d = engine.route("any-model", "请帮我翻译这段话", api_key)
    check("关键词 '翻译' 命中 translate_to_haiku", d.model == "claude-haiku-4-5-20251001")

    # Regex match
    d = engine.route("any-model", "parse this json object", api_key)
    check("正则 \\bjson\\b 命中", d.model == "claude-sonnet-4-5-20250929")

    # Model name match
    d = engine.route("gpt-4o", "hello", api_key)
    check("模型名 gpt-4o 重定向到 opus", d.model == "claude-opus-4-5-20251101")

    # No match → falls through to strategy
    d = engine.route("claude-sonnet-4-5-20250929", "今天天气怎么样", api_key)
    check("无规则命中 → 走 cost 策略", "cost:" in d.reason)

    # Disabled rule should not match
    d = engine.route("any-model", "this is disabled keyword", api_key)
    check("禁用规则不匹配", "rule:disabled" not in d.reason)

    # Priority: keyword 'python' (priority=0) beats '翻译' (priority=1)
    d = engine.route("any-model", "用 python 翻译这段代码", api_key)
    check("优先级: python(0) > 翻译(1)", d.model == "claude-sonnet-4-5-20250929")


# ── Test 3: Cost routing ──────────────────────────────────────

def test_cost_routing():
    print("\n" + "=" * 60)
    print("3. 成本路由 — 选最便宜的可用模型")
    print("=" * 60)

    rules = RuleEngine()
    registry = build_registry()
    pricing = build_pricing()
    engine = RoutingEngine(rules, provider_registry=registry, pricing_manager=pricing)
    api_key = {"routing_strategy": "cost"}

    d = engine.route("claude-sonnet-4-5-20250929", "hello", api_key)
    check("成本路由选择 haiku (最便宜)", d.model == "claude-haiku-4-5-20251001")
    check("reason 包含 cost:", "cost:" in d.reason)

    # Deprecated models should be skipped
    check("跳过 deprecated 模型", d.model != "deprecated-model")


# ── Test 4: Quality routing ───────────────────────────────────

def test_quality_routing():
    print("\n" + "=" * 60)
    print("4. 质量路由 — 选最贵（最强）的可用模型")
    print("=" * 60)

    rules = RuleEngine()
    registry = build_registry()
    pricing = build_pricing()
    engine = RoutingEngine(rules, provider_registry=registry, pricing_manager=pricing)
    api_key = {"routing_strategy": "quality"}

    d = engine.route("claude-haiku-4-5-20251001", "hello", api_key)
    check("质量路由选择 opus (最贵)", d.model == "claude-opus-4-5-20251101")
    check("reason 包含 quality:", "quality:" in d.reason)


# ── Test 5: Smart routing (auto) ─────────────────────────────

def test_smart_routing():
    print("\n" + "=" * 60)
    print("5. 智能路由 (auto) — 按复杂度分类选模型")
    print("=" * 60)

    rules = RuleEngine()
    smart = MockSmartRouter(
        strong_model="claude-sonnet-4-5-20250929",
        weak_model="claude-haiku-4-5-20251001",
        responses={
            "implement": "high",
            "complex": "high",
            "hello": "low",
            "简单": "low",
        },
    )
    engine = RoutingEngine(rules, smart_router=smart, provider_registry=build_registry())
    api_key = {"routing_strategy": "auto"}

    # Complex query → strong model
    d = engine.route("any-model", "implement a distributed cache system", api_key)
    check("复杂 query → strong model (sonnet)", d.model == "claude-sonnet-4-5-20250929")
    check("reason 包含 smart:high", "smart:high" in d.reason)

    # Simple query → weak model
    d = engine.route("any-model", "hello, how are you?", api_key)
    check("简单 query → weak model (haiku)", d.model == "claude-haiku-4-5-20251001")
    check("reason 包含 smart:low", "smart:low" in d.reason)

    # Chinese simple query
    d = engine.route("any-model", "这是一个简单的问题", api_key)
    check("中文简单 query → weak model", d.model == "claude-haiku-4-5-20251001")


# ── Test 6: Budget degradation ────────────────────────────────

def test_budget_degradation():
    print("\n" + "=" * 60)
    print("6. 预算感知降级 — 超 80% 自动降级到弱模型")
    print("=" * 60)

    rules = RuleEngine()
    smart = MockSmartRouter(
        strong_model="claude-sonnet-4-5-20250929",
        weak_model="claude-haiku-4-5-20251001",
    )
    engine = RoutingEngine(rules, smart_router=smart, provider_registry=build_registry())

    # 80% budget used → degrade
    api_key_80 = {
        "routing_strategy": "auto",
        "monthly_budget": 100.0,
        "budget_used_mtd": 80.0,
    }
    d = engine.route("claude-sonnet-4-5-20250929", "complex question", api_key_80)
    check("预算 80% → 降级到 weak model", d.model == "claude-haiku-4-5-20251001")
    check("reason 为 budget_degradation", d.reason == "budget_degradation")

    # 90% budget used → also degrade
    api_key_90 = {
        "routing_strategy": "cost",
        "monthly_budget": 100.0,
        "budget_used_mtd": 90.0,
    }
    d = engine.route("claude-opus-4-5-20251101", "anything", api_key_90)
    check("预算 90% → 也降级", d.reason == "budget_degradation")

    # 50% budget used → no degradation
    api_key_50 = {
        "routing_strategy": "auto",
        "monthly_budget": 100.0,
        "budget_used_mtd": 50.0,
    }
    d = engine.route("claude-sonnet-4-5-20250929", "hello", api_key_50)
    check("预算 50% → 不降级", d.reason != "budget_degradation")

    # No budget set → no degradation
    api_key_no_budget = {
        "routing_strategy": "auto",
        "monthly_budget": 0,
        "budget_used_mtd": 999.0,
    }
    d = engine.route("claude-sonnet-4-5-20250929", "hello", api_key_no_budget)
    check("无预算限制 → 不降级", d.reason != "budget_degradation")

    # Strategy off → no budget check
    api_key_off = {
        "routing_strategy": "off",
        "monthly_budget": 100.0,
        "budget_used_mtd": 99.0,
    }
    d = engine.route("claude-opus-4-5-20251101", "anything", api_key_off)
    check("strategy=off → 不检查预算", d.reason == "routing_off")
    check("strategy=off → 保持原始模型", d.model == "claude-opus-4-5-20251101")


# ── Test 7: Rule priority over strategy ───────────────────────

def test_rule_priority_over_strategy():
    print("\n" + "=" * 60)
    print("7. 规则优先于策略 — 规则命中时忽略 cost/quality/auto")
    print("=" * 60)

    rules = build_rules()
    smart = MockSmartRouter("claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001")
    registry = build_registry()
    pricing = build_pricing()
    engine = RoutingEngine(rules, smart, registry, pricing)

    # Even with cost strategy, keyword rule should win
    api_key = {"routing_strategy": "cost"}
    d = engine.route("any-model", "写一段 python 代码", api_key)
    check("cost 策略下规则仍优先", "rule:" in d.reason)

    # Even with quality strategy
    api_key = {"routing_strategy": "quality"}
    d = engine.route("any-model", "翻译这段话", api_key)
    check("quality 策略下规则仍优先", "rule:" in d.reason)

    # Even with auto strategy
    api_key = {"routing_strategy": "auto"}
    d = engine.route("gpt-4o", "hello", api_key)
    check("auto 策略下模型名规则仍优先", d.model == "claude-opus-4-5-20251101")


# ── Test 8: Edge cases ────────────────────────────────────────

def test_edge_cases():
    print("\n" + "=" * 60)
    print("8. 边界情况")
    print("=" * 60)

    # No pricing manager → cost routing raises error
    rules = RuleEngine()
    engine = RoutingEngine(rules, provider_registry=build_registry())
    api_key = {"routing_strategy": "cost"}
    try:
        engine.route("any-model", "hello", api_key)
        check("无 pricing → cost 路由抛异常", False)
    except NoProviderAvailableError:
        check("无 pricing → cost 路由抛 NoProviderAvailableError", True)

    # No smart router → auto routing returns fallback
    engine2 = RoutingEngine(rules, smart_router=None, provider_registry=build_registry())
    api_key_auto = {"routing_strategy": "auto"}
    d = engine2.route("any-model", "hello", api_key_auto)
    check("无 SmartRouter → auto 返回 fallback", "smart:no_router" in d.reason)

    # Empty user message
    rules2 = build_rules()
    engine3 = RoutingEngine(rules2, provider_registry=build_registry(), pricing_manager=build_pricing())
    api_key_cost = {"routing_strategy": "cost"}
    d = engine3.route("any-model", "", api_key_cost)
    check("空消息 → 无规则命中，走策略", "cost:" in d.reason)

    # Case insensitive keyword matching
    d = engine3.route("any-model", "PYTHON CODE", api_key_cost)
    check("关键词大小写不敏感", "rule:" in d.reason)


# ── Main ──────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("智能路由引擎 — 手工测试")
    print("=" * 60)
    print("测试不需要运行代理服务或真实 LLM 调用")

    test_strategy_off()
    test_rule_routing()
    test_cost_routing()
    test_quality_routing()
    test_smart_routing()
    test_budget_degradation()
    test_rule_priority_over_strategy()
    test_edge_cases()

    # Summary
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    print(f"  通过: {PASS}")
    print(f"  失败: {FAIL}")
    print(f"  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL == 0:
        print("\n🎉 全部通过!")
        return 0
    else:
        print(f"\n❌ {FAIL} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
