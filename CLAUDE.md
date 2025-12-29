# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an **Anthropic-Bedrock API Proxy** - a FastAPI service that translates between the Anthropic Messages API format and AWS Bedrock's Converse API. This allows clients using the Anthropic Python SDK to seamlessly access any Bedrock model.

**Key Insight**: The service is bidirectional translation middleware. Requests flow through: Anthropic format → Bedrock format → Bedrock API → Bedrock response → Anthropic format.

## Development Setup

### Installation & Environment

```bash
# Install dependencies with uv (preferred)
uv sync

# Alternative: pip install with dev extras
pip install -e ".[dev]"

# Copy and configure environment
cp .env.example .env
# Edit .env with your AWS credentials and settings

# Setup DynamoDB tables
uv run  scripts/setup_tables.py

# Create an API key for testing
uv run  scripts/create_api_key.py --user-id dev-user --name "Development Key"
```

### Running the Service

```bash
# Development mode (with auto-reload)
uv run uvicorn app.main:app --reload

# Production mode
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Using Docker Compose (includes DynamoDB Local, Prometheus, Grafana)
docker-compose up -d
```

### Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/unit/test_converters.py

# Run integration tests only
uv run pytest -m integration

# Run with verbose output
uv run pytest -v
```

### Code Quality

```bash
# Format code
black app tests

# Lint code
ruff check app tests

