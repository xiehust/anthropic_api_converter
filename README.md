# Anthropic-Bedrock API Proxy

A production-ready FastAPI service that converts AWS Bedrock model inference API to Anthropic-compatible API format, enabling seamless use of Bedrock models with the Anthropic Python SDK.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Documentation](#api-documentation)
- [Deployment](#deployment)
- [Security](#security)
- [Monitoring](#monitoring)
- [Development](#development)
- [Testing](#testing)
- [Contributing](#contributing)
- [License](#license)

## Features

### Core Functionality
- **Anthropic API Compatibility**: Full support for Anthropic Messages API format
- **Bidirectional Format Conversion**: Seamless conversion between Anthropic and Bedrock formats
- **Streaming Support**: Server-Sent Events (SSE) for real-time streaming responses
- **Non-Streaming Support**: Traditional request-response pattern

### Advanced Features
- **Tool Use (Function Calling)**: Convert and execute tool definitions
- **Extended Thinking**: Support for thinking blocks in responses
- **Multi-Modal Content**: Text, images, and document support
- **System Messages**: Custom system prompts and instructions
- **Stop Sequences**: Custom stop conditions
- **Prompt Caching**: Map cache control hints (where supported)

### Infrastructure
- **Authentication**: API key-based authentication with DynamoDB storage
- **Rate Limiting**: Token bucket algorithm per API key
- **Usage Tracking**: Comprehensive analytics and token usage tracking
- **Caching**: Optional response caching with TTL
- **Logging**: Structured logging with correlation IDs
- **Metrics**: Prometheus-compatible metrics export
- **Health Checks**: Kubernetes/ECS-ready health endpoints

### Supported Models
- Claude 3.5 Sonnet (v1 & v2)
- Claude 3 Opus
- Claude 3 Sonnet
- Claude 3 Haiku
- Claude 2.1 / 2.0
- Claude Instant 1.2
- Any other Bedrock models supporting Converse API

## Architecture

```
+----------------------------------------------------------+
|              Client Application                          |
|           (Anthropic Python SDK)                         |
+---------------------------+------------------------------+
                            |
                            | HTTP/HTTPS (Anthropic Format)
                            |
                            v
+----------------------------------------------------------+
|          FastAPI API Proxy Service                       |
|                                                           |
|  +----------+  +-----------+  +----------------+         |
|  |   Auth   |  |   Rate    |  |   Format       |         |
|  |Middleware|->| Limiting  |->|  Conversion    |         |
|  +----------+  +-----------+  +----------------+         |
+-------+---------------+---------------+------------------+
        |               |               |
        v               v               v
  +----------+    +----------+    +----------+
  | DynamoDB |    |   AWS    |    |CloudWatch|
  |          |    | Bedrock  |    |   Logs/  |
  | API Keys |    | Runtime  |    | Metrics  |
  |  Usage   |    | Converse |    |          |
  |  Cache   |    |          |    |          |
  +----------+    +----------+    +----------+
```

### Component Overview

- **FastAPI Application**: Async web framework with automatic OpenAPI docs
- **Format Converters**: Bidirectional conversion between Anthropic and Bedrock formats
- **Authentication Middleware**: API key validation using DynamoDB
- **Rate Limiting Middleware**: Token bucket algorithm with configurable limits
- **Bedrock Service**: Interface to AWS Bedrock Converse/ConverseStream APIs
- **DynamoDB Storage**: API keys, usage tracking, caching, model mappings
- **Metrics Collection**: Prometheus-compatible metrics for monitoring

## Quick Start

### Prerequisites

- Python 3.12+
- AWS Account with Bedrock access
- AWS credentials configured
- DynamoDB access (or local DynamoDB for development)

### Installation

1. **Clone the repository**:
```bash
git clone <repository-url>
cd anthropic_api_proxy
```

2. **Install dependencies using uv**:
```bash
# Install uv if not already installed
pip install uv

# Install dependencies
uv sync
```

3. **Configure environment**:
```bash
cp .env.example .env
# Edit .env with your configuration
```

4. **Set up DynamoDB tables**:
```bash
uv run scripts/setup_tables.py
```

5. **Create an API key**:
```bash
uv run scripts/create_api_key.py --user-id dev-user --name "Development Key"
```

6. **Run the service**:
```bash
uv run uvicorn app.main:app --reload  --port 8000
```

The service will be available at `http://localhost:8000`.

### Using Docker Compose

For a complete local development environment with DynamoDB Local:

```bash
docker-compose up -d
```

This starts:
- API Proxy Service (port 8000)
- DynamoDB Local (port 8001)
- DynamoDB Admin UI (port 8002)
- Prometheus (port 9090)
- Grafana (port 3000)

## Configuration

### Environment Variables

Configuration is managed through environment variables. See `.env.example` for all options.

#### Application Settings
```bash
APP_NAME=Anthropic-Bedrock API Proxy
ENVIRONMENT=development  # development, staging, production
LOG_LEVEL=INFO
```

#### AWS Settings
```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

#### Authentication
```bash
REQUIRE_API_KEY=True
MASTER_API_KEY=sk-your-master-key
API_KEY_HEADER=x-api-key
```

#### Rate Limiting
```bash
RATE_LIMIT_ENABLED=True
RATE_LIMIT_REQUESTS=100  # requests per window
RATE_LIMIT_WINDOW=60     # window in seconds
```

#### Feature Flags
```bash
ENABLE_TOOL_USE=True
ENABLE_EXTENDED_THINKING=True
ENABLE_DOCUMENT_SUPPORT=True
PROMPT_CACHING_ENABLED=False
```

## API Documentation

### Endpoints

#### POST /v1/messages

Create a message (Anthropic-compatible).

**Request Body**:
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "max_tokens": 1024,
  "messages": [
    {
      "role": "user",
      "content": "Hello, Claude!"
    }
  ],
  "stream": false
}
```

**Response** (Non-streaming):
```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "Hello! How can I help you today?"
    }
  ],
  "model": "claude-3-5-sonnet-20241022",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 10,
    "output_tokens": 20
  }
}
```

**Response** (Streaming):
Server-Sent Events format:
```
event: message_start
data: {"type":"message_start","message":{...}}

event: content_block_start
data: {"type":"content_block_start","index":0,...}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},...}

event: message_stop
data: {"type":"message_stop"}
```

#### GET /v1/models

List available Bedrock models.

**Response**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
      "name": "Claude 3.5 Sonnet",
      "provider": "Anthropic",
      "input_modalities": ["TEXT", "IMAGE"],
      "output_modalities": ["TEXT"],
      "streaming_supported": true
    }
  ]
}
```

#### GET /health

Health check endpoint.

**Response**:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z",
  "uptime_seconds": 3600,
  "version": "1.0.0",
  "services": {
    "bedrock": {"status": "available"},
    "dynamodb": {"status": "available"}
  }
}
```

### Using with Anthropic SDK

```python
from anthropic import Anthropic

# Initialize client with custom base URL
client = Anthropic(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000/v1"
)

# Use as normal
message = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Hello, Claude!"}
    ]
)

print(message.content[0].text)
```

### Streaming Example

```python
with client.messages.stream(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Tell me a story"}
    ]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### Tool Use Example

```python
message = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    tools=[
        {
            "name": "get_weather",
            "description": "Get weather for a location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }
    ],
    messages=[
        {"role": "user", "content": "What's the weather in SF?"}
    ]
)
```

## Deployment

### Docker Deployment

Build and run with Docker:

```bash
# Build image
docker build -t anthropic-bedrock-proxy:latest .

# Run container
docker run -d \
  -p 8000:8000 \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  -e MASTER_API_KEY=your-master-key \
  --name api-proxy \
  anthropic-bedrock-proxy:latest
```

### AWS ECS Deployment

1. **Create ECR repository**:
```bash
aws ecr create-repository --repository-name anthropic-bedrock-proxy
```

2. **Build and push image**:
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
docker tag anthropic-bedrock-proxy:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/anthropic-bedrock-proxy:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/anthropic-bedrock-proxy:latest
```

3. **Create ECS task definition** with:
   - Container image from ECR
   - IAM role with Bedrock and DynamoDB permissions
   - Environment variables from SSM Parameter Store
   - Health check configured to `/health`

4. **Create ECS service** with:
   - Application Load Balancer
   - Auto-scaling based on CPU/Memory
   - Multiple availability zones

### AWS Lambda Deployment

Use AWS Lambda Web Adapter for serverless deployment:

```dockerfile
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.1 as lambda-adapter
FROM anthropic-bedrock-proxy:latest

COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=response_stream
```

### Kubernetes Deployment

Example deployment manifest:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: anthropic-bedrock-proxy
spec:
  replicas: 3
  selector:
    matchLabels:
      app: anthropic-bedrock-proxy
  template:
    metadata:
      labels:
        app: anthropic-bedrock-proxy
    spec:
      containers:
      - name: api-proxy
        image: anthropic-bedrock-proxy:latest
        ports:
        - containerPort: 8000
        env:
        - name: AWS_REGION
          value: "us-east-1"
        - name: MASTER_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-secrets
              key: master-api-key
        livenessProbe:
          httpGet:
            path: /liveness
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
```

## Security

### Best Practices

1. **API Key Management**:
   - Never commit API keys to version control
   - Use environment variables or secret managers
   - Rotate keys regularly
   - Use separate keys for different environments

2. **AWS Credentials**:
   - Use IAM roles when running on AWS (ECS, Lambda)
   - Apply least privilege principle
   - Enable CloudTrail logging

3. **Network Security**:
   - Use HTTPS in production
   - Configure CORS appropriately
   - Use VPC endpoints for AWS services
   - Implement WAF rules

4. **Rate Limiting**:
   - Configure appropriate limits per API key
   - Monitor for abuse patterns
   - Implement exponential backoff

### Required IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:DeleteItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:*:*:table/anthropic-proxy-*"
      ]
    }
  ]
}
```

## Monitoring

### Metrics

The service exposes Prometheus metrics at `/metrics`:

- **Request metrics**: Total requests, duration, status codes
- **Bedrock metrics**: API calls, latency, errors
- **Token usage**: Input/output/cached tokens per model
- **Rate limiting**: Rejected requests per API key
- **Authentication**: Failed auth attempts

### Logging

Structured logs include:
- Request ID for correlation
- API key (masked)
- Model used
- Token usage
- Latency
- Errors with stack traces

### Alerts

Recommended alerts:
- High error rate (>5%)
- Slow response time (p95 > 10s)
- Rate limit exceeded frequency
- Authentication failures spike
- AWS service errors

## Development

### Project Structure

```
anthropic_api_proxy/
   app/
      api/              # API route handlers
         health.py     # Health check endpoints
         messages.py   # Messages API
         models.py     # Models API
      converters/       # Format converters
         anthropic_to_bedrock.py
         bedrock_to_anthropic.py
      core/             # Core functionality
         config.py     # Configuration management
         logging.py    # Logging setup
         metrics.py    # Metrics collection
      db/               # Database clients
         dynamodb.py   # DynamoDB operations
      middleware/       # Middleware components
         auth.py       # Authentication
         rate_limit.py # Rate limiting
      schemas/          # Pydantic models
         anthropic.py  # Anthropic API schemas
         bedrock.py    # Bedrock API schemas
      services/         # Business logic
         bedrock_service.py
      main.py           # Application entry point
   tests/
      unit/             # Unit tests
      integration/      # Integration tests
   scripts/              # Utility scripts
   config/               # Configuration files
   Dockerfile            # Docker image definition
   docker-compose.yml    # Local development stack
   pyproject.toml        # Project dependencies
   README.md             # This file
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/unit/test_converters.py

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

## Testing

### Manual Testing

```bash
# Health check
curl http://localhost:8000/health

# List models
curl http://localhost:8000/v1/models \
  -H "x-api-key: sk-your-api-key"

# Create message
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-your-api-key" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'

# Streaming message
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-your-api-key" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "stream": true,
    "messages": [
      {"role": "user", "content": "Count to 10"}
    ]
  }'
```

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

[Your License Here]

## Support

For issues and questions:
- GitHub Issues: [repository-url]/issues
- Documentation: [docs-url]
- Email: [support-email]

## Acknowledgments

Built with:
- [FastAPI](https://fastapi.tiangolo.com/)
- [AWS Bedrock](https://aws.amazon.com/bedrock/)
- [Anthropic API](https://docs.anthropic.com/)
