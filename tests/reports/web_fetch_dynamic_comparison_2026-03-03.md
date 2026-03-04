# Web Fetch Dynamic Filtering + Citations: Official Anthropic API vs Proxy 对比报告

**测试日期**: 2026-03-04
**测试工具**: `web_fetch_20260209` (Dynamic Filtering) + `citations: {enabled: true}`
**测试命令**:
- Official: `python tests/web_fetch_test.py --official --citations`
- Proxy: `python tests/web_fetch_test.py --citations`

**测试模型**: `claude-sonnet-4-6`
**抓取 URL**: `https://httpbin.org/html`（Herman Melville - Moby-Dick 片段）
**用户提问**: "Please fetch the content at https://httpbin.org/html and count how many times 'hammer' appears"

> **本次测试为真实的 Official vs Proxy 对比**：`--official` 使用 `Anthropic(api_key=ANTHROPIC_API_KEY)` 直连 `api.anthropic.com`；无 `--official` 使用 `Anthropic(api_key=API_KEY, base_url=PROXY_URL)` 经过 Proxy（Bedrock）。

---

## 1. 基本指标对比

| 指标 | Official (Anthropic API) | Proxy (Bedrock) | 差异 |
|------|-------------------------|-----------------|------|
| **input_tokens** | 5,911 | 5,954 | +43 (+0.7%) |
| **output_tokens** | 1,307 | 1,318 | +11 (+0.8%) |
| **content blocks 总数** | 9 | 9 | 相同 |
| **server_tool_use blocks** | 2 | 2 | 相同 |
| **web_fetch_tool_result blocks** | 1 | 1 | 相同 |
| **bash_code_execution_tool_result** | 1 | 1 | 相同 |
| **text blocks** | 5 | 5 | 相同 |
| **text blocks with citations** | 2 | 2 | 相同 |
| **web_fetch_requests** | 1 | 1 | 相同 |
| **stop_reason** | end_turn | end_turn | 相同 |

> Token 差异 <1%，属于正常波动范围。Proxy 端 input_tokens 略高（+43），可能因为 Bedrock InvokeModel 在 token 计算上与 Anthropic 原生 API 存在微小差异。

---

## 2. Agentic Loop 行为对比

两者展现了**完全相同**的 3 轮迭代行为：

| 阶段 | Official (Anthropic API) | Proxy (Bedrock) |
|------|-------------------------|-----------------|
| **Iteration 1** | Claude 生成文本 + 调用 `web_fetch(url)` | 相同 |
| **Fetch 执行** | Anthropic 服务端抓取 → 3,602 chars | Proxy httpx 抓取 → 3,602 chars |
| **Iteration 2** | Claude 生成文本 + 调用 `bash_code_execution` | 相同 |
| **Bash 执行** | Anthropic sandbox 执行 Python → 3 次 | Proxy Docker sandbox 执行 → 3 次 |
| **Iteration 3** | Claude 生成最终文本回答（含 citations） | 相同 |

---

## 3. 抓取结果对比

### web_fetch_tool_result 结构

| 字段 | Official | Proxy | 一致性 |
|------|----------|-------|--------|
| **type** | `web_fetch_tool_result` | `web_fetch_tool_result` | 完全一致 |
| **content.type** | `web_fetch_result` | `web_fetch_result` | 完全一致 |
| **content.url** | `https://httpbin.org/html` | `https://httpbin.org/html` | 完全一致 |
| **content.retrieved_at** | `2026-03-04T01:17:20Z` | `2026-03-04T01:17:40Z` | 时间戳不同（正常，差 20s） |
| **content.content.type** | `document` | `document` | 完全一致 |
| **content.content.source.type** | `text` | `text` | 完全一致 |
| **content.content.source.media_type** | `text/plain` | `text/plain` | 完全一致 |
| **content.content.source.data** (长度) | 3,602 chars | 3,602 chars | 完全一致 |
| **content.content.title** | `null` | `null` | 完全一致 |
| **content.content.citations** | `{"enabled": true}` | `{"enabled": true}` | 完全一致 |

