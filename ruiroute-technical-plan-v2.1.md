# anthropic_api_proxy 升级技术方案 — 多 Provider 智能路由网关

> **版本：** v2.1（根据川哥决策更新）  
> **作者：** Amy（技术总监）  
> **日期：** 2026-03-04  
> **基础项目：** [github.com/xiehust/anthropic_api_proxy](https://github.com/xiehust/anthropic_api_proxy)

---

## 1. 架构演进

### 1.1 整体变化

```
现有架构：
  Client → [Auth → RateLimit → Convert → Bedrock → Convert] → Response

目标架构：
  Client → [Auth → RateLimit → Compress → Route → Provider(any) → Track] → Response
                                  ↑          ↑         ↑
                                新增       新增      扩展
```

### 1.2 核心原则

1. **扩展不重写** — 现有 30K 行代码不做大改，只加新模块
2. **向后兼容** — 纯 Bedrock 用户升级后零感知
3. **Feature Flag 控制** — 新功能默认关闭，按需开启
4. **DynamoDB 为主** — 不引入新的数据库依赖（Redis 后续再说）

---

## 2. 模块设计

### 2.1 Provider 抽象层

**新增文件：**
- `app/services/provider_base.py` — Provider 抽象接口
- `app/services/provider_registry.py` — Provider 注册中心
- `app/services/bedrock_provider.py` — 包装现有 BedrockService
- `app/services/openai_provider.py` — OpenAI Provider
- `app/services/anthropic_provider.py` — Anthropic Direct Provider
- `app/services/deepseek_provider.py` — DeepSeek Provider

**改动文件：**
- `app/api/messages.py` — 从直接调 bedrock_service 改为通过 router 调 provider

#### Provider 接口定义

```python
# app/services/provider_base.py
from abc import ABC, abstractmethod
from typing import AsyncGenerator

class LLMProvider(ABC):
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 标识：bedrock / openai / anthropic / deepseek"""
        ...
    
    @abstractmethod
    async def invoke(self, request: MessageRequest, api_key: str, **kwargs) -> MessageResponse:
        ...
    
    @abstractmethod
    async def invoke_stream(self, request: MessageRequest, api_key: str, **kwargs) -> AsyncGenerator[str, None]:
        ...
    
    @abstractmethod
    def supports_model(self, model_id: str) -> bool:
        ...
    
    @abstractmethod
    def get_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        ...
```

#### Bedrock Provider（包装层）

```python
# app/services/bedrock_provider.py
class BedrockProvider(LLMProvider):
    """零改动包装现有 BedrockService"""
    
    def __init__(self, bedrock_service: BedrockService):
        self._svc = bedrock_service
    
    @property
    def name(self) -> str:
        return "bedrock"
    
    async def invoke(self, request, api_key, **kwargs):
        return await self._svc.invoke_model(request, **kwargs)
    
    async def invoke_stream(self, request, api_key, **kwargs):
        async for event in self._svc.invoke_model_stream(request, **kwargs):
            yield event
    
    def supports_model(self, model_id: str) -> bool:
        return model_id in settings.default_model_mapping or "bedrock" in model_id
```

#### OpenAI Provider（格式转换核心）

```python
# app/services/openai_provider.py
class OpenAIProvider(LLMProvider):
    
    @property
    def name(self) -> str:
        return "openai"
    
    async def invoke(self, request: MessageRequest, api_key: str, **kwargs):
        openai_req = self._to_openai(request)
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=openai_req,
                timeout=120,
            )
            resp.raise_for_status()
        
        return self._from_openai(resp.json(), request.model)
    
    def _to_openai(self, request: MessageRequest) -> dict:
        """Anthropic → OpenAI 格式转换"""
        messages = []
        
        # system
        if request.system:
            sys_text = request.system if isinstance(request.system, str) else \
                       " ".join(b.text for b in request.system if hasattr(b, 'text'))
            messages.append({"role": "system", "content": sys_text})
        
        # messages
        for msg in request.messages:
            messages.append(self._convert_message(msg))
        
        return {
            "model": self._model_map.get(request.model, request.model),
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }
    
    def _convert_message(self, msg) -> dict:
        """单条消息转换"""
        if isinstance(msg.content, str):
            return {"role": msg.role, "content": msg.content}
        
        # 处理 content blocks
        parts = []
        tool_calls = []
        
        for block in msg.content:
            if block.type == "text":
                parts.append({"type": "text", "text": block.text})
            elif block.type == "image":
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.source.media_type};base64,{block.source.data}"}
                })
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": json.dumps(block.input)}
                })
            elif block.type == "tool_result":
                return {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": str(block.content)
                }
        
        result = {"role": msg.role}
        if parts:
            result["content"] = parts if len(parts) > 1 else parts[0].get("text", parts)
        if tool_calls:
            result["tool_calls"] = tool_calls
        
        return result
    
    def _from_openai(self, resp: dict, original_model: str) -> MessageResponse:
        """OpenAI → Anthropic 格式转换"""
        choice = resp["choices"][0]
        content = []
        
        # 文本内容
        if choice["message"].get("content"):
            content.append(TextContent(type="text", text=choice["message"]["content"]))
        
        # 工具调用
        for tc in choice["message"].get("tool_calls", []):
            content.append(ToolUseContent(
                type="tool_use",
                id=tc["id"],
                name=tc["function"]["name"],
                input=json.loads(tc["function"]["arguments"])
            ))
        
        return MessageResponse(
            id=resp["id"],
            type="message",
            role="assistant",
            model=original_model,
            content=content,
            stop_reason=self._map_stop_reason(choice.get("finish_reason")),
            usage=Usage(
                input_tokens=resp["usage"]["prompt_tokens"],
                output_tokens=resp["usage"]["completion_tokens"],
            )
        )
```

#### Anthropic Direct Provider

```python
# app/services/anthropic_provider.py
class AnthropicDirectProvider(LLMProvider):
    """直连 Anthropic API — 格式几乎一致，主要区别是 auth 和端点"""
    
    @property
    def name(self) -> str:
        return "anthropic"
    
    async def invoke(self, request: MessageRequest, api_key: str, **kwargs):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=request.model_dump(exclude_none=True),
                timeout=120,
            )
            resp.raise_for_status()
        
        return MessageResponse(**resp.json())
```

#### DeepSeek Provider

```python
# app/services/deepseek_provider.py
class DeepSeekProvider(LLMProvider):
    """DeepSeek — OpenAI 兼容接口，复用 OpenAI 转换逻辑"""
    
    def __init__(self):
        self._converter = OpenAIFormatConverter()  # 复用 OpenAI 的转换
        self._base_url = "https://api.deepseek.com/v1"
    
    @property
    def name(self) -> str:
        return "deepseek"
    
    async def invoke(self, request: MessageRequest, api_key: str, **kwargs):
        openai_req = self._converter.to_openai(request)
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=openai_req,
                timeout=120,
            )
            resp.raise_for_status()
        
        return self._converter.from_openai(resp.json(), request.model)
```

---

### 2.2 多 Key 池 + Failover

**新增文件：**
- `app/keypool/manager.py`
- `app/keypool/failover.py`
- `app/keypool/encryption.py`

**改动文件：**
- `app/db/dynamodb.py` — APIKeyManager 扩展 provider 相关查询
- `app/core/config.py` — 新增 failover 链配置

#### Key Pool 管理器

```python
# app/keypool/manager.py
class KeyPoolManager:
    
    def __init__(self, db: DynamoDBClient):
        self.db = db
        self._key_status: dict[str, KeyStatus] = {}  # 内存状态缓存
    
    async def get_provider_key(self, provider: str, model: str) -> Optional[str]:
        """获取可用的 Provider API Key"""
        keys = await self.db.api_key_manager.get_keys_by_provider(provider)
        
        available = [
            k for k in keys
            if k.is_active 
            and model in (k.provider_models or [])
            and self._is_available(k.api_key)
        ]
        
        if not available:
            return None
        
        # Round-Robin
        idx = self._rr_counter.get(provider, 0)
        key = available[idx % len(available)]
        self._rr_counter[provider] = idx + 1
        
        return self._decrypt(key.provider_api_key)
    
    def mark_rate_limited(self, api_key: str, retry_after: int = 60):
        """标记 Key 进入冷却"""
        self._key_status[api_key] = KeyStatus(
            cooldown_until=time.time() + retry_after
        )
    
    def _is_available(self, api_key: str) -> bool:
        status = self._key_status.get(api_key)
        if not status:
            return True
        return time.time() > status.cooldown_until
```

#### Failover 管理器

```python
# app/keypool/failover.py
class FailoverManager:
    
    def __init__(self, config: FailoverConfig, registry: ProviderRegistry, keypool: KeyPoolManager):
        self.config = config
        self.registry = registry
        self.keypool = keypool
    
    async def failover(self, original_provider: str, original_model: str) -> Optional[FailoverResult]:
        """
        Failover 链查找：
        配置示例：
        FAILOVER_CHAINS={
            "openai/gpt-4o": ["bedrock/claude-sonnet-4-5-20250929", "anthropic/claude-sonnet-4-5-20250929"],
            "bedrock/claude-sonnet-4-5-20250929": ["anthropic/claude-sonnet-4-5-20250929", "openai/gpt-4o"]
        }
        """
        chain_key = f"{original_provider}/{original_model}"
        chain = self.config.failover_chains.get(chain_key, [])
        
        for target in chain:
            provider_name, model = target.split("/", 1)
            provider = self.registry.get(provider_name)
            if not provider:
                continue
            
            key = await self.keypool.get_provider_key(provider_name, model)
            if key:
                logger.info(f"Failover: {chain_key} → {target}")
                return FailoverResult(provider=provider, model=model, api_key=key)
        
        return None
```

#### Key 加密

```python
# app/keypool/encryption.py
from cryptography.fernet import Fernet

class KeyEncryption:
    """应用层 AES 加密（Fernet = AES-128-CBC + HMAC-SHA256）"""
    
    def __init__(self, secret: str):
        # 从配置的 secret 派生 Fernet key
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)
    
    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()
    
    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
```

---

### 2.3 路由引擎

**新增文件：**
- `app/routing/engine.py`
- `app/routing/rules.py`
- `app/routing/smart.py`

#### 路由引擎

```python
# app/routing/engine.py
class RoutingEngine:
    
    def __init__(self, registry: ProviderRegistry, keypool: KeyPoolManager, config: RoutingConfig):
        self.registry = registry
        self.keypool = keypool
        self.config = config
        self._smart_router = None
    
    async def route(self, request: MessageRequest, api_key_info: dict) -> RoutingDecision:
        """主路由流程"""
        strategy = api_key_info.get("routing_strategy", "off")
        
        # 1. 规则路由（总是检查）
        rule_result = self._check_rules(request)
        if rule_result:
            return await self._resolve(rule_result, reason="rule_match")
        
        # 2. 策略路由
        if strategy == "off":
            return await self._route_default(request)
        elif strategy == "cost":
            return await self._route_by_cost(request)
        elif strategy == "quality":
            return await self._route_by_quality(request)
        elif strategy == "auto":
            return await self._route_smart(request, api_key_info)
        
        return await self._route_default(request)
    
    async def _route_default(self, request: MessageRequest) -> RoutingDecision:
        """默认路由：用请求中指定的模型"""
        providers = self.registry.find_by_model(request.model)
        for provider in providers:
            key = await self.keypool.get_provider_key(provider.name, request.model)
            if key:
                return RoutingDecision(
                    provider=provider, model=request.model,
                    api_key=key, reason="default"
                )
        raise NoProviderAvailableError(f"No provider available for model {request.model}")
    
    async def _route_by_cost(self, request: MessageRequest) -> RoutingDecision:
        """成本优先：选最便宜的可用模型"""
        candidates = []
        for provider in self.registry.all():
            for model in provider.available_models():
                key = await self.keypool.get_provider_key(provider.name, model)
                if key:
                    cost = provider.get_cost(model, 1000, 500)  # 标准化比较
                    candidates.append((cost, provider, model, key))
        
        if not candidates:
            raise NoProviderAvailableError()
        
        candidates.sort(key=lambda x: x[0])
        _, provider, model, key = candidates[0]
        return RoutingDecision(provider=provider, model=model, api_key=key, reason="cost_optimized")
    
    async def _route_smart(self, request: MessageRequest, api_key_info: dict) -> RoutingDecision:
        """RouteLLM 智能路由"""
        if not self._smart_router:
            self._smart_router = SmartRouter(self.config.smart_routing_config)
        
        # 判断复杂度
        user_msg = self._get_last_user_message(request)
        complexity = self._smart_router.classify(user_msg)
        
        # 预算检查 — 超 80% 强制降级
        budget_pct = api_key_info.get("budget_used_mtd", 0) / max(api_key_info.get("monthly_budget", float('inf')), 0.01)
        if budget_pct > 0.8:
            complexity = "low"  # 强制降级
        
        if complexity == "high":
            model = self.config.smart_routing_config.strong_model
        else:
            model = self.config.smart_routing_config.weak_model
        
        providers = self.registry.find_by_model(model)
        for provider in providers:
            key = await self.keypool.get_provider_key(provider.name, model)
            if key:
                return RoutingDecision(
                    provider=provider, model=model, api_key=key,
                    reason=f"smart_route:complexity={complexity}"
                )
        
        # 兜底到默认
        return await self._route_default(request)
```

#### 规则路由

```python
# app/routing/rules.py
class RuleEngine:
    """基于配置的规则路由"""
    
    def __init__(self, rules: list[RoutingRule]):
        self.rules = rules
    
    def match(self, request: MessageRequest) -> Optional[str]:
        """返回命中规则的目标模型，None 表示未命中"""
        user_msg = self._get_last_user_message(request)
        
        for rule in self.rules:
            if rule.type == "keyword":
                if any(kw in user_msg.lower() for kw in rule.keywords):
                    return rule.target_model
            elif rule.type == "regex":
                if re.search(rule.pattern, user_msg):
                    return rule.target_model
            elif rule.type == "model":
                if request.model in rule.source_models:
                    return rule.target_model
        
        return None
```

**配置示例：**

```python
# app/core/config.py 新增
routing_enabled: bool = Field(default=False, alias="ROUTING_ENABLED")
routing_rules: list = Field(default=[], alias="ROUTING_RULES")
# 示例：ROUTING_RULES=[{"type":"keyword","keywords":["code","python","函数"],"target_model":"claude-sonnet-4-5-20250929"}]

smart_routing_enabled: bool = Field(default=False, alias="SMART_ROUTING_ENABLED")
smart_routing_strong_model: str = Field(default="claude-sonnet-4-5-20250929", alias="SMART_ROUTING_STRONG_MODEL")
smart_routing_weak_model: str = Field(default="deepseek-chat", alias="SMART_ROUTING_WEAK_MODEL")
smart_routing_threshold: float = Field(default=0.5, alias="SMART_ROUTING_THRESHOLD")

failover_enabled: bool = Field(default=True, alias="FAILOVER_ENABLED")
failover_chains: dict = Field(default={}, alias="FAILOVER_CHAINS")
```

---

### 2.4 Agent 上下文压缩

**新增文件：**
- `app/compression/context_compressor.py`

```python
# app/compression/context_compressor.py
class ContextCompressor:
    
    def __init__(self, config: CompressionConfig):
        self.config = config
    
    async def compress(self, request: MessageRequest) -> tuple[MessageRequest, CompressionStats]:
        """返回压缩后的请求 + 压缩统计"""
        if self.config.strategy == "off":
            return request, CompressionStats.empty()
        
        messages = request.messages
        original_chars = self._count_chars(messages)
        compressed = []
        
        for i, msg in enumerate(messages):
            turns_from_end = len(messages) - i
            
            # 策略 1：工具结果截断
            if self._is_tool_result(msg):
                msg = self._truncate_tool_result(msg)
            
            # 策略 2：旧对话折叠
            if turns_from_end > self.config.fold_after_turns:
                if msg.role == "assistant" and self.config.strategy in ("aggressive", "moderate"):
                    msg = self._fold_message(msg)
            
            compressed.append(msg)
        
        compressed_chars = self._count_chars(compressed)
        stats = CompressionStats(
            original_chars=original_chars,
            compressed_chars=compressed_chars,
            savings_pct=round((1 - compressed_chars / max(original_chars, 1)) * 100, 1)
        )
        
        new_request = request.model_copy(update={"messages": compressed})
        return new_request, stats
    
    def _truncate_tool_result(self, msg) -> Message:
        """截断过长的工具结果"""
        content = self._get_content_text(msg)
        if len(content) <= self.config.tool_result_max_chars:
            return msg
        
        head = content[:self.config.truncate_head_chars]
        tail = content[-self.config.truncate_tail_chars:]
        truncated = f"{head}\n\n[... 已压缩，原始 {len(content)} 字符 ...]\n\n{tail}"
        
        return self._replace_content(msg, truncated)
    
    def _fold_message(self, msg) -> Message:
        """折叠旧消息为简短摘要标记"""
        content = self._get_content_text(msg)
        if len(content) <= 200:
            return msg
        
        # 简单截断（不调用 LLM 摘要，避免额外成本）
        summary = content[:150] + "..."
        return self._replace_content(msg, f"[历史消息摘要] {summary}")
```

**配置：**

```python
# app/core/config.py 新增
compression_enabled: bool = Field(default=False, alias="COMPRESSION_ENABLED")
compression_strategy: str = Field(default="moderate", alias="COMPRESSION_STRATEGY")  # aggressive/moderate/conservative/off
compression_tool_result_max_chars: int = Field(default=2000, alias="COMPRESSION_TOOL_RESULT_MAX_CHARS")
compression_fold_after_turns: int = Field(default=6, alias="COMPRESSION_FOLD_AFTER_TURNS")
compression_truncate_head_chars: int = Field(default=500, alias="COMPRESSION_TRUNCATE_HEAD_CHARS")
compression_truncate_tail_chars: int = Field(default=500, alias="COMPRESSION_TRUNCATE_TAIL_CHARS")
```

---

### 2.5 主流程改造

**改动文件：** `app/api/messages.py`

```python
# app/api/messages.py — 改造后核心流程
async def create_message(request: MessageRequest, ...):
    """
    原流程：Auth → RateLimit → Convert → Bedrock → Convert → Response
    新流程：Auth → RateLimit → Compress → Route → Provider → Failover → Track → Response
    """
    
    # ① 上下文压缩（新增，feature flag 控制）
    compression_stats = CompressionStats.empty()
    if settings.compression_enabled:
        request, compression_stats = await compressor.compress(request)
    
    # ② 路由决策（新增，feature flag 控制）
    if settings.routing_enabled:
        decision = await routing_engine.route(request, api_key_info)
    else:
        # 兼容模式：走原来的 Bedrock 路径
        decision = RoutingDecision(
            provider=bedrock_provider,
            model=request.model,
            api_key=None,  # Bedrock 用 IAM，不需要 key
            reason="legacy_bedrock"
        )
    
    # ③ 调用 Provider
    try:
        if request.stream:
            return StreamingResponse(
                decision.provider.invoke_stream(request, decision.api_key, model=decision.model),
                media_type="text/event-stream"
            )
        else:
            response = await decision.provider.invoke(request, decision.api_key, model=decision.model)
    except (RateLimitError, httpx.HTTPStatusError) as e:
        # ④ Failover（新增）
        if settings.failover_enabled and _is_rate_limit(e):
            keypool.mark_rate_limited(decision.api_key)
            failover = await failover_manager.failover(decision.provider.name, decision.model)
            if failover:
                metrics.failover_total.inc(labels={"from": decision.provider.name, "to": failover.provider.name})
                if request.stream:
                    return StreamingResponse(
                        failover.provider.invoke_stream(request, failover.api_key, model=failover.model),
                        media_type="text/event-stream"
                    )
                else:
                    response = await failover.provider.invoke(request, failover.api_key, model=failover.model)
            else:
                raise
    
    # ⑤ 用量追踪（复用现有逻辑，扩展字段）
    await usage_tracker.record(
        api_key=api_key_info["api_key"],
        model=decision.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        # 新增字段
        provider=decision.provider.name,
        routing_reason=decision.reason,
        original_model=request.model,
        compressed_chars_saved=compression_stats.original_chars - compression_stats.compressed_chars,
    )
    
    return response
```

---

## 3. 新增依赖

| 依赖 | 用途 | 必要性 |
|------|------|--------|
| `openai` | OpenAI Provider SDK | 可选（直接用 httpx 也行，但 SDK 处理 streaming 更方便） |
| `routellm` | 智能路由 | 可选（SMART_ROUTING_ENABLED=True 时才加载） |
| `cryptography` | Provider Key 加密 | 必须 |

**不引入的：**
- ❌ Redis — MVP 不做语义缓存
- ❌ fastembed — 同上
- ❌ SQLAlchemy — 继续用 DynamoDB

---

## 4. 新增配置项汇总

```bash
# === Provider 配置 ===
MULTI_PROVIDER_ENABLED=false          # 总开关
PROVIDER_KEY_ENCRYPTION_SECRET=xxx    # Provider Key 加密密钥

# === Failover 配置 ===
FAILOVER_ENABLED=true
FAILOVER_CHAINS={"openai/gpt-4o":["bedrock/claude-sonnet-4-5-20250929"]}

# === 路由配置 ===
ROUTING_ENABLED=false
ROUTING_RULES=[]
SMART_ROUTING_ENABLED=false
SMART_ROUTING_STRONG_MODEL=claude-sonnet-4-5-20250929
SMART_ROUTING_WEAK_MODEL=deepseek-chat
SMART_ROUTING_THRESHOLD=0.5

# === 压缩配置 ===
COMPRESSION_ENABLED=false
COMPRESSION_STRATEGY=moderate          # aggressive/moderate/conservative/off
COMPRESSION_TOOL_RESULT_MAX_CHARS=2000
COMPRESSION_FOLD_AFTER_TURNS=6
```

---

## 5. 新增 Prometheus Metrics

```python
# 路由指标
routing_decisions_total = Counter("routing_decisions_total", "Total routing decisions", ["strategy", "reason", "target_model"])
routing_decision_duration = Histogram("routing_decision_duration_seconds", "Routing decision latency")

# Failover 指标
failover_total = Counter("failover_total", "Total failovers", ["from_provider", "to_provider"])

# Provider 指标
provider_requests_total = Counter("provider_requests_total", "Requests by provider", ["provider", "model", "success"])
provider_request_duration = Histogram("provider_request_duration_seconds", "Provider latency", ["provider"])

# 压缩指标
compression_savings_chars = Histogram("compression_savings_chars", "Characters saved by compression")
compression_savings_pct = Histogram("compression_savings_pct", "Compression savings percentage")
```

---

## 6. 文件变更总览

```
新增文件（~3000 行）：
  app/services/provider_base.py        ~50 行
  app/services/provider_registry.py    ~60 行
  app/services/bedrock_provider.py     ~80 行
  app/services/openai_provider.py      ~300 行  ← 最复杂（格式转换）
  app/services/anthropic_provider.py   ~100 行
  app/services/deepseek_provider.py    ~80 行
  app/keypool/manager.py               ~150 行
  app/keypool/failover.py              ~100 行
  app/keypool/encryption.py            ~40 行
  app/routing/engine.py                ~250 行
  app/routing/rules.py                 ~80 行
  app/routing/smart.py                 ~100 行
  app/compression/context_compressor.py ~200 行
  tests/unit/test_openai_converter.py  ~300 行
  tests/unit/test_routing.py           ~200 行
  tests/unit/test_compression.py       ~200 行
  tests/unit/test_keypool.py           ~200 行

改动文件（~500 行改动）：
  app/api/messages.py                  ~100 行改动
  app/core/config.py                   ~50 行新增配置
  app/core/metrics.py                  ~30 行新增 metrics
  app/db/dynamodb.py                   ~80 行新增 provider key 查询
  app/main.py                          ~30 行初始化新模块
  docker-compose.yml                   无改动（不加 Redis）
```

---

## 7. 开发排期

| 周 | 内容 | 交付 |
|----|------|------|
| **W1** | Provider 接口 + BedrockProvider + OpenAIProvider（含格式转换）+ 单测 | SDK 可通过网关调 GPT-4o |
| **W2** | KeyPool + Failover + AnthropicProvider + DeepSeekProvider + 加密 | 限流自动切换 |
| **W3** | 路由引擎 + 规则路由 + RouteLLM 集成 + 预算降级 | 智能路由跑通 |
| **W4** | 上下文压缩 + 主流程集成 + 集成测试 + 文档 + Admin Portal 扩展 | MVP 完成 |

**总计：4 周**

---

> **Amy | 技术总监 | 锐评OPC** ⌨️
