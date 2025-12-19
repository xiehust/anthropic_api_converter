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
python scripts/setup_tables.py

# Create an API key for testing
python scripts/create_api_key.py --user-id dev-user --name "Development Key"
```

### Running the Service

```bash
# Development mode (with auto-reload)
uvicorn app.main:app --reload

# Production mode
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Using Docker Compose (includes DynamoDB Local, Prometheus, Grafana)
docker-compose up -d
```

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/unit/test_converters.py

# Run integration tests only
pytest -m integration

# Run with verbose output
pytest -v
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
    anthropic_model_id="claude-3-opus-20240229",
    bedrock_model_id="anthropic.claude-3-opus-20240229-v1:0"
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

See `.env.example` for full list.

## API Compatibility Notes

This service aims for **100% compatibility** with the Anthropic Messages API. Key differences:

1. **Model IDs**: Must use Anthropic-style IDs (e.g., `claude-3-5-sonnet-20241022`), which are mapped to Bedrock ARNs internally. You can also pass Bedrock ARNs directly.

2. **Thinking Blocks**: Bedrock may not support extended thinking natively. The service converts thinking blocks to text annotations where needed.

3. **Prompt Caching**: Anthropic's `cache_control` parameter is parsed but may not be fully supported by Bedrock. The service gracefully handles this.

4. **Rate Limiting**: This service adds rate limiting (not in base Anthropic API). Clients see `429` errors and `Retry-After` headers.

5. **Authentication**: Uses API keys in `x-api-key` header (consistent with Anthropic API).

## Key Files to Understand

1. **`app/converters/anthropic_to_bedrock.py`** - Request conversion (Anthropic → Bedrock)
2. **`app/converters/bedrock_to_anthropic.py`** - Response conversion (Bedrock → Anthropic)
3. **`app/services/bedrock_service.py`** - Orchestrates Bedrock API calls
4. **`app/api/messages.py`** - Main API endpoint handler
5. **`app/core/config.py`** - Configuration and settings
6. **`ARCHITECTURE.md`** - Detailed architecture documentation

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