> 抓取内容（Moby-Dick 文本）在两者之间**逐字节一致**（3,602 chars）。

---

## 4. Citations（引用）对比 — 核心新增测试项

### Citations 结构

两者的 citations 格式**完全一致**，均使用 `char_location` 类型：

**Official:**
```json
{
  "type": "char_location",
  "cited_text": "Herman Melville - Moby-Dick\n\n \n\n \n\n Availing himself of the mild, summer-cool weather that now reigned in these latitudes, and in preparation for the ",
  "document_index": 0,
  "document_title": "",
  "start_char_index": 0,
  "end_char_index": 150,
  "file_id": null
}
```

**Proxy:**
```json
{
  "type": "char_location",
  "cited_text": "Herman Melville - Moby-Dick\n\n \n\n \n\n Availing himself of the mild, summer-cool weather that now reigned in these latitudes, and in preparation for the ",
  "document_index": 0,
  "document_title": "",
  "start_char_index": 0,
  "end_char_index": 150,
  "file_id": null
}
```

### Citations 字段兼容性

| 字段 | Official | Proxy | 一致性 |
|------|----------|-------|--------|
| **type** | `char_location` | `char_location` | 完全一致 |
| **cited_text** | `"Herman Melville - Moby-Dick..."` (150 chars) | 相同 | 完全一致 |
| **document_index** | `0` | `0` | 完全一致 |
| **document_title** | `""` (空字符串) | `""` (空字符串) | 完全一致 |
| **start_char_index** | `0` | `0` | 完全一致 |
| **end_char_index** | `150` | `150` | 完全一致 |
| **file_id** | `null` | `null` | 完全一致 |

### Citations 分布模式

两者对最终回答文本的 citations 拆分方式**完全一致**：

| Text Block | 内容 | Citations |
|------------|------|-----------|
| Block 1 | `The word **"hammer"** appears **3 times** in the content at...` | 1 citation (char_location) |
| Block 2 | `. The page contains an excerpt from *Herman Melville's Moby-Dick*...` | 1 citation (char_location) |
| Block 3 | `:\n\n1. *"...patient **hammer** wielded..."*\n2. ...` (列表部分) | 无 citation |

> 两者均将带有引用标注的文本拆分为多个 text block，citation 附着在引用了文档内容的 block 上，纯列表部分无 citation。这一行为**完全一致**。

---

## 5. Dynamic Filtering (Bash Code Execution) 对比

### Claude 生成的代码

两者均生成了统计 "hammer" 出现次数的 Python 代码，逻辑相同但细节有差异：

| 维度 | Official | Proxy |
|------|----------|-------|
| **统计方法** | `text.lower().count('hammer')` | `text.lower().count('hammer')` |
| **输出格式** | `Count of hammer: {count}` | `Total occurrences of hammer: {count}` |
| **引号转义** | `old man's`（三引号内无需转义） | `old man\\'s`（使用转义） |
| **上下文显示** | `text[m.start()-30:m.end()+30]` (30 chars) | `text[m.start()-40:m.end()+40]` (40 chars) |
| **输出前缀** | `{i}. ...{context}...` | `Position {m.start()}: ...{context}...` |

> 代码差异来自模型生成的自然变化，核心逻辑和计算结果完全一致。

### 执行结果

| 字段 | Official | Proxy | 一致性 |
|------|----------|-------|--------|
| **return_code** | 0 | 0 | 完全一致 |
| **stderr** | (empty) | (empty) | 完全一致 |
| **hammer 出现次数** | 3 | 3 | 完全一致 |
| **匹配 #1** | `...patient hammer wielded by a patient arm...` | 相同 | 完全一致 |
| **匹配 #2** | `...heavy beating of his hammer the heavy beating...` | 相同 | 完全一致 |
| **匹配 #3** | `...old husband's hammer; whose reverberations...` | 相同 | 完全一致 |

