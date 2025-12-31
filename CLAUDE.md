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

## AWS Deployment (CDK)

The service can be deployed to AWS ECS using CDK. Two launch types are supported:

### Launch Type Comparison

| Feature | Fargate | EC2 |
|---------|---------|-----|
| **PTC Support** | No | Yes |
| **Management** | Serverless (zero) | Some (ASG, AMI) |
| **Cost Model** | Pay per use | Instance-based |
| **Scaling** | Fast (seconds) | Slower (minutes) |
| **Docker Access** | No | Yes (socket mount) |
| **Best For** | Standard API proxy | PTC-enabled deployments |

### Fargate Deployment (Default)

```bash
cd cdk

# Deploy to dev environment (ARM64, Fargate)
./scripts/deploy.sh -e dev -p arm64

# Deploy to prod environment (AMD64, Fargate)
./scripts/deploy.sh -e prod -p amd64 -r us-east-1
```

### EC2 Deployment (For PTC Support)

```bash
cd cdk

# Deploy to dev with EC2 (enables PTC, uses Spot instances)
./scripts/deploy.sh -e dev -p arm64 -l ec2

# Deploy to prod with EC2 (On-Demand instances for stability)
./scripts/deploy.sh -e prod -p arm64 -l ec2
```

### Key CDK Files

- `cdk/config/config.ts` - Environment configurations (VPC, ECS, EC2, PTC settings)
- `cdk/lib/ecs-stack.ts` - ECS infrastructure (supports both Fargate and EC2)
- `cdk/scripts/deploy.sh` - Deployment script with options

### CDK Environment Variables

The deploy script sets these environment variables:
- `CDK_PLATFORM` - Architecture: `arm64` or `amd64`
- `CDK_LAUNCH_TYPE` - Launch type: `fargate` or `ec2`
- `CDK_ENVIRONMENT` - Environment name: `dev` or `prod`

### EC2 Instance Types

Platform-specific instance types are automatically selected:
- **ARM64**: t4g.medium (dev), t4g.large (prod) - Graviton processors
- **AMD64**: t3.medium (dev), t3.large (prod) - Intel/AMD processors

Dev environments use Spot instances for cost savings; prod uses On-Demand.

## Architecture

### Dual API Mode

The service supports two Bedrock API modes depending on the model:

1. **InvokeModel API** (for Claude models):
   - Uses `invoke_model` / `invoke_model_with_response_stream`
   - Native Anthropic request/response format (minimal conversion)
   - Supports all Claude beta features (tool-examples, tool-search, etc.)
   - Better feature compatibility with Anthropic API

2. **Converse API** (for non-Claude models):
   - Uses `converse` / `converse_stream`
   - Requires format conversion (Anthropic ↔ Bedrock Converse format)
   - Unified API for all Bedrock models
   - Some beta features may not be available

**Routing Logic:** Model ID is checked - if it contains "anthropic" or "claude", InvokeModel API is used.

### Critical Conversion Flow

The core of this service is the bidirectional conversion between Anthropic and Bedrock formats:

**Request Flow (Converse API - non-Claude models):**
1. `app/api/messages.py` - Receives Anthropic-formatted request
2. `app/middleware/auth.py` - Validates API key from DynamoDB
3. `app/middleware/rate_limit.py` - Enforces token bucket rate limiting
4. `app/converters/anthropic_to_bedrock.py` - **Converts request to Bedrock format**
5. `app/services/bedrock_service.py` - Calls AWS Bedrock Converse API
6. `app/converters/bedrock_to_anthropic.py` - **Converts response back to Anthropic format**
7. Response returned to client

**Request Flow (InvokeModel API - Claude models):**
1. `app/api/messages.py` - Receives Anthropic-formatted request
2. `app/middleware/auth.py` - Validates API key from DynamoDB
3. `app/middleware/rate_limit.py` - Enforces token bucket rate limiting
4. `app/services/bedrock_service.py` - Converts to native Anthropic format with Bedrock versioning
5. `app/services/bedrock_service.py` - Calls AWS Bedrock InvokeModel API
6. Response is already in Anthropic format (minimal conversion to MessageResponse)
7. Response returned to client

