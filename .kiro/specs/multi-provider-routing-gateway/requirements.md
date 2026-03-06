# 需求文档

## 简介

将现有 `anthropic_api_proxy` 项目（Anthropic-Bedrock API 代理，30K+ 行代码）升级为多 Provider 智能路由网关。本阶段聚焦于：Provider 抽象层（仅实现 Bedrock Provider）、多 Key 池与 Failover、智能路由、Agent 上下文压缩四大核心模块。所有新功能通过 Feature Flag 控制，默认关闭，确保现有 Bedrock 用户零影响升级。

核心价值：通过智能路由、多 Key 轮换和上下文压缩帮用户降低 30%+ 的 LLM API 调用成本。

### 关键决策

- 项目名不改，继续叫 `anthropic_api_proxy`
- 只保留 Anthropic Messages API 端点，不加 OpenAI Chat Completions 端点
- Provider Key 使用应用层加密（AES-256），不依赖 KMS
- MVP 不做语义缓存（不引入 Redis）
- 所有新功能通过 Feature Flag 控制，默认关闭
- DynamoDB 作为主数据存储（不引入 Redis）
- 本阶段仅实现 Bedrock Provider，其他 Provider（OpenAI、Anthropic Direct、DeepSeek）后续迭代

### 本阶段范围

- Provider 抽象层：LLMProvider 基类 + ProviderRegistry + BedrockProvider 实现
- 多 Key 池与 Failover：Key 轮换（Round-Robin）、Rate Limit 检测、Key 冷却、Failover 链配置
- 智能路由：规则路由、成本路由、RouteLLM 智能路由、预算感知降级、per-key 路由策略、Prompt Cache 感知路由（Cache Affinity）
- Agent 上下文压缩：工具结果截断、历史对话折叠、压缩策略配置、压缩统计
- 统一模型列表：`/v1/models` 返回所有已注册 Provider 的可用模型
- Feature Flag：所有新功能默认关闭，确保向后兼容
- Admin Portal 配置管理：通过管理后台配置 Provider Key、路由策略、压缩策略、Failover 链

### MVP 排除项

- OpenAI Provider、Anthropic Direct Provider、DeepSeek Provider（后续迭代）
- Anthropic ↔ OpenAI 格式双向转换（后续迭代）
- 语义缓存（Redis）
- OpenAI Chat Completions 端点
- 成本节省追踪报表
- 本地模型 Provider（vLLM/Ollama）
- Key 健康检查
- 路由 A/B 测试

## 术语表

- **Gateway（网关）**: anthropic_api_proxy 应用本身，作为客户端与 LLM Provider 之间的代理层
- **Provider**: LLM 服务提供商，本阶段仅包括 AWS Bedrock，架构设计支持后续扩展 OpenAI、Anthropic Direct、DeepSeek 等
- **Provider_Registry（Provider 注册中心）**: 管理所有已注册 Provider 实例的组件，负责按模型名查找对应 Provider
- **LLMProvider**: Provider 抽象基类，定义 name、invoke、invoke_stream、supports_model、get_cost 等统一接口
- **Bedrock_Provider**: 包装现有 BedrockService 的 Provider 实现，使用 IAM 认证
- **BedrockService**: 现有的 Bedrock 调用服务（app/services/bedrock_service.py），包含 Converse API 调用、streaming、缓存等完整功能
- **KeyPool_Manager（Key 池管理器）**: 管理各 Provider 多个 API Key 的组件，负责 Round-Robin 轮换和冷却状态追踪
- **Failover_Manager（Failover 管理器）**: 当 Provider 所有 Key 不可用时，按配置的 Failover 链切换到备用 Provider 的组件
- **Failover_Chain（Failover 链）**: 配置的跨 Provider 备用路由映射，如 bedrock/claude-sonnet → bedrock/claude-haiku（本阶段为同 Provider 内不同模型间的 Failover）
- **Routing_Engine（路由引擎）**: 根据策略（规则/成本/智能/关闭）决定请求路由到哪个 Provider 和模型的组件
- **Rule_Engine（规则引擎）**: 基于关键词、正则表达式、模型名的 if-else 规则匹配组件
- **Smart_Router（智能路由器）**: 集成 RouteLLM 库，基于 query 复杂度自动分类并选择模型的组件
- **Context_Compressor（上下文压缩器）**: 在网关层透明压缩 Agent 多轮对话上下文的组件
- **Feature_Flag**: 通过环境变量控制新功能开关的机制，所有新功能默认关闭
- **Cooldown（冷却期）**: API Key 被 rate limit 后的等待时间，取 retry-after 响应头值或默认 60 秒
- **Routing_Strategy（路由策略）**: per-key 配置的路由模式，包括 cost（成本优先）、quality（质量优先）、auto（智能）、off（关闭）
- **Compression_Strategy（压缩策略）**: per-key 配置的压缩模式，包括 aggressive、moderate、conservative、off
- **Key_Encryption（Key 加密）**: 使用 Fernet（AES-128-CBC + HMAC-SHA256）对 Provider API Key 进行应用层加密的机制
- **Admin_Portal（管理后台）**: 现有的管理界面（admin_portal/），包含 FastAPI 后端和 React 前端，用于管理 API Key、模型映射、定价等配置，本阶段扩展支持 Provider Key 管理、路由策略配置、压缩策略配置和 Failover 链配置
- **Cache_Affinity（缓存亲和性）**: 路由引擎在检测到请求包含 prompt cache 标记时，优先保持使用请求指定的模型，避免切换模型导致 cache 失效的机制
- **Cache-Active_Session（缓存活跃会话）**: 请求中 system、messages 或 tools 包含 cache_control 块的会话，表示客户端正在利用 prompt cache 功能

