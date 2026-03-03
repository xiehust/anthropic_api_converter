# Web Search Dynamic Filtering: Official API vs Proxy 对比报告

**测试日期**: 2026-03-03
**测试工具**: `web_search_20260209` (Dynamic Filtering)
**测试命令**:
- Official: `python web_search_test.py --dynamic --official`
- Proxy: `python web_search_test.py --dynamic`

**测试模型**: `claude-sonnet-4-6`
**用户提问**: "Compare the current stock prices and P/E ratios of AAPL and GOOGL. Which one has a better P/E ratio?"

---

## 1. 基本指标对比

| 指标 | Official API | Proxy (Bedrock) | 差异 |
|------|-------------|-----------------|------|
| **input_tokens** | 18,521 | 18,426 | -95 (-0.5%) |
| **output_tokens** | 1,373 | 1,420 | +47 (+3.4%) |
| **content blocks 总数** | 23 | 20 | -3 |
| **server_tool_use blocks** | 3 | 3 | 相同 |
| **web_search_tool_result blocks** | 2 | 2 | 相同 |
| **bash_code_execution_tool_result** | 1 | 1 | 相同 |
| **text blocks** | 17 (16 with citations) | 14 (13 with citations) | -3 |
| **web_search_requests** | 2 | 2 | 相同 |
| **stop_reason** | end_turn | end_turn | 相同 |

> Input tokens 差异 <1%，output tokens 差异 <4%，说明两者在 token 消耗上几乎一致。

---

## 2. 搜索查询对比

两者生成的搜索查询**完全相同**：

| 查询编号 | Official API | Proxy |
|---------|-------------|-------|
| #1 | `AAPL current stock price and P/E ratio 2025` | `AAPL current stock price and P/E ratio 2025` |
| #2 | `GOOGL current stock price and P/E ratio 2025` | `GOOGL current stock price and P/E ratio 2025` |

---

## 3. 搜索结果来源对比

每次搜索返回 5 条结果。核心数据源 (fullratio, macrotrends) 一致，部分次要来源因搜索引擎返回顺序不确定性略有不同。

### AAPL 搜索结果

| 排名 | Official API | Proxy |
|-----|-------------|-------|
| #1 | fullratio.com/stocks/nasdaq-aapl/pe-ratio | fullratio.com/stocks/nasdaq-aapl/pe-ratio |
| #2 | macrotrends.net/stocks/charts/AAPL/apple/pe-ratio | **statmuse.com/money/ask/apple-stock-pe-ratio-in-2025** |
| #3 | **tradingeconomics.com/aapl:us:pe** | macrotrends.net/stocks/charts/AAPL/apple/pe-ratio |
| #4 | **finance.yahoo.com/quote/AAPL/key-statistics/** | **companiesmarketcap.com/apple/pe-ratio/** |
| #5 | finance.yahoo.com/quote/AAPL/ | finance.yahoo.com/quote/AAPL/ |

### GOOGL 搜索结果

| 排名 | Official API | Proxy |
|-----|-------------|-------|
| #1 | fullratio.com/stocks/nasdaq-googl/pe-ratio | fullratio.com/stocks/nasdaq-googl/pe-ratio |
| #2 | macrotrends.net/stocks/charts/GOOGL/alphabet/pe-ratio | **wisesheets.io/pe-ratio/GOOGL** |
| #3 | **public.com/stocks/googl** | macrotrends.net/stocks/charts/GOOGL/alphabet/pe-ratio |
| #4 | companiesmarketcap.com/alphabet-google/pe-ratio/ | **public.com/stocks/googl** |
| #5 | **morningstar.com/stocks/xnas/googl/quote** | companiesmarketcap.com/alphabet-google/pe-ratio/ |

> 两者共享相同的核心来源 (fullratio, macrotrends, yahoo finance, public.com, companiesmarketcap)，差异来自搜索引擎返回的非确定性。

---

## 4. Dynamic Filtering (Bash Code Execution) 对比

两者都正确触发了 1 次 `bash_code_execution`，提取相同的数据并计算出**完全相同**的结果。

### 计算结果

