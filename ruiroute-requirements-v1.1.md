# anthropic_api_proxy 升级需求文档 — 多 Provider 智能路由网关

> **文档编号：** PRD-001  
> **版本：** v1.1（根据川哥决策更新）  
> **作者：** Amy（技术总监）  
> **日期：** 2026-03-04  
> **状态：** 已确认

---

## 1. 项目背景

### 1.1 现状

现有项目 `anthropic_api_proxy` 是一个 Anthropic-Bedrock API 代理，功能成熟（30K 行代码），但仅支持 AWS Bedrock 单一后端。

### 1.2 业务目标

将现有项目升级为 **AI Agent 专用的 LLM 智能路由网关**，核心价值：**帮用户降低 30%+ 的 LLM API 调用成本**。

### 1.3 目标用户

- 使用 OpenClaw 等 AI Agent 平台的开发者
- 中小团队，有多个 LLM Provider API Key，需要统一管理
- 对 Token 成本敏感的用户

### 1.4 关键决策（已确认）

| 决策项 | 结论 |
|--------|------|
| 项目名 | 不改名，继续叫 `anthropic_api_proxy` |
| API 端点 | 只保留 Anthropic Messages API，不加 OpenAI Chat Completions 端点 |
| Key 加密 | 应用层加密，不依赖 KMS |
| 语义缓存 | MVP 不做，后续迭代 |
| 商业化 | MVP 完成后先内部使用验证 |

---

## 2. 功能需求

### 2.1 多 Provider 支持

**优先级：P0（必须）**

#### 需求描述

在现有 Bedrock 后端基础上，新增对 OpenAI、Anthropic Direct、DeepSeek 等 Provider 的支持。用户通过同一个 Anthropic Messages API 端点访问所有模型。

#### 用户故事

- 作为用户，我希望用同一个 API Key 和同一个 base_url 调用不同 Provider 的模型
- 作为用户，我希望请求格式统一为 Anthropic Messages API 格式，网关自动做格式转换
- 作为用户，我希望能通过 Admin Portal 配置和管理各 Provider 的 API Key

#### 功能清单

| 编号 | 功能点 | 说明 | 优先级 |
|------|--------|------|--------|
| F-1.1 | Provider 接口抽象 | 统一的 LLMProvider 基类，定义 invoke/invoke_stream/list_models 等方法 | P0 |
| F-1.2 | Bedrock Provider | 复用现有 bedrock_service.py，包装为 Provider 接口 | P0 |
| F-1.3 | OpenAI Provider | 支持 gpt-4o、gpt-4o-mini、o1 等，Anthropic ↔ OpenAI 格式双向转换 | P0 |
| F-1.4 | Anthropic Direct Provider | 直连 Anthropic API（非 Bedrock），格式基本一致，主要是 auth 和端点不同 | P0 |
| F-1.5 | DeepSeek Provider | 支持 deepseek-chat、deepseek-reasoner，走 OpenAI 兼容接口 | P1 |
| F-1.6 | Provider 注册中心 | 管理所有已注册的 Provider，按模型名查找 Provider | P0 |
| F-1.7 | 统一模型列表 | `/v1/models` 返回所有 Provider 的可用模型 | P0 |
| F-1.8 | 格式转换 | Anthropic Messages API ↔ OpenAI Chat Completions 双向转换（内部用，不暴露 OpenAI 端点） | P0 |
| F-1.9 | Streaming 统一 | 所有 Provider 的 streaming 响应统一转为 Anthropic SSE 格式 | P0 |

#### 格式转换覆盖范围

| 内容类型 | Anthropic 格式 | OpenAI 格式 | 优先级 |
|----------|---------------|-------------|--------|
| 文本 | TextContent | message.content (string) | P0 |
| 图片 | ImageContent (base64) | image_url content part | P0 |
| 工具调用 | ToolUseContent | tool_calls | P0 |
| 工具结果 | ToolResultContent | tool message | P0 |
| System | system (顶层字段) | system message | P0 |
| Thinking | ThinkingContent | 无原生支持，转为文本或忽略 | P1 |

#### 验收标准

- [ ] Anthropic SDK 设置 base_url 指向网关后，能成功调用 OpenAI GPT-4o
- [ ] Anthropic SDK 调用 DeepSeek V3，请求/响应格式自动转换
- [ ] Streaming 模式下各 Provider 的 SSE 事件格式一致
- [ ] `/v1/models` 返回所有已配置 Provider 的模型列表
- [ ] 现有 Bedrock 用户升级后行为完全不变（向后兼容）