## 需求

### 需求 1: Provider 抽象层

**用户故事:** 作为网关开发者，我希望有一个统一的 Provider 抽象接口和注册中心，以便所有 LLM Provider 遵循相同的调用规范，实现可插拔的多 Provider 架构，方便后续扩展更多 Provider。

#### 验收标准

1. THE LLMProvider SHALL 定义 name 属性、invoke 方法、invoke_stream 方法、supports_model 方法和 get_cost 方法作为抽象接口
2. THE Provider_Registry SHALL 维护所有已注册 Provider 实例的映射，并支持按模型名查找对应的 Provider
3. WHEN 请求指定的模型名在 Provider_Registry 中存在对应 Provider 时，THE Provider_Registry SHALL 返回支持该模型的 Provider 列表
4. WHEN 请求指定的模型名在 Provider_Registry 中无对应 Provider 时，THE Provider_Registry SHALL 返回空列表
5. THE Gateway SHALL 通过 `/v1/models` 端点返回所有已注册 Provider 的可用模型列表
6. THE Provider_Registry SHALL 支持动态注册和注销 Provider 实例

### 需求 2: Bedrock Provider 包装

**用户故事:** 作为现有 Bedrock 用户，我希望升级后行为完全不变，网关将现有 BedrockService 包装为统一 Provider 接口，保持向后兼容。

#### 验收标准

1. THE Bedrock_Provider SHALL 包装现有 BedrockService，实现 LLMProvider 接口的所有抽象方法
2. WHEN Feature_Flag MULTI_PROVIDER_ENABLED 为 false 时，THE Gateway SHALL 使用与升级前完全相同的 Bedrock 调用路径，不经过 Provider 抽象层
3. WHEN Feature_Flag MULTI_PROVIDER_ENABLED 为 true 时，THE Gateway SHALL 通过 Bedrock_Provider 调用 BedrockService
4. THE Bedrock_Provider SHALL 支持现有所有 Bedrock 模型映射（default_model_mapping）中的模型
5. WHEN 通过 Bedrock_Provider 调用模型时，THE Bedrock_Provider SHALL 使用 IAM 认证而非 API Key
6. THE Bedrock_Provider SHALL 支持现有 BedrockService 的所有功能，包括 streaming、prompt caching、extended thinking、service tier 和 tool use

### 需求 3: 多 Key 注册与存储

**用户故事:** 作为管理员，我希望能为 Provider 配置多个 API Key 并安全存储，以便网关进行 Key 轮换和 Failover。

#### 验收标准

