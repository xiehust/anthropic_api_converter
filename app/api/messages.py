"""
Messages API endpoints (Anthropic-compatible).

Implements POST /v1/messages for both streaming and non-streaming message creation.
"""
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.db.dynamodb import DynamoDBClient, UsageTracker
from app.middleware.auth import get_api_key_info
from app.schemas.anthropic import (
    CountTokensRequest,
    CountTokensResponse,
    ErrorResponse,
    MessageRequest,
    MessageResponse,
)
from app.services.bedrock_service import BedrockService

router = APIRouter()


# Dependency to get Bedrock service
def get_bedrock_service() -> BedrockService:
    """Get Bedrock service instance."""
    return BedrockService()


# Dependency to get usage tracker
def get_usage_tracker(request: Request) -> UsageTracker:
    """Get usage tracker instance."""
    dynamodb_client = request.app.state.dynamodb_client
    return UsageTracker(dynamodb_client)


@router.post(
    "/messages",
    response_model=MessageResponse,
    responses={
        200: {"description": "Successful response"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Create a message",
    description="Create a message using the Anthropic Messages API format. "
    "Supports both streaming and non-streaming responses.",
)
async def create_message(
    request_data: MessageRequest,
    request: Request,
    beta: Optional[str] = None,
    api_key_info: dict = Depends(get_api_key_info),
    bedrock_service: BedrockService = Depends(get_bedrock_service),
    usage_tracker: UsageTracker = Depends(get_usage_tracker),
):
    """
    Create a message (Anthropic-compatible endpoint).

    This endpoint accepts requests in Anthropic Messages API format and returns
    responses in the same format, while using AWS Bedrock as the backend.

    Supports:
    - Streaming and non-streaming responses
    - Tool use (function calling)
    - Extended thinking
    - Multiple content types (text, images, documents)
    - System messages
    - Stop sequences

    Args:
        request_data: Message request in Anthropic format
        request: FastAPI request object
        beta: Optional beta features flag (e.g., "true" for beta features)
        api_key_info: API key information from auth middleware
        bedrock_service: Bedrock service instance
        usage_tracker: Usage tracker instance

    Returns:
        MessageResponse for non-streaming, StreamingResponse for streaming

    Raises:
        HTTPException: For various error conditions
    """
    request_id = f"msg-{uuid4().hex}"

    print(f"\n{'='*80}")
    print(f"[REQUEST] ID: {request_id}")
    print(f"[REQUEST] Model: {request_data.model}")
    print(f"[REQUEST] Stream: {request_data.stream}")
    print(f"[REQUEST] Beta: {beta}")
    print(f"[REQUEST] API Key: {api_key_info.get('api_key', 'unknown')[:20]}...")
    print(f"{'='*80}\n")

    # Get service_tier from API key info (defaults to 'default' if not set)
    service_tier = api_key_info.get("service_tier", "default")
    print(f"[REQUEST] Service Tier: {service_tier}")

    try:
        # Check if streaming is requested
        if request_data.stream:
            # Return streaming response
            return StreamingResponse(
                _handle_streaming_request(
                    request_data,
                    request_id,
                    api_key_info,
                    bedrock_service,
                    usage_tracker,
                    service_tier,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Request-ID": request_id,
                },
            )
        else:
            # Handle non-streaming request (async to not block event loop)
            response = await bedrock_service.invoke_model(request_data, request_id, service_tier)

            # Record usage
            usage_tracker.record_usage(
                api_key=api_key_info.get("api_key"),
                request_id=request_id,
                model=request_data.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_tokens=response.usage.cache_read_input_tokens or 0,
                success=True,
            )

            return response

    except HTTPException as he:
        # Re-raise HTTP exceptions
        print(f"\n[ERROR] HTTPException in request {request_id}")
        print(f"[ERROR] Status: {he.status_code}")
        print(f"[ERROR] Detail: {he.detail}\n")
        raise

    except Exception as e:
        # Record failed usage
        print(f"\n[ERROR] Exception in request {request_id}")
        print(f"[ERROR] Type: {type(e).__name__}")
        print(f"[ERROR] Message: {str(e)}")
        import traceback
        print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")

        usage_tracker.record_usage(
            api_key=api_key_info.get("api_key"),
            request_id=request_id,
            model=request_data.model,
            input_tokens=0,
            output_tokens=0,
            success=False,
            error_message=str(e),
        )

        # Return error response
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": f"Failed to process request: {str(e)}",
            },
        )


