# Integration Tests for Anthropic-Bedrock API Proxy

This directory contains tests to validate the proxy service functionality.

## Test Files

- `test_proxy_integration.py` - Integration tests using the Anthropic SDK to test the proxy service

## Running the Tests

### Run all integration tests:
```bash
uv run pytest tests/test_proxy_integration.py -v
```

### Run with detailed output:
```bash
uv run pytest tests/test_proxy_integration.py -v -s
```

### Run specific test:
```bash
uv run pytest tests/test_proxy_integration.py::test_simple_message_non_streaming -v -s
```

### Run directly (quick test):
```bash
uv run python tests/test_proxy_integration.py
```

## Prerequisites

1. **Proxy service must be running**: 
   ```bash
   uv run uvicorn app.main:app --reload --port 8000
   ```

2. **Valid AWS credentials** configured with access to Bedrock

3. **Model access** enabled in your AWS account for Claude models

## Test Coverage

The integration tests cover:

- ✓ Health check endpoint
- ✓ Non-streaming message requests
- ✓ Streaming message requests
- ✓ System prompts
- ✓ Authentication (valid/invalid/missing API keys)
- ✓ Rate limiting headers
- ✓ Model listing

## Expected Behavior

- Tests that require Bedrock API access will be **skipped** if:
  - AWS credentials are not configured
  - Bedrock service is not accessible
  - Required models are not available

- Authentication tests will **always run** as they only test the proxy layer

## Configuration

The tests use the following configuration (from `test_proxy_integration.py`):

```python
PROXY_BASE_URL = "http://localhost:8000"
API_KEY = "sk-a22b7892b0ec47eb9a87a6ece52a9bb6"
```

To test with different configuration, modify these values in the test file.