1. THE Gateway SHALL 支持在 DynamoDB 的 api-keys 表中为每个 Provider 存储多个 API Key，新增 provider、provider_api_key、provider_models 字段
2. THE Gateway SHALL 使用 Fernet 加密（基于 PROVIDER_KEY_ENCRYPTION_SECRET 环境变量派生密钥）存储所有 Provider API Key
3. WHEN 加密密钥（PROVIDER_KEY_ENCRYPTION_SECRET）未配置时，THE Gateway SHALL 在启动时记录警告日志并禁用多 Provider 功能
4. WHEN 解密 Provider API Key 失败时，THE Gateway SHALL 记录错误日志（不含密钥内容）并将该 Key 标记为不可用

### 需求 4: Key 轮换

**用户故事:** 作为用户，我希望为同一个 Provider 配置多个 API Key，网关自动轮换使用，避免单个 Key 被限流导致服务中断。

#### 验收标准

1. WHEN 选择 Provider API Key 时，THE KeyPool_Manager SHALL 使用 Round-Robin 策略在可用 Key 之间轮换
2. WHEN 同一 Provider 配置 N 个可用 Key 时，THE KeyPool_Manager SHALL 将请求均匀分布到 N 个 Key 上
3. THE KeyPool_Manager SHALL 自动跳过处于冷却期的 Key
4. WHEN 所有 Key 均处于冷却期时，THE KeyPool_Manager SHALL 返回空结果，触发 Failover 流程

### 需求 5: Rate Limit 检测与 Key 冷却

**用户故事:** 作为用户，我希望网关能自动检测 API Key 被限流的情况，将限流的 Key 暂时停用并切换到其他可用 Key。

#### 验收标准

1. WHEN Provider 响应状态码为 429 或抛出 ThrottlingError 时，THE KeyPool_Manager SHALL 将对应 API Key 标记为限流状态
2. WHEN Provider 响应包含 retry-after 头时，THE KeyPool_Manager SHALL 使用 retry-after 值作为冷却时间
3. WHEN Provider 响应不包含 retry-after 头时，THE KeyPool_Manager SHALL 使用默认 60 秒作为冷却时间
4. WHEN 冷却时间到期后，THE KeyPool_Manager SHALL 将 Key 恢复为可用状态
5. WHEN Provider 响应包含 x-ratelimit-remaining 头且值为 0 时，THE KeyPool_Manager SHALL 主动将对应 Key 标记为限流状态

### 需求 6: Failover 机制

**用户故事:** 作为用户，我希望当当前模型所有 Key 都不可用时，网关自动切换到备用模型或 Provider，保证服务不中断。

#### 验收标准

1. THE Gateway SHALL 支持通过 FAILOVER_CHAINS 环境变量配置 Failover 链映射（如 bedrock/claude-sonnet-4-5-20250929 → bedrock/claude-haiku-4-5-20251001）
2. WHEN 当前模型所有 Key 均不可用时，THE Failover_Manager SHALL 按 Failover 链顺序查找下一个可用的模型和 Key
3. WHEN Failover 发生时，THE Gateway SHALL 保持响应格式不变，客户端无感知
4. WHEN Failover 发生时，THE Gateway SHALL 记录 Failover 事件日志（包含 from_provider、to_provider、from_model、to_model）
5. IF Failover 链中所有目标均不可用，THEN THE Gateway SHALL 返回 503 Service Unavailable 错误
6. WHEN Feature_Flag FAILOVER_ENABLED 为 false 时，THE Gateway SHALL 不执行 Failover，直接返回原始错误

### 需求 7: 规则路由

**用户故事:** 作为用户，我希望能配置基于关键词、正则表达式或模型名的路由规则，将特定请求路由到指定模型。

#### 验收标准

1. WHEN 路由功能开启且存在配置的规则时，THE Rule_Engine SHALL 在其他路由策略之前优先检查规则匹配
2. THE Rule_Engine SHALL 支持基于关键词匹配的路由规则（用户最新消息包含指定关键词时命中，不区分大小写）
3. THE Rule_Engine SHALL 支持基于正则表达式匹配的路由规则（用户最新消息匹配指定正则时命中）
4. THE Rule_Engine SHALL 支持基于模型名匹配的路由规则（请求模型在指定源模型列表中时命中）
5. WHEN 规则命中时，THE Rule_Engine SHALL 返回规则配置的目标模型
6. WHEN 多条规则同时命中时，THE Rule_Engine SHALL 使用第一条命中的规则（按配置顺序）

### 需求 8: 成本路由