---

### 2.2 多 Key 轮换与跨 Provider Failover

**优先级：P0（必须）**

#### 需求描述

支持同一 Provider 配置多个 API Key 自动轮换，当所有 Key 都被限流时自动切换到备用 Provider。

#### 用户故事

- 作为用户，我希望为同一个 Provider（如 OpenAI）配置多个 API Key，网关自动轮换使用
- 作为用户，我希望当一个 Key 被 rate limit 时，网关自动切到下一个可用 Key
- 作为用户，我希望当一个 Provider 所有 Key 都不可用时，网关自动 failover 到备用 Provider
- 作为用户，我希望 failover 是透明的，客户端无需处理

#### 功能清单

| 编号 | 功能点 | 说明 | 优先级 |
|------|--------|------|--------|
| F-2.1 | Provider Key 注册 | DynamoDB 中为每个 Provider 存储多个 API Key（应用层加密） | P0 |
| F-2.2 | Key 轮换 | Round-Robin 轮换，自动跳过冷却中的 Key | P0 |
| F-2.3 | Rate Limit 检测 | 解析 Provider 响应头（x-ratelimit-remaining 等），实时更新 Key 状态 | P0 |
| F-2.4 | Key 冷却 | 被 rate limit 的 Key 进入冷却期（retry-after 或默认 60s） | P0 |
| F-2.5 | Failover 链配置 | 配置跨 Provider 的 failover 映射（如 openai/gpt-4o → bedrock/claude-sonnet） | P0 |
| F-2.6 | Failover 事件记录 | 记录日志 + Prometheus metrics | P1 |

#### DynamoDB Schema 扩展

现有 `anthropic-proxy-api-keys` 表新增字段：

```
新增字段：
  provider: str          # "bedrock" | "openai" | "anthropic" | "deepseek"
  provider_api_key: str  # 实际 Provider API Key（应用层 AES-256 加密）
  provider_models: list  # 该 Key 可用的模型列表
```

#### 验收标准

- [ ] 同一 Provider 配 3 个 Key，请求均匀分布
- [ ] 模拟 Key1 返回 429，后续请求自动切到 Key2
- [ ] 模拟所有 OpenAI Key 限流，请求自动 failover 到 Bedrock
- [ ] Failover 时客户端无感知（响应格式不变）
- [ ] Prometheus metrics 记录 failover 次数

---

### 2.3 智能路由

**优先级：P1（重要）**

#### 需求描述

根据请求复杂度、用户预算、模型成本等因素，自动选择最优模型和 Provider。

#### 用户故事

- 作为用户，我希望简单问题自动路由到便宜模型，复杂问题路由到强模型
- 作为用户，我希望能配置路由策略：成本优先 / 质量优先 / 自动平衡
- 作为用户，我希望能设置规则路由（如"包含代码关键词 → Claude"）
- 作为用户，我希望预算快用完时，网关自动降级到更便宜的模型

#### 功能清单

| 编号 | 功能点 | 说明 | 优先级 |
|------|--------|------|--------|
| F-3.1 | 规则路由 | 基于关键词/正则/模型名的 if-else 规则引擎 | P1 |
| F-3.2 | 成本路由 | 按 token 单价排序，选最便宜的可用模型 | P1 |
| F-3.3 | 智能路由 | 集成 RouteLLM，基于 query 复杂度自动选模型 | P1 |
| F-3.4 | 预算感知降级 | 月预算使用超 80% 时自动降级模型 | P1 |
| F-3.5 | 路由策略配置 | per-key 配置路由策略（cost / quality / auto / off） | P1 |
| F-3.6 | 路由决策日志 | 记录每次路由：原始模型 → 实际模型 → 决策原因 | P1 |

#### 路由决策流程

```
请求进入
  ↓
1. 检查是否有强制规则 → 命中则直接路由
  ↓
2. 检查路由策略
   ├─ "off" → 直接用请求中的模型
   ├─ "cost" → 选最便宜的可用模型
   ├─ "quality" → 选最强的可用模型
   └─ "auto" → RouteLLM 判断复杂度后选模型
  ↓
3. 检查预算 → 超 80% 则降级
  ↓
4. 选择 Provider + Key → 调用
```

#### 验收标准

