# 在 Amazon Bedrock 上实现 Anthropic Web Search 与 Web Fetch —— Server-Managed Tools 的自建 Proxy 方案

## 前言

在上一篇博客[《使用 Amazon Bedrock + 自建 ECS Docker Sandbox 实现 Agent 程序化工具调用 Programmatic Tool Calling》](https://aws.amazon.com/cn/blogs/china/programmatic-tool-calling-agent-using-bedrock-and-ecs-docker-sandbox/)中，我们介绍了如何通过自建 Docker Sandbox 在 Amazon Bedrock 上实现 Anthropic 的 Programmatic Tool Calling（PTC），让 Claude 能够生成 Python 代码来编排工具调用，从而大幅降低 Token 消耗并提升推理准确率。本篇是该系列的第二篇，聚焦另一类重要的服务端特性：**Web Search 与 Web Fetch**。

Anthropic API 近期推出的 `web_search` 和 `web_fetch` 是一种被称为 **Server-Managed Tools（服务端托管工具）** 的新能力。与传统的 Client-Side Tool（客户端工具）不同，这类工具由 Anthropic 服务端直接执行 —— 客户端只需在请求的 `tools` 列表中声明工具类型，Claude 便会在推理过程中自主调用搜索引擎或抓取网页，将实时信息融入回答。然而，AWS Bedrock 的 InvokeModel API **并不支持**这类 server-managed tool 声明。如果请求中包含 `type: "web_search_20250305"` 这样的工具定义，Bedrock 会直接返回错误。

本文将详细介绍我们如何在自建 Proxy 的中间层实现 Web Search 和 Web Fetch 这两个服务端工具，使得**客户端使用 Anthropic Python SDK 无需任何代码修改**，即可在 Bedrock 上获得与 Anthropic 官方 API 完全一致的搜索和抓取体验。文章涵盖实现原理、架构设计，以及与 Anthropic 官方 API 的详细对比验证。

---

## 一、背景

### 1.1 Web Search 简介

Anthropic 的 Web Search 工具赋予 Claude 搜索互联网、获取实时信息的能力。当开发者在请求中声明 web_search 工具后，Claude 可以在推理过程中主动发起搜索查询，获取最新的网页内容，并基于搜索结果生成带有来源引用的回答。

目前 Anthropic 提供了两个版本的 Web Search 工具：

| 版本 | 类型标识 | Beta Header | 核心特性 |
|------|---------|-------------|---------|
| 标准版 | `web_search_20250305` | `web-search-2025-03-05` | Web 搜索 + 结构化引用（citation） |
| 增强版 | `web_search_20260209` | `web-search-2026-02-09` | 标准搜索 + **Dynamic Filtering**（代码执行过滤） |

标准版提供了基础的搜索与引用能力，而增强版在此基础上加入了 Dynamic Filtering 特性，让 Claude 能够通过编写和执行代码来进一步过滤、分析搜索结果，显著提升了复杂查询场景下的回答准确率。

### 1.2 Dynamic Filtering：搜索结果的智能过滤

Dynamic Filtering 是 Anthropic 于 2026 年 2 月推出的增强搜索能力。根据 Anthropic 官方博客（[Improved Web Search with Dynamic Filtering](https://claude.com/blog/improved-web-search-with-dynamic-filtering)）公布的基准测试数据：

- **BrowseComp 基准**：Sonnet 从 33.3% 提升至 46.6%，Opus 从 45.3% 提升至 61.6%
- **平均准确率提升 11%**，**Token 效率提升 24%**

Dynamic Filtering 的核心思想是：当标准搜索返回大量结果后，Claude 不再仅凭自然语言理解来筛选信息，而是**自动编写 Python 代码来解析、过滤和交叉引用搜索结果**，只保留与问题最相关的内容，然后基于精炼后的数据生成回答。正如 Anthropic 官方博客所描述的，启用 Dynamic Filtering 后，Claude "behaves like an actual researcher, writing Python to parse, filter, and cross-reference results" —— 像一位真正的研究员那样，用代码来处理和分析数据。

这种方法在需要数值计算、数据对比或精确信息提取的场景中尤为有效。例如查询两家公司的财务指标对比时，Claude 可以编写代码从搜索结果中提取具体数字并进行计算，而非依赖模型自身的数值推理能力。

### 1.3 Web Fetch 简介

与 Web Search 搜索关键词获取多条摘要不同，Web Fetch 允许 Claude 直接抓取指定 URL 的完整页面内容。两者的对比如下：

| 对比维度 | Web Search | Web Fetch |
|---------|-----------|-----------|
| **输入** | 搜索关键词（query） | 具体 URL |
| **输出** | 多条搜索结果摘要 | 单个 URL 的完整页面内容 |
| **典型场景** | "搜索 Python 最新版本" | "读取 docs.python.org 的发布说明" |
| **内容深度** | 每条结果的部分内容 | 完整文档内容 |
| **结果数量** | 每次搜索返回 5 条（可配置） | 每次抓取 1 个 URL |

Web Fetch 同样提供标准版（`web_fetch_20250910`）和增强版（`web_fetch_20260209`，支持 Dynamic Filtering）。典型应用场景包括：读取技术文档的具体页面、获取 API 参考的详细内容、抓取特定网页进行数据提取等。

### 1.4 Bedrock 的局限

AWS Bedrock 的 InvokeModel API 在工具调用方面采用了标准的 tool definition 格式，即每个工具必须包含 `name`、`description` 和 `input_schema` 字段。对于 Anthropic 特有的 server-managed tool 声明（如 `type: "web_search_20250305"`），Bedrock **无法识别**，请求会被直接拒绝并返回验证错误。

这意味着，即使底层使用的是同一个 Claude 模型，通过 Bedrock 调用时也无法直接使用 Web Search 和 Web Fetch 这两项能力。这正是本文要解决的核心问题：**如何在 Proxy 层弥补这一差距，让 Bedrock 上的 Claude 也能具备实时搜索和网页抓取能力**。