**用户故事:** 作为对成本敏感的用户，我希望网关能自动选择最便宜的可用模型，帮我降低 LLM API 调用成本。

#### 验收标准

1. WHEN 路由策略为 cost 时，THE Routing_Engine SHALL 按 token 单价排序所有可用模型，选择成本最低的模型
2. THE Routing_Engine SHALL 使用标准化的 token 数量（1000 input tokens + 500 output tokens）进行成本比较
3. WHEN 成本最低的模型无可用 Key 时，THE Routing_Engine SHALL 选择下一个成本最低的可用模型
4. IF 所有可用模型均无可用 Key，THEN THE Routing_Engine SHALL 抛出 NoProviderAvailableError

### 需求 9: 智能路由（RouteLLM 集成）

**用户故事:** 作为用户，我希望网关能根据 query 复杂度自动选择模型，简单问题用便宜模型，复杂问题用强模型，实现成本和质量的平衡。

#### 验收标准

1. WHEN 路由策略为 auto 时，THE Smart_Router SHALL 使用 RouteLLM 库对用户最新消息进行复杂度分类
2. WHEN 复杂度分类为 high 时，THE Smart_Router SHALL 路由到配置的 SMART_ROUTING_STRONG_MODEL
3. WHEN 复杂度分类为 low 时，THE Smart_Router SHALL 路由到配置的 SMART_ROUTING_WEAK_MODEL
4. THE Smart_Router SHALL 支持通过环境变量配置 strong_model、weak_model 和分类阈值（SMART_ROUTING_THRESHOLD）
5. WHEN Feature_Flag SMART_ROUTING_ENABLED 为 false 时，THE Gateway SHALL 不加载 RouteLLM 依赖

### 需求 10: 预算感知降级

**用户故事:** 作为用户，我希望月预算快用完时，网关自动降级到更便宜的模型，避免超支。

#### 验收标准

1. WHEN 用户月预算使用超过 80% 时，THE Routing_Engine SHALL 将复杂度强制设为 low，路由到 weak_model
2. THE Routing_Engine SHALL 从 api_key_info 中读取 budget_used_mtd 和 monthly_budget 字段计算预算使用比例
3. WHEN 路由策略为 off 时，THE Routing_Engine SHALL 不执行预算检查，直接使用请求中指定的模型

### 需求 11: 路由策略配置与日志

**用户故事:** 作为管理员，我希望能为每个 API Key 配置不同的路由策略，并查看路由决策日志。

#### 验收标准

1. THE Gateway SHALL 支持 per-key 配置路由策略，可选值为 cost、quality、auto、off
2. WHEN 路由策略为 off 时，THE Routing_Engine SHALL 直接使用请求中指定的模型和对应 Provider
3. WHEN 路由策略为 quality 时，THE Routing_Engine SHALL 选择配置中质量最高的可用模型
4. THE Routing_Engine SHALL 为每次路由决策记录日志，包含原始模型、实际模型和决策原因

### 需求 12: Prompt Cache 感知路由（Cache Affinity）

**用户故事:** 作为 Agent 开发者，我希望路由引擎在多轮对话中保持模型粘性，避免中途切换模型导致 prompt cache 失效、成本反而增加。

#### 验收标准

1. WHEN 请求包含 cache_control 块（system、messages 或 tools 中任一包含 cache_control）时，THE Routing_Engine SHALL 识别该请求为 cache-active 会话
2. WHEN 请求为 cache-active 会话且请求中已指定模型时，THE Routing_Engine SHALL 优先保持使用请求指定的模型（cache affinity），跳过成本路由和智能路由的模型切换逻辑
3. WHEN cache affinity 生效时，THE Routing_Engine SHALL 在路由决策日志中记录 reason 为 "cache_affinity"
4. WHEN cache-active 会话的当前模型所有 Key 不可用时（触发 Failover），THE Routing_Engine SHALL 允许 Failover 切换模型，并在日志中记录 cache 失效警告
5. WHEN 路由策略为 off 时，THE Routing_Engine SHALL 不执行 cache affinity 检查（与现有行为一致）
6. THE Routing_Engine SHALL 通过 CACHE_AWARE_ROUTING_ENABLED 环境变量控制 cache affinity 功能，默认为 true（当 ROUTING_ENABLED=true 时生效）