- [ ] 规则"包含 python/code → claude-sonnet"路由正确
- [ ] 成本优先模式下路由到最便宜的可用模型
- [ ] 智能路由：简单 query 和复杂 query 路由到不同模型
- [ ] 月预算用了 80% 后自动从 gpt-4o 降级到 gpt-4o-mini
- [ ] 路由日志可在 Admin Portal 查看

---

### 2.4 Agent 上下文压缩

**优先级：P1（重要 — 差异化功能）**

#### 需求描述

在网关层透明压缩 Agent 多轮对话上下文，减少 Token 消耗。竞品（LiteLLM/OpenRouter/Portkey）均未实现此功能。

#### 用户故事

- 作为 Agent 开发者，我希望网关自动压缩过长的工具调用结果
- 作为用户，我希望 10 轮以上对话不会因 Token 膨胀而变贵
- 作为用户，我希望压缩是透明的，不影响响应质量
- 作为用户，我希望能配置压缩策略或关闭

#### 功能清单

| 编号 | 功能点 | 说明 | 优先级 |
|------|--------|------|--------|
| F-4.1 | 工具结果截断 | tool_result > 2000 字符 → 保留首 500 + 尾 500 + 中间省略标记 | P1 |
| F-4.2 | 历史对话折叠 | 超过 N 轮（默认 6）的旧对话折叠为摘要 | P1 |
| F-4.3 | 压缩策略配置 | per-key：aggressive / moderate / conservative / off | P1 |
| F-4.4 | 压缩统计 | 记录原始 token / 压缩后 token / 节省比例 | P1 |
| F-4.5 | 重复内容消除 | 多轮中重复的 system prompt 片段去重 | P2 |

#### 验收标准

- [ ] 5000 字符工具结果压缩后 < 1500 字符
- [ ] 15 轮对话请求，压缩后 token 数减少 40%+
- [ ] 压缩后 LLM 响应质量无明显下降
- [ ] compression=off 时请求原样透传
- [ ] 用量记录包含压缩节省的 token 数

---

## 3. 非功能需求

### 3.1 性能

| 指标 | 要求 |
|------|------|
| 网关附加延迟 | < 50ms（不含 LLM 响应） |
| 智能路由决策 | < 10ms |
| 并发支持 | > 100 req/s（单实例） |

### 3.2 兼容性

| 要求 | 说明 |
|------|------|
| API 兼容 | 100% Anthropic Messages API（现有能力保持） |
| 向后兼容 | 现有纯 Bedrock 用户升级无需改客户端代码 |
| Feature Flag | 所有新功能默认关闭，通过环境变量开启 |

### 3.3 安全

| 要求 | 说明 |
|------|------|
| Provider Key 存储 | AES-256 应用层加密，存 DynamoDB |
| 日志脱敏 | Provider Key 不出现在日志中 |

---

## 4. 不做的事情（MVP 排除）

| 功能 | 原因 | 计划 |
|------|------|------|
| 语义缓存（Redis） | 川哥决策 MVP 不引入 Redis 依赖 | v2 迭代 |
| OpenAI Chat Completions 端点 | 川哥决策先不支持 | v2 迭代 |
| 成本节省追踪报表 | 优先级低 | v2 迭代 |
| 本地模型 Provider（vLLM/Ollama） | 非核心用户场景 | v2 迭代 |
| Key 健康检查 | 锦上添花 | v2 迭代 |
| 路由 A/B 测试 | 需要更多数据基础 | v2 迭代 |
| 项目改名 | 川哥决策 | 待定 |

---

## 5. 交付计划

| 里程碑 | 周 | 交付物 | 验收标准 |
|--------|---|--------|---------|
| **M1: Provider 抽象** | W1 | Provider 接口 + BedrockProvider + OpenAIProvider | Anthropic SDK 可通过网关调用 GPT-4o |
| **M2: 高可用** | W2 | 多 Key 轮换 + Failover + AnthropicProvider + DeepSeekProvider | 模拟限流后自动切换 |
| **M3: 智能路由** | W3 | RouteLLM 集成 + 规则路由 + 预算降级 | 简单/复杂 query 路由到不同模型 |
| **M4: 上下文压缩** | W4 | 工具结果截断 + 历史折叠 + 压缩统计 | 15 轮对话 token 减少 40%+ |
| **M5: 发布** | W4-5 | 集成测试 + Docker 打包 + 文档更新 + Admin Portal 路由/压缩配置 | 全部验收标准通过 |

---

> **Amy | 技术总监 | 锐评OPC** ⌨️