**Streaming Flow:** Same as above, using streaming API variants.

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
- **For AWS ECS deployment**: Must use EC2 launch type (not Fargate)
  - Fargate doesn't provide Docker daemon access
  - EC2 launch type mounts Docker socket (`/var/run/docker.sock`)
  - Deploy with: `./scripts/deploy.sh -e dev -p arm64 -l ec2`
- **For multi-instance deployments**: ALB sticky sessions must be enabled
  - PTC sessions are stored in-memory on individual instances
  - Continuation requests (with `container.id`) must route to the same instance
  - CDK automatically enables sticky sessions with 300s duration
  - Without sticky sessions, continuation requests may go to wrong instance and create new sessions

**Limitations:**
- Non-streaming only (streaming PTC not yet implemented)
- Network disabled in sandbox by default
- Tools are executed client-side (not in the sandbox)
- **ECS Fargate not supported** (no Docker daemon access)
- **Multi-instance deployments require sticky sessions** (see Requirements above)

### Beta Header Mapping and Tool Input Examples

The proxy supports mapping Anthropic beta headers to Bedrock-specific beta headers, and the `input_examples` tool parameter for enhanced tool use.

**Beta Header Mapping:**

When Anthropic clients send beta headers (e.g., `anthropic-beta: advanced-tool-use-2025-11-20`), the proxy maps them to corresponding Bedrock beta headers for supported models.

**Configuration (`app/core/config.py`):**
```python
# Beta header mapping (Anthropic → Bedrock)
beta_header_mapping: Dict[str, List[str]] = {
    "advanced-tool-use-2025-11-20": [
        "tool-examples-2025-10-29",
        "tool-search-tool-2025-10-19",
    ],
}

# Models that support beta header mapping
beta_header_supported_models: List[str] = [
    "claude-opus-4-5-20251101",
    "global.anthropic.claude-opus-4-5-20251101-v1:0",
]
```

**How it works:**
1. Client sends request with `anthropic-beta: advanced-tool-use-2025-11-20` header
2. Proxy checks if the model supports beta header mapping
3. If supported, maps to Bedrock beta headers: `tool-examples-2025-10-29`, `tool-search-tool-2025-10-19`
4. Mapped headers are added to `additionalModelRequestFields.anthropic_beta`

**Tool Input Examples:**

The `input_examples` parameter allows providing example inputs to help Claude understand how to use a tool:

```python
tools = [
    {
        "name": "get_weather",
        "description": "Get the current weather in a given location",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["location"]
        },
        "input_examples": [
            {"location": "San Francisco, CA", "unit": "fahrenheit"},
            {"location": "Tokyo, Japan", "unit": "celsius"},
            {"location": "New York, NY"}  # 'unit' is optional
        ]
    }
]
```

**Key Files:**
- `app/core/config.py` - `beta_header_mapping` and `beta_header_supported_models` settings
- `app/schemas/anthropic.py` - `Tool.input_examples` field
- `app/converters/anthropic_to_bedrock.py` - `_map_beta_headers()`, `_supports_beta_header_mapping()`, `_get_tools_with_examples()`

**Implementation Note:**
Bedrock's standard `toolSpec` doesn't support `inputExamples`. When the `tool-examples-2025-10-29` beta is enabled, tools with `input_examples` are passed via `additionalModelRequestFields.tools` in Anthropic format instead of the standard `toolConfig`.

**Extending Support:**
- To add more beta header mappings, update `BETA_HEADER_MAPPING` in config
- To enable for more models, add model IDs to `BETA_HEADER_SUPPORTED_MODELS`

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

### Docker-in-Docker (DinD) Bind Mount Issue

**Problem**: When the proxy runs inside a Docker container (e.g., ECS EC2 with Docker socket mount), PTC sandbox containers fail with "Container failed to become ready" because bind mounts don't work correctly.

**Root Cause**: Docker bind mounts resolve paths from the **Docker daemon's perspective** (the host), not from inside the container making the API call.