### 需求 13: 工具结果截断

**用户故事:** 作为 Agent 开发者，我希望网关自动压缩过长的工具调用结果，减少 Token 消耗而不影响关键信息。

#### 验收标准

1. WHEN tool_result 内容超过配置的最大字符数（默认 2000，通过 COMPRESSION_TOOL_RESULT_MAX_CHARS 配置）时，THE Context_Compressor SHALL 截断内容为首部字符（默认 500）+ 尾部字符（默认 500）+ 中间省略标记
2. WHEN tool_result 内容不超过配置的最大字符数时，THE Context_Compressor SHALL 保持内容不变
3. THE Context_Compressor SHALL 在省略标记中包含原始内容的字符数信息
4. WHEN 压缩策略为 off 时，THE Context_Compressor SHALL 不执行任何截断操作，请求原样透传
5. THE Context_Compressor SHALL 不修改包含 cache_control 块的消息内容，以避免破坏 prompt cache 前缀匹配

### 需求 14: 历史对话折叠

**用户故事:** 作为用户，我希望超过 N 轮的旧对话自动折叠为摘要，避免 Token 膨胀导致成本增加。

#### 验收标准

1. WHEN 消息距离对话末尾超过配置的折叠轮数（默认 6 轮，通过 COMPRESSION_FOLD_AFTER_TURNS 配置）时，THE Context_Compressor SHALL 将 assistant 消息折叠为简短摘要
2. WHEN 压缩策略为 aggressive 或 moderate 时，THE Context_Compressor SHALL 执行历史对话折叠
3. WHEN 压缩策略为 conservative 时，THE Context_Compressor SHALL 不执行历史对话折叠，仅执行工具结果截断
4. THE Context_Compressor SHALL 对长度不超过 200 字符的消息不执行折叠
5. THE Context_Compressor SHALL 使用简单截断方式生成摘要（保留前 150 字符 + "..."），不调用 LLM 生成摘要以避免额外成本
6. THE Context_Compressor SHALL 不折叠包含 cache_control 块的消息，以保持 prompt cache 前缀完整性

### 需求 15: 压缩策略配置与统计

**用户故事:** 作为用户，我希望能配置压缩策略或关闭压缩，并查看压缩节省的 Token 数。

#### 验收标准

1. THE Gateway SHALL 支持 per-key 配置压缩策略，可选值为 aggressive、moderate、conservative、off
2. THE Context_Compressor SHALL 记录每次压缩的统计信息，包含原始字符数、压缩后字符数和节省比例
3. THE Gateway SHALL 在用量记录中包含压缩节省的字符数

### 需求 16: Feature Flag 控制

**用户故事:** 作为运维人员，我希望所有新功能通过环境变量控制开关，默认关闭，按需开启，确保升级安全。

#### 验收标准

1. THE Gateway SHALL 通过 MULTI_PROVIDER_ENABLED 环境变量控制多 Provider 功能总开关，默认为 false
2. THE Gateway SHALL 通过 ROUTING_ENABLED 环境变量控制路由功能开关，默认为 false
3. THE Gateway SHALL 通过 SMART_ROUTING_ENABLED 环境变量控制智能路由开关，默认为 false
4. THE Gateway SHALL 通过 FAILOVER_ENABLED 环境变量控制 Failover 功能开关，默认为 true
5. THE Gateway SHALL 通过 COMPRESSION_ENABLED 环境变量控制压缩功能开关，默认为 false
6. WHEN 所有新功能 Feature_Flag 均为默认值时，THE Gateway SHALL 与升级前行为完全一致

### 需求 17: 安全与日志脱敏

**用户故事:** 作为安全负责人，我希望所有 Provider API Key 加密存储，且不出现在任何日志中。

#### 验收标准

1. THE Gateway SHALL 使用 Fernet 加密（基于 PROVIDER_KEY_ENCRYPTION_SECRET 派生密钥）存储所有 Provider API Key 到 DynamoDB
2. THE Gateway SHALL 确保 Provider API Key 明文不出现在任何日志输出中
3. WHEN 日志中需要引用 Provider API Key 时，THE Gateway SHALL 使用脱敏格式（如前 4 位 + "****" + 后 4 位）

### 需求 18: Admin Portal — Provider Key 管理