async def _handle_streaming_request(
    request_data: MessageRequest,
    request_id: str,
    api_key_info: dict,
    bedrock_service: BedrockService,
    usage_tracker: UsageTracker,
    service_tier: str = "default",
):
    """
    Handle streaming request and yield SSE events.

    Args:
        request_data: Message request
        request_id: Request ID
        api_key_info: API key information
        bedrock_service: Bedrock service instance
        usage_tracker: Usage tracker instance
        service_tier: Bedrock service tier

    Yields:
        SSE-formatted event strings
    """
    accumulated_tokens = {"input": 0, "output": 0, "cached": 0}
    success = True
    error_message = None

    print(f"[STREAMING] Starting stream for request {request_id}")
    print(f"[STREAMING] Service tier: {service_tier}")

    try:
        # Stream events from Bedrock
        async for sse_event in bedrock_service.invoke_model_stream(
            request_data, request_id, service_tier
        ):
            # Parse event to track usage
            if "usage" in sse_event:
                # Extract usage information from events
                # This is a simplified approach; real implementation would parse JSON
                pass

            yield sse_event

        print(f"[STREAMING] Stream completed successfully for request {request_id}")

    except Exception as e:
        success = False
        error_message = str(e)

        print(f"\n[ERROR] Streaming error in request {request_id}")
        print(f"[ERROR] Type: {type(e).__name__}")
        print(f"[ERROR] Message: {str(e)}")
        import traceback
        print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")

        # Send error event
        error_event = (
            f"event: error\n"
            f"data: {{'type': 'error', 'error': {{'type': 'internal_error', 'message': '{str(e)}'}}}}\n\n"
        )
        yield error_event

    finally:
        # Record usage after stream completes
        usage_tracker.record_usage(
            api_key=api_key_info.get("api_key"),
            request_id=request_id,
            model=request_data.model,
            input_tokens=accumulated_tokens["input"],
            output_tokens=accumulated_tokens["output"],
            cached_tokens=accumulated_tokens["cached"],
            success=success,
            error_message=error_message,
        )


@router.get(
    "/messages/{message_id}",
    summary="Get message details (not implemented)",
    description="This endpoint is not implemented as Bedrock doesn't store message history.",
    deprecated=True,
)
async def get_message(message_id: str):
    """Get message details (not implemented)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "type": "not_implemented",
            "message": "Message retrieval is not supported. "
            "Bedrock does not store message history.",
        },
    )


@router.post(
    "/messages/count_tokens",
    response_model=CountTokensResponse,
    responses={
        200: {"description": "Token count calculated successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication error"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Count tokens in messages",
    description="Count the number of tokens in a set of messages without making an API call. "
    "This is useful for estimating costs and staying within model token limits.",
)
async def count_tokens(
    request_data: CountTokensRequest,
    api_key_info: dict = Depends(get_api_key_info),
    bedrock_service: BedrockService = Depends(get_bedrock_service),
):
    """
    Count tokens in messages (Anthropic-compatible endpoint).

    This endpoint estimates the number of input tokens that would be used
    for a given set of messages, system prompt, and tools without actually
    invoking the model.

    Args:
        request_data: Count tokens request with model, messages, system, and tools
        api_key_info: API key information from auth middleware
        bedrock_service: Bedrock service instance

    Returns:
        CountTokensResponse with input_tokens count

    Raises:
        HTTPException: For various error conditions
    """
    try:
        # Count tokens using the Bedrock service (async to not block event loop)
        token_count = await bedrock_service.count_tokens(request_data)

        return CountTokensResponse(input_tokens=token_count)

    except HTTPException:
        # Re-raise HTTP exceptions
        raise

    except Exception as e:
        # Return error response
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": f"Failed to count tokens: {str(e)}",
            },
        )
