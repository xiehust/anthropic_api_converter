"""
FastAPI application entry point.

Configures and initializes the Anthropic-Bedrock API proxy service.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health, messages, models
from app.core.config import settings
from app.db.dynamodb import DynamoDBClient
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware


# Initialize DynamoDB client globally (needed for middleware)
dynamodb_client = DynamoDBClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # Startup
    print(f"Starting {settings.app_name} v{settings.app_version}")
    print(f"Environment: {settings.environment}")
    print(f"AWS Region: {settings.aws_region}")

    # Store DynamoDB client in app state
    app.state.dynamodb_client = dynamodb_client

    # Create tables if they don't exist (optional, for development)
    if settings.environment == "development":
        try:
            print("Creating DynamoDB tables (if not exist)...")
            dynamodb_client.create_tables()
        except Exception as e:
            print(f"Warning: Failed to create DynamoDB tables: {e}")

    print("Application started successfully")

    yield

    # Shutdown
    print("Shutting down application...")
    # Cleanup resources if needed


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="""
    API proxy service that converts AWS Bedrock model inference API to
    Anthropic-compatible API format. This allows clients to access any model
    in Bedrock using the Anthropic Python SDK.

    ## Features

    - **Anthropic API Compatibility**: Full support for Anthropic Messages API format
    - **Streaming Support**: Server-Sent Events (SSE) for streaming responses
    - **Tool Use**: Function calling with tool definitions
    - **Extended Thinking**: Support for thinking blocks
    - **Multi-Modal**: Text, images, and document support
    - **Authentication**: API key-based authentication
    - **Rate Limiting**: Token bucket rate limiting per API key
    - **Usage Tracking**: Comprehensive usage analytics

    ## Authentication

    All requests require an API key provided in the `x-api-key` header:

    ```
    x-api-key: sk-your-api-key-here
    ```

    ## Supported Models

    The proxy supports all Bedrock models that implement the Converse API:
    - Claude 3.5 Sonnet
    - Claude 3 Opus
    - Claude 3 Sonnet
    - Claude 3 Haiku
    - And other Bedrock foundation models

    ## Rate Limits

    Rate limits are enforced per API key:
    - Default: 100 requests per 60 seconds
    - Custom limits can be configured per API key
    - Rate limit headers are included in responses
    """,
    docs_url=settings.docs_url,
    openapi_url=settings.openapi_url,
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# Add authentication middleware (must be added after rate limiting)
# Note: DynamoDB client is initialized globally above
app.add_middleware(AuthMiddleware, dynamodb_client=dynamodb_client)


# Include routers
app.include_router(
    health.router,
    tags=["health"],
)

app.include_router(
    messages.router,
    prefix=settings.api_prefix,
    tags=["messages"],
)

app.include_router(
    models.router,
    prefix=settings.api_prefix,
    tags=["models"],
)


# Custom HTTPException handler to return proper JSON format
from fastapi import HTTPException

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Handle HTTPException with proper JSON response."""
    # If detail is already a dict (like from our middleware), use it directly
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "error",
                "error": exc.detail,
            },
        )

    # Otherwise, wrap it in standard format
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "error",
            "error": {
                "type": "api_error",
                "message": str(exc.detail),
            },
        },
    )


# Root endpoint
@app.get(
    "/",
    summary="API information",
    description="Get API information and available endpoints.",
)
async def root():
    """Root endpoint with API information."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "description": "Anthropic-Bedrock API Proxy",
        "documentation": f"{settings.docs_url}",
        "endpoints": {
            "messages": f"{settings.api_prefix}/messages",
            "models": f"{settings.api_prefix}/models",
            "health": "/health",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers,
        log_level=settings.log_level.lower(),
    )