---

## 6. 最终回答对比

| 维度 | Official | Proxy |
|------|----------|-------|
| **结论** | "hammer" appears **3 times** | "hammer" appears **3 times** |
| **引用格式** | 3 条 Markdown 斜体 + 粗体引用 | 3 条 Markdown 斜体 + 粗体引用（相同） |
| **引用 #1** | `...patient **hammer** wielded by a patient arm...` | 相同 |
| **引用 #2** | `...heavy beating of his **hammer** the heavy beating...` | 相同 |
| **引用 #3** | `...her young-armed old husband's **hammer**; whose reverberations, muffled...` | `...the stout ringing of her young-armed old husband's **hammer**; whose reverberations...` |
| **背景说明** | *Herman Melville's Moby-Dick* | *Herman Melville's Moby-Dick* |

> 唯一差异：第 3 条引用上下文的截取范围略有不同（Proxy 多包含了 "the stout ringing of"），这是模型生成的自然变化。

---

## 7. 响应格式兼容性

### Content Block 序列

两者的 content blocks 顺序**完全相同**：

```
[0] text                              — "I'll fetch the content..."                   (citations: null)
[1] server_tool_use                   — web_fetch(url="https://httpbin.org/html")
[2] web_fetch_tool_result             — {web_fetch_result: {document: ..., citations: {enabled: true}}}
[3] text                              — "Now let me count..."                         (citations: null)
[4] server_tool_use                   — bash_code_execution(command="python3 -c ...")
[5] bash_code_execution_tool_result   — {stdout: "...hammer: 3\n...", return_code: 0}
[6] text                              — "The word **hammer** appears **3 times**..."  (citations: [char_location])
[7] text                              — ". The page contains an excerpt..."            (citations: [char_location])
[8] text                              — ":\n\n1. *...hammer...*\n2. ..."              (citations: null)
```

### JSON 结构兼容性

| 特性 | Official | Proxy | 兼容 |
|------|----------|-------|------|
| Message ID 前缀 | `msg-` | `msg-` | Yes |
| server_tool_use ID 前缀 | `srvtoolu_bdrk_` | `srvtoolu_bdrk_` | Yes |
| web_fetch_tool_result 嵌套结构 | `content.content.source.data` | 相同 | Yes |
| document block 格式 | `{type: "document", source: {...}, title: ...}` | 相同 | Yes |
| **citations 在 document 内** | `{citations: {enabled: true}}` | `{citations: {enabled: true}}` | Yes |
| **text block citations 字段** | `[{type: "char_location", ...}]` | `[{type: "char_location", ...}]` | Yes |
| **citations null 处理** | `citations: null`（无引用时） | `citations: null`（无引用时） | Yes |
| bash_code_execution_tool_result | `{type: "bash_code_execution_result", stdout, stderr, return_code}` | 相同 | Yes |
| usage.server_tool_use | `{web_fetch_requests: 1}` | `{web_fetch_requests: 1}` | Yes |
| cache_control 字段 | `null` | `null` | Yes |
| stop_reason | `end_turn` | `end_turn` | Yes |

---

## 8. 完整 JSON 响应结构骨架