# Type checking
mypy app
```

## Architecture

### Critical Conversion Flow

The core of this service is the bidirectional conversion between Anthropic and Bedrock formats:

**Request Flow:**
1. `app/api/messages.py` - Receives Anthropic-formatted request
2. `app/middleware/auth.py` - Validates API key from DynamoDB
3. `app/middleware/rate_limit.py` - Enforces token bucket rate limiting
4. `app/converters/anthropic_to_bedrock.py` - **Converts request to Bedrock format**
5. `app/services/bedrock_service.py` - Calls AWS Bedrock API
6. `app/converters/bedrock_to_anthropic.py` - **Converts response back to Anthropic format**
7. Response returned to client

**Streaming Flow:** Same as above, but step 6 happens per-event in a generator, with SSE formatting.

### Key Conversion Logic

**Model ID Mapping** (`app/core/config.py`):
- Anthropic model IDs (e.g., `claude-3-5-sonnet-20241022`) → Bedrock ARNs (e.g., `anthropic.claude-3-5-sonnet-20241022-v2:0`)
- Custom mappings stored in DynamoDB `model-mapping` table
- Falls back to treating unknown IDs as valid Bedrock ARNs

**Content Block Conversion** (`anthropic_to_bedrock.py`):
- `TextContent` → `{"text": "..."}`
- `ImageContent` → `{"image": {"format": "png", "source": {"bytes": b"..."}}}`
- `ToolUseContent` → `{"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}}`
- `ToolResultContent` → `{"toolResult": {"toolUseId": "...", "content": [...], "status": "success"}}`

**Streaming Event Conversion** (`bedrock_to_anthropic.py`):
- Bedrock's `contentBlockDelta` → Anthropic's `content_block_delta`
- Bedrock's `messageStart` → Anthropic's `message_start`
- SSE format: `event: <type>\ndata: <json>\n\n`

### DynamoDB Schema

**Critical Tables:**
1. **API Keys** (`anthropic-proxy-api-keys`):
   - PK: `api_key` - The actual API key string
   - Attributes: `user_id`, `name`, `is_active`, `rate_limit`, `service_tier`, `metadata`
   - GSI: `user_id-index` for querying by user

2. **Usage Tracking** (`anthropic-proxy-usage`):
   - PK: `api_key`, SK: `timestamp`
   - Attributes: `request_id`, `model`, `input_tokens`, `output_tokens`, `success`
   - GSI: `request_id-index` for request lookup

3. **Model Mapping** (`anthropic-proxy-model-mapping`):
   - PK: `anthropic_model_id`
   - Attributes: `bedrock_model_id`

### Configuration Management

All configuration is in `app/core/config.py` using Pydantic Settings:
- Loads from environment variables (`.env` file in development)
- Feature flags: `ENABLE_TOOL_USE`, `ENABLE_EXTENDED_THINKING`, `ENABLE_DOCUMENT_SUPPORT`
- AWS settings: Region, credentials, endpoint URLs
- Rate limiting: Requests per window, window duration
- Table names: All DynamoDB table names configurable

**Important:** When adding new features, add corresponding feature flags and configuration options.

## Common Development Tasks

### Adding Support for a New Anthropic Feature

1. **Update Pydantic schemas** (`app/schemas/anthropic.py` and `app/schemas/bedrock.py`)
2. **Update request converter** (`app/converters/anthropic_to_bedrock.py`):
   - Add conversion logic in appropriate method
   - Handle feature flag (if optional)
3. **Update response converter** (`app/converters/bedrock_to_anthropic.py`):
   - Add reverse conversion logic
   - Handle streaming events if applicable
4. **Add tests** (`tests/unit/test_converters.py`)
5. **Update documentation** (README.md, ARCHITECTURE.md)

### Adding a New Model Mapping

**Programmatically:**
```python
from app.db.dynamodb import DynamoDBClient

client = DynamoDBClient()
client.model_mapping_manager.set_mapping(
    anthropic_model_id="claude-sonnet-4-5-20250929",
    bedrock_model_id='global.anthropic.claude-sonnet-4-5-20250929-v1:0'
)
```

**Via Configuration:**
Update `DEFAULT_MODEL_MAPPING` in `app/core/config.py`.

### Debugging Conversion Issues

1. **Enable DEBUG logging** in `.env`: `LOG_LEVEL=DEBUG`
2. **Check conversion logs**: Look for `Converting Anthropic request` and `Converting Bedrock response` messages
3. **Inspect raw Bedrock requests/responses**: Add logging in `app/services/bedrock_service.py`
4. **Test converters directly**:
```python
from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.schemas.anthropic import MessageRequest

converter = AnthropicToBedrockConverter()
bedrock_request = converter.convert_request(your_request)
print(bedrock_request)
```

### Working with Streaming Responses

Streaming uses **Server-Sent Events (SSE)**. Key points:

- SSE format: `event: <event_type>\ndata: <json_data>\n\n`
- Must set headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`
- FastAPI's `StreamingResponse` handles SSE automatically when you yield strings in the correct format
- Bedrock streaming events arrive as `contentBlockDelta`, `messageStart`, etc.
- Convert each event to Anthropic format (`message_start`, `content_block_delta`, etc.)

**Implementation:** See `app/api/messages.py` → `create_message()` → streaming branch and `app/services/bedrock_service.py` → `invoke_model_stream()`.

### Programmatic Tool Calling (PTC)

PTC allows Claude to generate Python code that calls tools programmatically. The proxy implements this using a Docker sandbox for code execution with client-side tool execution.

**How PTC Works:**

1. Client sends request with:
   - Header: `anthropic-beta: advanced-tool-use-2025-11-20`
   - Tool: `{"type": "code_execution_20250825", "name": "code_execution"}`
   - Regular tools with `allowed_callers: ["code_execution_20250825"]`

2. **Detailed Flow (Multi-Tool, Multi-Round):**

   ```
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ INITIAL REQUEST                                                         │
   ├─────────────────────────────────────────────────────────────────────────┤
   │ Client Request → Proxy → Bedrock (Claude)                               │
   │                              ↓                                          │
   │                    Claude returns execute_code                          │
   │                              ↓                                          │
   │                    Proxy creates Docker container                       │
   │                    Proxy runs code in sandbox                           │
   └─────────────────────────────────────────────────────────────────────────┘
                                  ↓
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ TOOL CALL LOOP (repeats for each tool call from code)                   │
   ├─────────────────────────────────────────────────────────────────────────┤
   │ Code calls tool (e.g., get_expenses) → Sandbox PAUSES                   │
   │                              ↓                                          │
   │ Proxy returns tool_use to client (with caller field)                    │
   │                              ↓                                          │
   │ Client executes tool locally → sends tool_result back                   │
   │                              ↓                                          │
   │ Proxy RESUMES sandbox with tool result                                  │
   │                              ↓                                          │
   │ Code continues... (may call more tools → repeat loop)                   │
   └─────────────────────────────────────────────────────────────────────────┘
                                  ↓
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ CODE COMPLETION                                                         │
   ├─────────────────────────────────────────────────────────────────────────┤
   │ Code finishes execution → Proxy gets stdout/stderr                      │
   │                              ↓                                          │
   │ Proxy calls Claude with code output as tool_result                      │
   │                              ↓                                          │
   │ Claude generates final response (or requests more code → repeat all)    │
   │                              ↓                                          │
   │ Proxy returns Claude's response to client                               │
   └─────────────────────────────────────────────────────────────────────────┘
   ```

3. **Response Content Types:**

   Tool call from code (client must execute):
   ```json
   {
     "type": "tool_use",
     "id": "toolu_xxx",
     "name": "get_expenses",
     "input": {"employee_id": "ENG008"},
     "caller": {
       "type": "code_execution_20250825",
       "tool_id": "srvtoolu_yyy"
     }
   }
   ```

   Direct tool call (not from code execution):
   ```json
   {
     "type": "tool_use",
     "id": "toolu_xxx",
     "name": "search_docs",
     "input": {"query": "..."},
     "caller": {"type": "direct"}
   }
   ```

4. **Session Management:**
   - Container reuse via `container` field in response/request
   - Sessions timeout after 4.5 minutes (`PTC_SESSION_TIMEOUT`)
   - Client must include `container.id` in continuation requests
   - Proxy tracks pending tool calls per session

5. **Multi-Round Code Execution:**
   - After code completes, Claude may request another `execute_code`
   - Proxy handles this recursively until Claude returns text response
   - Same container is reused across multiple code execution rounds

6. **Parallel Tool Calls (asyncio.gather support):**

   Claude can generate code that calls tools in parallel using `asyncio.gather`:
   ```python
   # Claude generates code like this (instead of sequential loop)
   expense_tasks = [
       get_expenses(employee_id=emp_id, quarter='Q3')
       for emp_id in employee_ids
   ]
   results = await asyncio.gather(*expense_tasks)
   ```

   **How it works:**
   - Sandbox detects multiple tool calls within 100ms batch window
   - Proxy returns response with multiple `tool_use` blocks (one per tool)
   - Client executes all tools (can be parallel) and returns all `tool_result` blocks
   - Proxy batches results and injects them all into sandbox
   - Code resumes with all results available

   **Response format (batch):**
   ```json
   {
     "content": [
       {"type": "server_tool_use", "id": "srvtoolu_xxx", "name": "code_execution", ...},
       {"type": "tool_use", "id": "toolu_aaa", "name": "get_expenses", "input": {"employee_id": "ENG001"}, "caller": {...}},
       {"type": "tool_use", "id": "toolu_bbb", "name": "get_expenses", "input": {"employee_id": "ENG002"}, "caller": {...}},
       {"type": "tool_use", "id": "toolu_ccc", "name": "get_expenses", "input": {"employee_id": "ENG003"}, "caller": {...}}
     ],
     "stop_reason": "tool_use"
   }
   ```

   **Client continuation (all results in one request):**
   ```json
   {
     "messages": [..., {
       "role": "user",
       "content": [
         {"type": "tool_result", "tool_use_id": "toolu_aaa", "content": "..."},
         {"type": "tool_result", "tool_use_id": "toolu_bbb", "content": "..."},
         {"type": "tool_result", "tool_use_id": "toolu_ccc", "content": "..."}
       ]
     }],
     "container": {"id": "container_xxx"}
   }
   ```

   **Configuration:**
   - `tool_call_batch_window_ms`: Time window to collect parallel calls (default: 100ms)

**Key Files:**
- `app/services/ptc_service.py` - Main PTC orchestration, state management
- `app/services/ptc/sandbox.py` - Docker sandbox executor, socket IPC
- `app/services/ptc/exceptions.py` - PTC-specific exceptions
- `app/schemas/ptc.py` - PTC Pydantic models
- `app/schemas/anthropic.py` - Content types (ServerToolUseContent, ServerToolResultContent)

**Key Methods in PTCService:**
- `process_request()` - Entry point, detects PTC and orchestrates flow
- `_handle_code_execution()` - Runs code, handles tool call pauses
- `handle_tool_result_continuation()` - Resumes paused sandbox
- `_finalize_code_execution()` - Calls Claude after code completes
- `resume_execution()` - Injects tool result into sandbox, continues

**Health Check:**
```bash
curl http://localhost:8000/health/ptc
# Returns: {"status": "healthy", "docker": "connected", "active_sessions": 0, ...}
```

**Requirements:**
- Docker must be running and accessible
- `python:3.11-slim` image (or configured `PTC_SANDBOX_IMAGE`)

**Limitations:**
- Non-streaming only (streaming PTC not yet implemented)
- Network disabled in sandbox by default
- Tools are executed client-side (not in the sandbox)

## Testing Strategy

### Unit Tests (`tests/unit/`)

- **Converters**: Test all conversion paths (Anthropic↔Bedrock)
- **Schemas**: Validate Pydantic models with various inputs
- **Middleware**: Test auth and rate limiting logic

**Pattern:**
```python
def test_convert_text_content():
    converter = AnthropicToBedrockConverter()
    result = converter._convert_content_blocks("Hello")
    assert result == [{"text": "Hello"}]
```

### Integration Tests (`tests/integration/`)

- **Full request flow**: Auth → Rate limit → Conversion → Mock Bedrock → Response
- **Streaming**: Test SSE event generation
- **DynamoDB**: Test with moto for AWS mocking

**Pattern:**
```python
@pytest.mark.integration
async def test_full_message_flow(test_client):
    response = await test_client.post(
        "/v1/messages",
        json={...},
        headers={"x-api-key": "test-key"}
    )
    assert response.status_code == 200
```

### Mocking AWS Services

Use `moto` for DynamoDB and Bedrock mocking:
```python
from moto import mock_dynamodb, mock_bedrock

@mock_dynamodb
def test_with_dynamodb():
    # Your test code
    pass
```

## Important Design Decisions

### Why Synchronous boto3 Instead of Async?

DynamoDB operations are fast enough (single-digit milliseconds) that synchronous calls don't significantly impact performance. This simplifies the codebase and avoids aioboto3 complexity.

### Why Token Bucket for Rate Limiting?

Token bucket algorithm allows burst traffic while maintaining average rate limits. Each API key gets its own bucket stored in memory, refilled at a constant rate.

### Why DynamoDB Instead of Redis?

- **Persistence**: API keys and usage data persist across restarts
- **Serverless-friendly**: Works well with Lambda/ECS without managing Redis servers
- **Single-region deployment**: No need for cross-region replication (per requirements)
- **AWS integration**: Seamless with other AWS services

### Handling Bedrock-Specific Features Not in Anthropic API

Features like guardrails are Bedrock-specific. Strategy:
1. Add optional parameters to request schema
2. Pass through to Bedrock if present
3. Document as "extended" features
4. Don't break Anthropic SDK compatibility

## Troubleshooting

### "Rate limit exceeded" Errors

- Check token bucket configuration: `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW`
- Verify API key's custom rate limit in DynamoDB
- Rate limits reset based on time window, not calendar time

### "Invalid API key" Errors

- Ensure DynamoDB tables are created: `python scripts/setup_tables.py`
- Verify API key exists: Check `anthropic-proxy-api-keys` table
- Check `is_active` flag is `True`
- Master key bypasses validation (for admin use): Set `MASTER_API_KEY` in `.env`

### Conversion Errors

- Most common: Missing fields in Pydantic models
- Check that new Anthropic features are mapped in converters
- Verify model ID mapping exists (or allow passthrough)

### Streaming Cuts Off Early

- Check `STREAMING_TIMEOUT` setting
- Verify client keeps connection alive
- Look for exceptions in `invoke_model_stream()` generator

### AWS Credentials Issues

- For local development: Use AWS CLI credentials or environment variables
- For ECS/Lambda: Use IAM roles (preferred)
- Required permissions: `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `dynamodb:*`

### PTC / Docker Issues

- **"Docker not available" error**: Ensure Docker daemon is running (`docker ps`)
- **Container timeout**: Increase `PTC_EXECUTION_TIMEOUT` or check for infinite loops
- **Session expired**: Sessions timeout after 4.5 minutes; use `container.id` for reuse
- **Missing sandbox image**: Pull image manually: `docker pull python:3.11-slim`
- **Permission denied**: Ensure user has Docker socket access (`/var/run/docker.sock`)
- **Health check failing**: Check `/health/ptc` endpoint for detailed status
- **Tool calls not returning**: Verify client sends `tool_result` back to continue execution

## Project Structure Rationale

```
app/
├── api/              # Route handlers (thin layer, delegates to services)
├── converters/       # Core conversion logic (most complex code here)
├── core/             # Configuration, logging, metrics (cross-cutting concerns)
├── db/               # DynamoDB client and managers (data access layer)
├── middleware/       # Auth and rate limiting (request processing pipeline)
├── schemas/          # Pydantic models (validation and serialization)
└── services/         # Business logic (orchestrates converters and Bedrock calls)
    ├── bedrock_service.py  # Bedrock API calls
    ├── ptc_service.py      # PTC orchestration
    └── ptc/                # PTC sandbox module
        ├── sandbox.py      # Docker sandbox executor
        └── exceptions.py   # PTC exceptions
```

**Why this structure?**
- **Separation of concerns**: Each layer has a single responsibility
- **Testability**: Can test converters independently of API layer
- **FastAPI patterns**: Follows FastAPI best practices (routers, middleware, schemas)
- **Scalability**: Easy to add new endpoints without touching core conversion logic

## Environment Variables Reference

**Required:**
- `AWS_REGION` - AWS region for Bedrock and DynamoDB
- `MASTER_API_KEY` - Master key for admin access (or set `REQUIRE_API_KEY=False` for dev)

**Optional but Recommended:**
- `DYNAMODB_ENDPOINT_URL` - Use `http://localhost:8001` for DynamoDB Local
- `BEDROCK_ENDPOINT_URL` - Override Bedrock endpoint (rarely needed)
- `ENABLE_METRICS=True` - Exposes Prometheus metrics at `/metrics`

**Feature Flags:**
- `ENABLE_TOOL_USE=True` - Support function calling
- `ENABLE_EXTENDED_THINKING=True` - Support thinking blocks
- `ENABLE_DOCUMENT_SUPPORT=True` - Support document content
- `PROMPT_CACHING_ENABLED=False` - Prompt caching (not fully implemented)
- `ENABLE_PROGRAMMATIC_TOOL_CALLING=True` - Support PTC (requires Docker)

**Programmatic Tool Calling (PTC) Settings:**
- `PTC_SANDBOX_IMAGE=python:3.11-slim` - Docker image for sandbox
- `PTC_SESSION_TIMEOUT=270` - Session timeout in seconds (4.5 minutes)
- `PTC_EXECUTION_TIMEOUT=60` - Code execution timeout
- `PTC_MEMORY_LIMIT=256m` - Container memory limit
- `PTC_NETWORK_DISABLED=True` - Disable network in sandbox

See `.env.example` for full list.

## API Compatibility Notes

This service aims for **100% compatibility** with the Anthropic Messages API. Key differences:

1. **Model IDs**: Must use Anthropic-style IDs (e.g., `claude-3-5-sonnet-20241022`), which are mapped to Bedrock ARNs internally. You can also pass Bedrock ARNs directly.

2. **Thinking Blocks**: Bedrock may not support extended thinking natively. The service converts thinking blocks to text annotations where needed.

3. **Prompt Caching**: Anthropic's `cache_control` parameter is parsed but may not be fully supported by Bedrock. The service gracefully handles this.

4. **Rate Limiting**: This service adds rate limiting (not in base Anthropic API). Clients see `429` errors and `Retry-After` headers.

5. **Authentication**: Uses API keys in `x-api-key` header (consistent with Anthropic API).

6. **Programmatic Tool Calling**: Fully supported via Docker sandbox. Requires `anthropic-beta: advanced-tool-use-2025-11-20` header and Docker running on the server. Tools are executed client-side (returned to caller for execution).

## Key Files to Understand

1. **`app/converters/anthropic_to_bedrock.py`** - Request conversion (Anthropic → Bedrock)
2. **`app/converters/bedrock_to_anthropic.py`** - Response conversion (Bedrock → Anthropic)
3. **`app/services/bedrock_service.py`** - Orchestrates Bedrock API calls
4. **`app/api/messages.py`** - Main API endpoint handler
5. **`app/core/config.py`** - Configuration and settings
6. **`app/services/ptc_service.py`** - PTC orchestration (code execution flow)
7. **`app/services/ptc/sandbox.py`** - Docker sandbox executor
8. **`ARCHITECTURE.md`** - Detailed architecture documentation

## Performance Considerations

- **Conversion overhead**: ~10-50ms per request (negligible compared to Bedrock latency)
- **DynamoDB latency**: 1-10ms for API key lookup (synchronous call)
- **Streaming**: No buffering - events streamed as received from Bedrock
- **Bottleneck**: Almost always Bedrock API response time, not proxy logic

**Target metrics:**
- P50 latency: <500ms (non-streaming)
- P95 latency: <2s (non-streaming)
- Time to first token: <500ms (streaming)
- Throughput: >100 req/s per instance