```
┌─────────────────────────────────────────────────────────────────┐
│ EC2 Host                                                        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Proxy Container (ECS Task)                               │   │
│  │                                                          │   │
│  │  tempfile.mkdtemp() creates /tmp/ptc_sandbox_xxx         │   │
│  │  File written: /tmp/ptc_sandbox_xxx/runner.py  ← EXISTS  │   │
│  │                                                          │   │
│  │  Docker API call: volumes={"/tmp/ptc_sandbox_xxx": ...}  │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  Docker daemon receives path "/tmp/ptc_sandbox_xxx"             │
│  Looks for it on HOST filesystem ← DOESN'T EXIST (or empty)    │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Sandbox Container                                        │   │
│  │  /sandbox/ is EMPTY - runner.py not found!               │   │
│  │  python /sandbox/runner.py → fails immediately           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Solution**: Use Docker's `put_archive` API to copy files directly into the container instead of bind mounts. This works regardless of where the proxy runs.

**How to verify the issue** (via SSM on ECS EC2 instance):
```bash
# Check if temp dirs exist on host vs inside proxy container
echo "=== Host /tmp ===" && ls -la /tmp/ | grep ptc
echo "=== Inside proxy container /tmp ===" && docker exec <proxy_container_id> ls -la /tmp/ | grep ptc

# Test bind mount behavior
docker exec <proxy_container_id> sh -c "mkdir -p /tmp/test && echo content > /tmp/test/file.txt"
docker run --rm -v /tmp/test:/test python:3.11-slim cat /test/file.txt  # Will fail - empty!
```

**Key code changes** (`app/services/ptc/sandbox.py`):
- Removed bind mount volumes from container config
- Added `_copy_file_to_container()` method using `put_archive`
- Changed runner path from `/sandbox/runner.py` to `/tmp/runner.py`
- Removed `read_only=True` (incompatible with `put_archive`)

**Security maintained via**:
- `network_disabled=True` - No network access
- `security_opt=["no-new-privileges"]` - Prevent privilege escalation
- `cap_drop=["ALL"]` - Drop all Linux capabilities
- Memory and CPU limits

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

**Beta Header Mapping Settings:**
- `BETA_HEADER_MAPPING` - Dict mapping Anthropic beta headers to Bedrock beta headers (default: `advanced-tool-use-2025-11-20` → `['tool-examples-2025-10-29', 'tool-search-tool-2025-10-19']`)
- `BETA_HEADER_SUPPORTED_MODELS` - List of model IDs that support beta header mapping (default: Claude Opus 4.5)

See `.env.example` for full list.

## API Compatibility Notes

This service aims for **100% compatibility** with the Anthropic Messages API. Key differences:

1. **Model IDs**: Must use Anthropic-style IDs (e.g., `claude-3-5-sonnet-20241022`), which are mapped to Bedrock ARNs internally. You can also pass Bedrock ARNs directly.

2. **Thinking Blocks**: Bedrock may not support extended thinking natively. The service converts thinking blocks to text annotations where needed.

3. **Prompt Caching**: Anthropic's `cache_control` parameter is parsed but may not be fully supported by Bedrock. The service gracefully handles this.

4. **Rate Limiting**: This service adds rate limiting (not in base Anthropic API). Clients see `429` errors and `Retry-After` headers.

5. **Authentication**: Uses API keys in `x-api-key` header (consistent with Anthropic API).

6. **Programmatic Tool Calling**: Fully supported via Docker sandbox. Requires `anthropic-beta: advanced-tool-use-2025-11-20` header and Docker running on the server. Tools are executed client-side (returned to caller for execution).

7. **Beta Header Mapping**: Anthropic beta headers (e.g., `advanced-tool-use-2025-11-20`) are mapped to corresponding Bedrock beta headers for supported models (currently Claude Opus 4.5). Unsupported models ignore unmapped beta headers.

8. **Tool Input Examples**: The `input_examples` parameter in tool definitions is supported and passed to Bedrock as `inputExamples`. Requires beta header `advanced-tool-use-2025-11-20` for the feature to be enabled.

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