```json
// 两者共享的响应结构：
{
  "id": "msg-...",
  "content": [
    {"type": "text", "text": "I'll fetch...", "citations": null},
    {"type": "server_tool_use", "id": "srvtoolu_bdrk_...", "name": "web_fetch", "input": {"url": "..."}},
    {"type": "web_fetch_tool_result", "tool_use_id": "srvtoolu_bdrk_...", "content": {
      "type": "web_fetch_result",
      "url": "https://httpbin.org/html",
      "content": {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "Herman Melville..."},
        "title": null,
        "citations": {"enabled": true}
      },
      "retrieved_at": "2026-03-04T..."
    }},
    {"type": "text", "text": "Now let me count...", "citations": null},
    {"type": "server_tool_use", "id": "srvtoolu_bdrk_...", "name": "bash_code_execution", "input": {"command": "python3 -c ..."}},
    {"type": "bash_code_execution_tool_result", "tool_use_id": "srvtoolu_bdrk_...", "content": {
      "type": "bash_code_execution_result",
      "stdout": "...hammer: 3\n...",
      "stderr": "",
      "return_code": 0
    }},
    {"type": "text", "text": "The word **\"hammer\"** appears **3 times**...", "citations": [
      {"type": "char_location", "cited_text": "Herman Melville...", "document_index": 0, "document_title": "", "start_char_index": 0, "end_char_index": 150, "file_id": null}
    ]},
    {"type": "text", "text": ". The page contains an excerpt...", "citations": [
      {"type": "char_location", ...}
    ]},
    {"type": "text", "text": ":\n\n1. *...hammer...*\n2. ...", "citations": null}
  ],
  "model": "claude-sonnet-4-6",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": "~5900",
    "output_tokens": "~1310",
    "cache_creation_input_tokens": null,
    "cache_read_input_tokens": null,
    "server_tool_use": {"web_fetch_requests": 1},
    "iterations": null
  }
}
```

---

## 9. 结论

| 评估维度 | 结果 |
|----------|------|
| **Token 消耗** | 差异 <1%（Official 5,911/1,307 vs Proxy 5,954/1,318） |
| **Agentic Loop 行为** | 相同的 3 轮迭代：fetch → bash → final answer |
| **抓取内容** | 逐字节一致（3,602 chars，相同的 Moby-Dick 文本） |
| **Dynamic Filtering** | 相同的计算逻辑，相同的执行结果（3 次 "hammer"） |
| **Citations 格式** | **100% 兼容** — `char_location` 类型、所有字段名/值完全一致 |
| **Citations 分布** | **完全一致** — 相同的 text block 拆分模式，相同的 citation 附着规则 |
| **响应格式** | 100% 兼容 — 所有 block types、ID 前缀、嵌套结构完全一致 |
| **最终回答** | 结论相同（3 次），仅第 3 条引用上下文截取范围有自然差异 |

### 与官方 Anthropic API 的 Citations 兼容性

本次测试**首次验证了 Proxy 的 citations 功能与 Anthropic 官方 API 的完全兼容性**：

- `citations: {enabled: true}` 在 `web_fetch_tool_result.content.content` 中正确传递
- Text blocks 的 `citations` 字段格式完全一致（`char_location` 类型）
- Citation 字段完整性：`type`, `cited_text`, `document_index`, `document_title`, `start_char_index`, `end_char_index`, `file_id` — 全部匹配
- Text block 拆分策略一致：引用文档内容的片段带 citation，纯列表部分不带
- 无 citation 时统一为 `citations: null`

### 与之前测试（无 Citations）的对比

| 维度 | 无 Citations (03-03) | 有 Citations (03-04) |
|------|---------------------|---------------------|
| **Content blocks** | 7 | 9（文本因 citations 边界拆分为更多 blocks） |
| **Text blocks** | 3 | 5（带 citation 的文本被拆分） |
| **Citations 字段** | 不存在 | `char_location` 类型，指向 document_index=0 |
| **input_tokens** | ~5,510 | ~5,930（+7.6%，citations 元数据增加） |
| **output_tokens** | ~1,367 | ~1,312（-4%，正常波动） |
| **功能正确性** | 通过 | 通过 |

**总体评价**: Proxy 实现的 `web_fetch_20260209` + Citations 功能在格式、数据结构、引用标注上与 Anthropic 官方 API **完全兼容**。Citations 的 `char_location` 类型正确关联了抓取文档的字符位置范围，text block 拆分策略与官方行为一致。