| 指标 | Official API | Proxy | 一致性 |
|------|-------------|-------|--------|
| AAPL Stock Price | $264.72 | $264.72 | 完全一致 |
| AAPL TTM EPS | $7.92 | $7.92 | 完全一致 |
| AAPL P/E Ratio | 33.42 | 33.42 | 完全一致 |
| GOOGL Stock Price | $306.52 | $306.52 | 完全一致 |
| GOOGL TTM EPS | $10.91 | $10.91 | 完全一致 |
| GOOGL P/E Ratio | 28.10 | 28.10 | 完全一致 |
| AAPL 10yr Avg P/E | 24.05 | 24.05 | 完全一致 |
| GOOGL 10yr Avg P/E | 27.69 | 27.69 | 完全一致 |
| 结论 | GOOGL P/E 更低 (更好) | GOOGL P/E 更低 (更好) | 完全一致 |

### Bash 执行状态

| 指标 | Official API | Proxy |
|------|-------------|-------|
| return_code | 0 | 0 |
| stderr | (empty) | (empty) |
| 执行方式 | `python3 -c "..."` | `python3 -c "..."` |

---

## 5. 响应格式兼容性

| 特性 | Official API | Proxy | 兼容 |
|------|-------------|-------|------|
| Message ID 前缀 | `msg-` | `msg-` | Yes |
| server_tool_use ID 前缀 | `srvtoolu_bdrk_` | `srvtoolu_bdrk_` | Yes |
| Citation 类型 | `web_search_result_location` | `web_search_result_location` | Yes |
| Citation 字段 | url, title, cited_text, encrypted_index | url, title, cited_text, encrypted_index | Yes |
| bash result 类型 | `bash_code_execution_result` | `bash_code_execution_result` | Yes |
| encrypted_content 编码 | Base64 | Base64 | Yes |
| usage.server_tool_use | `web_search_requests: 2` | `web_search_requests: 2` | Yes |
| tool_use_id 关联 | web_search_tool_result.tool_use_id → server_tool_use.id | 相同 | Yes |

---

## 6. 内容呈现差异

虽然数据完全一致，两者的文本呈现风格略有不同：

| 风格 | Official API | Proxy |
|------|-------------|-------|
| 表格结构 | 合并对比表格 (AAPL vs GOOGL 并排) | 分开的独立表格 (AAPL、GOOGL 各一张) |
| Text blocks 数量 | 17 个 (更细粒度的 citation 分段) | 14 个 (更紧凑的 citation 分段) |
| 额外指标 | 包含 Forward P/E、Market Cap | 包含 Forward P/E (部分) |
| 历史均值段落 | 表格内嵌 | 独立段落 |

> 这种差异是正常的模型生成行为差异，不影响功能正确性。

---

## 7. Verification 测试结果

两者都通过了结构验证检查：

```
Official API:
  [OK] server_tool_use blocks: 3
  [OK] web_search_tool_result blocks: 2
  [OK] text blocks: 17
  [OK] text blocks with citations: 16
  [OK] server_tool_use IDs with srvtoolu_ prefix: 3
  [OK] web_search_tool_result.tool_use_id matches server_tool_use.id: 2/2
  [OK] usage.server_tool_use present: True
  --- PASSED ---
  Dynamic filtering: 1 bash execution(s) found

Proxy:
  [OK] server_tool_use blocks: 3
  [OK] web_search_tool_result blocks: 2
  [OK] text blocks: 14
  [OK] text blocks with citations: 13
  [OK] server_tool_use IDs with srvtoolu_ prefix: 3
  [OK] web_search_tool_result.tool_use_id matches server_tool_use.id: 2/2
  [OK] usage.server_tool_use present: True
  --- PASSED ---
  Dynamic filtering: 1 bash execution(s) found
```

---

## 8. 结论

| 评估维度 | 结果 |
|----------|------|
| **Token 消耗** | 差异 <1% (input) / <4% (output)，几乎一致 |
| **搜索行为** | 查询完全相同，核心来源一致，次要来源有自然差异 |
| **Dynamic Filtering** | 都正确触发 bash_code_execution，计算结果完全一致 |
| **响应格式** | 100% 兼容 — block types、ID 前缀、citation 格式、usage 字段全部一致 |
| **数据准确性** | 提取相同的股票数据，得出相同的分析结论 |
| **功能完整性** | 两者都完整支持 web_search_20260209 的 dynamic filtering 特性 |

**总体评价**: Proxy 通过 Bedrock 实现的 `web_search_20260209` dynamic filtering 与 Official Anthropic API 在功能、格式、数据准确性上**完全等价**。差异仅体现在搜索结果的自然随机性和模型生成的表述风格上，属于正常行为。