**用户故事:** 作为管理员，我希望通过 Admin Portal 为各 Provider 添加、编辑和删除 API Key，并查看 Key 的状态和可用模型。

#### 验收标准

1. THE Admin_Portal SHALL 提供 Provider Key 管理页面，展示所有已配置的 Provider Key 列表（包含 Provider 名称、Key 脱敏显示、可用模型、状态）
2. THE Admin_Portal SHALL 支持新增 Provider Key，表单包含 Provider 选择（bedrock/openai/anthropic/deepseek）、API Key 输入、可用模型列表配置
3. THE Admin_Portal SHALL 支持编辑已有 Provider Key 的可用模型列表和启用/禁用状态
4. THE Admin_Portal SHALL 支持删除 Provider Key
5. THE Admin_Portal SHALL 在 Key 列表中显示每个 Key 的当前状态（可用/冷却中/已禁用）
6. THE Admin_Portal 后端 API SHALL 在存储 Provider Key 时调用 Key_Encryption 进行加密，返回时不返回明文 Key

### 需求 19: Admin Portal — 路由策略配置

**用户故事:** 作为管理员，我希望通过 Admin Portal 为每个 API Key 配置路由策略，管理全局路由规则，以及配置智能路由参数。

#### 验收标准

1. THE Admin_Portal SHALL 在 API Key 编辑表单中新增路由策略选择字段，可选值为 cost、quality、auto、off
2. THE Admin_Portal SHALL 提供全局路由规则管理页面，支持添加、编辑、删除和排序路由规则
3. THE Admin_Portal SHALL 支持配置关键词路由规则（输入关键词列表 + 目标模型）
4. THE Admin_Portal SHALL 支持配置正则表达式路由规则（输入正则 + 目标模型）
5. THE Admin_Portal SHALL 支持配置模型名路由规则（输入源模型列表 + 目标模型）
6. THE Admin_Portal SHALL 支持拖拽或上下移动调整规则优先级顺序
7. THE Admin_Portal SHALL 提供智能路由全局配置面板，支持配置 strong_model（强模型）、weak_model（弱模型）和分类阈值（threshold）
8. THE Admin_Portal SHALL 在智能路由配置面板中提供模型下拉选择（从已注册 Provider 的可用模型中选取）
9. THE Admin_Portal 后端 SHALL 将路由规则和智能路由配置持久化到 DynamoDB，网关启动时加载并支持运行时热更新

### 需求 20: Admin Portal — 压缩策略配置

**用户故事:** 作为管理员，我希望通过 Admin Portal 为每个 API Key 配置压缩策略。

#### 验收标准

1. THE Admin_Portal SHALL 在 API Key 编辑表单中新增压缩策略选择字段，可选值为 aggressive、moderate、conservative、off
2. THE Admin_Portal SHALL 在压缩策略选择旁显示各策略的简要说明（aggressive：工具截断+历史折叠，moderate：工具截断+历史折叠，conservative：仅工具截断，off：不压缩）

### 需求 21: Admin Portal — Failover 链配置

**用户故事:** 作为管理员，我希望通过 Admin Portal 配置 Failover 链映射，指定当某个模型不可用时自动切换到哪个备用模型。

#### 验收标准

1. THE Admin_Portal SHALL 提供 Failover 链配置页面，展示所有已配置的 Failover 映射
2. THE Admin_Portal SHALL 支持新增 Failover 链条目，表单包含源 Provider/模型选择和目标 Provider/模型有序列表
3. THE Admin_Portal SHALL 支持编辑和删除已有的 Failover 链条目
4. THE Admin_Portal SHALL 支持为同一源模型配置多个有序的 Failover 目标（按优先级排序）
5. THE Admin_Portal 后端 SHALL 将 Failover 链配置持久化到 DynamoDB，网关启动时加载

### 需求 22: 性能目标（非验收标准）

**用户故事:** 作为用户，我希望网关附加延迟足够低，不影响 LLM 调用体验。

> **注意：** 以下为设计目标，供开发参考，不作为自动化验收测试标准。

#### 性能目标

1. 网关附加延迟（不含 LLM Provider 响应时间）目标低于 50 毫秒
2. 路由决策耗时目标低于 10 毫秒
3. 单实例并发处理能力目标超过 100 请求/秒
