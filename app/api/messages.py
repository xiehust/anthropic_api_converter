"""
Messages API endpoints (Anthropic-compatible).

Implements POST /v1/messages for both streaming and non-streaming message creation.
Supports Programmatic Tool Calling (PTC) via Docker sandbox execution.
"""
import json
import logging
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import settings
from app.core.exceptions import BedrockAPIError
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
from app.services.ptc_service import PTCService, get_ptc_service
from app.services.ptc import DockerNotAvailableError, SandboxError, ToolCallRequest, ExecutionResult
from app.services.standalone_code_execution_service import (
    StandaloneCodeExecutionService,
    get_standalone_service,
)

logger = logging.getLogger(__name__)


def _extract_ptc_tool_result(request: MessageRequest, container_id: Optional[str], ptc_service: PTCService) -> Optional[tuple]:
    """
    Check if request is a tool_result continuation for a pending PTC execution.

    Returns:
        For single tool result:
            Tuple of (session_id, tool_use_id, tool_result_content, is_error)
        For batch tool results:
            Tuple of (session_id, "batch", {call_id: result_content}, has_any_error)
        None if not a PTC continuation.
    """
    if not container_id:
        return None

    # Check if there's a pending execution for this container
    pending_state = ptc_service.get_pending_execution(container_id)
    if not pending_state:
        return None

    # Check if the last message contains a tool_result
    if not request.messages:
        return None

    last_message = request.messages[-1]
    if isinstance(last_message, dict):
        if last_message.get("role") != "user":
            return None
        content = last_message.get("content", [])
    elif hasattr(last_message, "role"):
        if last_message.role != "user":
            return None
        content = last_message.content if hasattr(last_message, "content") else []
    else:
        return None

    # Find tool_result(s) in content
    if isinstance(content, str):
        return None

    # Collect all tool_results
    tool_results = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                result_content = block.get("content", "")
                is_error = block.get("is_error", False)
                if tool_use_id:
                    tool_results.append((tool_use_id, result_content, is_error))
        elif hasattr(block, "type") and block.type == "tool_result":
            tool_use_id = block.tool_use_id if hasattr(block, "tool_use_id") else None
            result_content = block.content if hasattr(block, "content") else ""
            is_error = block.is_error if hasattr(block, "is_error") else False
            if tool_use_id:
                tool_results.append((tool_use_id, result_content, is_error))

    if not tool_results:
        return None

    # Check if this is a batch result (multiple tool_results or pending batch)
    is_batch = len(tool_results) > 1 or (
        pending_state.pending_batch_call_ids and len(pending_state.pending_batch_call_ids) > 1
    )

    if is_batch:
        # Build dict mapping call_id to result
        # The tool_use_id format is "toolu_<call_id[:12]>", extract call_id
        batch_results = {}
        has_any_error = False

        # Get the pending call IDs to map tool_use_id back to call_id
        pending_call_ids = pending_state.pending_batch_call_ids or []

        for tool_use_id, result_content, is_error in tool_results:
            # Extract the call_id portion from tool_use_id (format: toolu_<12chars>)
            id_suffix = tool_use_id.replace("toolu_", "")

            # Find matching call_id
            matched_call_id = None
            for call_id in pending_call_ids:
                if call_id.startswith(id_suffix) or call_id[:12] == id_suffix:
                    matched_call_id = call_id
                    break

            if matched_call_id:
                batch_results[matched_call_id] = result_content
            else:
                # Fallback: use tool_use_id as key
                batch_results[id_suffix] = result_content

            if is_error:
                has_any_error = True

        logger.info(f"[PTC] Found batch tool_results ({len(batch_results)} results)")
        return (container_id, "batch", batch_results, has_any_error)
    else:
        # Single tool result
        tool_use_id, result_content, is_error = tool_results[0]
        logger.info(f"[PTC] Found tool_result for tool_use_id={tool_use_id}, pending={pending_state.pending_tool_call_id}")
        return (container_id, tool_use_id, result_content, is_error)

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


# Dependency to get PTC service
def get_ptc_service_dep() -> PTCService:
    """Get PTC service instance."""
    return get_ptc_service()


# Dependency to get standalone code execution service
def get_standalone_service_dep() -> StandaloneCodeExecutionService:
    """Get standalone code execution service instance."""
    return get_standalone_service()


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
    anthropic_beta: Optional[str] = Header(None, alias="anthropic-beta"),
    api_key_info: dict = Depends(get_api_key_info),
    bedrock_service: BedrockService = Depends(get_bedrock_service),
    usage_tracker: UsageTracker = Depends(get_usage_tracker),
    ptc_service: PTCService = Depends(get_ptc_service_dep),
    standalone_service: StandaloneCodeExecutionService = Depends(get_standalone_service_dep),
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
    - Programmatic Tool Calling (PTC) via Docker sandbox

    Args:
        request_data: Message request in Anthropic format
        request: FastAPI request object
        anthropic_beta: Beta features header (e.g., "advanced-tool-use-2025-11-20")
        api_key_info: API key information from auth middleware
        bedrock_service: Bedrock service instance
        usage_tracker: Usage tracker instance
        ptc_service: PTC service instance

    Returns:
        MessageResponse for non-streaming, StreamingResponse for streaming

    Raises:
        HTTPException: For various error conditions
    """
    request_id = f"msg-{uuid4().hex}"

    # Get container ID from request body for session reuse (plain string)
    container_id = request_data.container

    logger.info(f"Request {request_id}: model={request_data.model}, stream={request_data.stream}, beta={anthropic_beta}")

    print(f"\n{'='*80}")
    print(f"[REQUEST] ID: {request_id}")
    print(f"[REQUEST] Model: {request_data.model}")
    print(f"[REQUEST] Stream: {request_data.stream}")
    print(f"[REQUEST] Beta: {anthropic_beta}")
    print(f"[REQUEST] API Key: {api_key_info.get('api_key', 'unknown')[:20]}...")
    print(f"{'='*80}\n")

    # Get service_tier from API key info (defaults to 'default' if not set)
    service_tier = api_key_info.get("service_tier", "default")
    print(f"[REQUEST] Service Tier: {service_tier}")

    # Check if this is a PTC request
    is_ptc = PTCService.is_ptc_request(request_data, anthropic_beta)
    if is_ptc:
        logger.info(f"Request {request_id}: Detected PTC request")
        print(f"[PTC] Detected Programmatic Tool Calling request")

    try:
        # Handle PTC requests
        if is_ptc:
            try:
                # Check if this is a tool_result continuation for a pending sandbox
                ptc_continuation = _extract_ptc_tool_result(request_data, container_id, ptc_service)

                if request_data.stream:
                    # Streaming PTC request
                    logger.info(f"Request {request_id}: Streaming PTC request")

                    if ptc_continuation:
                        # Resume sandbox execution with tool result(s) - streaming
                        session_id, tool_use_id, tool_result_content, is_error = ptc_continuation

                        is_batch = tool_use_id == "batch"
                        if is_batch:
                            logger.info(f"[PTC Streaming] Resuming sandbox with batch results ({len(tool_result_content)} tools)")
                        else:
                            logger.info(f"[PTC Streaming] Resuming sandbox execution for session {session_id}")

                        return StreamingResponse(
                            ptc_service.handle_tool_result_continuation_streaming(
                                session_id=session_id,
                                tool_result=tool_result_content,
                                is_error=is_error,
                                original_request=request_data,
                                bedrock_service=bedrock_service,
                                request_id=request_id,
                                service_tier=service_tier,
                            ),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Request-ID": request_id,
                                "X-Container-ID": container_id or "",
                            },
                        )
                    else:
                        # New PTC request - streaming
                        return StreamingResponse(
                            ptc_service.handle_ptc_request_streaming(
                                request=request_data,
                                bedrock_service=bedrock_service,
                                request_id=request_id,
                                service_tier=service_tier,
                                container_id=container_id,
                                anthropic_beta=anthropic_beta,
                            ),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Request-ID": request_id,
                            },
                        )

                # Non-streaming PTC request
                if ptc_continuation:
                    # Resume sandbox execution with tool result(s)
                    session_id, tool_use_id, tool_result_content, is_error = ptc_continuation

                    # Check if this is a batch result
                    is_batch = tool_use_id == "batch"
                    if is_batch:
                        logger.info(f"[PTC] Resuming sandbox with batch results ({len(tool_result_content)} tools)")
                    else:
                        logger.info(f"[PTC] Resuming sandbox execution for session {session_id}")

                    response, container_info = await ptc_service.handle_tool_result_continuation(
                        session_id=session_id,
                        tool_result=tool_result_content,  # dict for batch, value for single
                        is_error=is_error,
                        original_request=request_data,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                    )
                else:
                    # New PTC request - call Bedrock
                    response, container_info = await ptc_service.handle_ptc_request(
                        request=request_data,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        container_id=container_id,
                        anthropic_beta=anthropic_beta,
                    )

                # Record usage
                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                )

                # Add container info to response
                response_dict = response.model_dump()
                if container_info:
                    response_dict["container"] = container_info.model_dump()

                logger.debug(f"[PTC] Final response: {response_dict}")
                return JSONResponse(content=response_dict)

            except DockerNotAvailableError as e:
                logger.error(f"Request {request_id}: Docker not available for PTC: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "type": "api_error",
                        "message": str(e),
                    },
                )
            except SandboxError as e:
                logger.error(f"Request {request_id}: Sandbox error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "type": "api_error",
                        "message": f"Code execution error: {str(e)}",
                    },
                )

        # Check if this is a standalone code execution request
        # (code-execution-2025-08-25 header + code_execution tool without allowed_callers)
        # PTC has higher priority, so this only runs if is_ptc is False
        is_standalone = StandaloneCodeExecutionService.is_standalone_request(request_data, anthropic_beta)
        if is_standalone:
            logger.info(f"Request {request_id}: Detected standalone code execution request")
            print(f"[STANDALONE] Detected standalone code execution request")

            if request_data.stream:
                # Streaming standalone code execution
                logger.info(f"Request {request_id}: Streaming standalone code execution")

                # Create session first to get container ID for headers
                session = await standalone_service._get_or_create_session(container_id)

                return StreamingResponse(
                    standalone_service.handle_request_streaming(
                        request=request_data,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        container_id=session.session_id,
                        anthropic_beta=anthropic_beta,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Request-ID": request_id,
                        "X-Container-ID": session.session_id,
                        "X-Container-Expires-At": session.expires_at.isoformat(),
                    },
                )

            try:
                # Handle standalone code execution
                response, container_info = await standalone_service.handle_request(
                    request=request_data,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    container_id=container_id,
                    anthropic_beta=anthropic_beta,
                )

                # Record usage
                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                )

                # Add container info to response
                response_dict = response.model_dump()
                if container_info:
                    response_dict["container"] = container_info.model_dump()

                logger.debug(f"[STANDALONE] Final response: {response_dict}")
                return JSONResponse(content=response_dict)

            except DockerNotAvailableError as e:
                logger.error(f"Request {request_id}: Docker not available for standalone: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "type": "api_error",
                        "message": str(e),
                    },
                )
            except SandboxError as e:
                logger.error(f"Request {request_id}: Standalone sandbox error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "type": "api_error",
                        "message": f"Code execution error: {str(e)}",
                    },
                )

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
                    anthropic_beta,
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
            response = await bedrock_service.invoke_model(
                request_data, request_id, service_tier, anthropic_beta
            )

            # Record usage
            usage_tracker.record_usage(
                api_key=api_key_info.get("api_key"),
                request_id=request_id,
                model=request_data.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_tokens=response.usage.cache_read_input_tokens or 0,
                cache_write_input_tokens=response.usage.cache_creation_input_tokens or 0,
                success=True,
            )

            return response

    except HTTPException as he:
        # Re-raise HTTP exceptions
        print(f"\n[ERROR] HTTPException in request {request_id}")
        print(f"[ERROR] Status: {he.status_code}")
        print(f"[ERROR] Detail: {he.detail}\n")
        raise

    except BedrockAPIError as e:
        # Handle Bedrock API errors with proper HTTP status codes
        print(f"\n[ERROR] BedrockAPIError in request {request_id}")
        print(f"[ERROR] Code: {e.error_code}")
        print(f"[ERROR] Message: {e.error_message}")
        print(f"[ERROR] HTTP Status: {e.http_status}")
        print(f"[ERROR] Error Type: {e.error_type}\n")

        usage_tracker.record_usage(
            api_key=api_key_info.get("api_key"),
            request_id=request_id,
            model=request_data.model,
            input_tokens=0,
            output_tokens=0,
            success=False,
            error_message=f"[{e.error_code}] {e.error_message}",
        )

        # Return error response with correct HTTP status
        raise HTTPException(
            status_code=e.http_status,
            detail={
                "type": e.error_type,
                "message": e.error_message,
            },
        )

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
                "type": "api_error",
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
    anthropic_beta: Optional[str] = None,
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
        anthropic_beta: Optional beta header from Anthropic client (comma-separated)

    Yields:
        SSE-formatted event strings
    """
    accumulated_tokens = {"input": 0, "output": 0, "cached": 0, "cache_write": 0}
    success = True
    error_message = None

    print(f"[STREAMING] Starting stream for request {request_id}")
    print(f"[STREAMING] Service tier: {service_tier}")

    try:
        # Stream events from Bedrock (with beta header mapping)
        async for sse_event in bedrock_service.invoke_model_stream(
            request_data, request_id, service_tier, anthropic_beta
        ):
            # Parse event to track usage from message_delta and message_start events
            # SSE format: "event: <type>\ndata: <json>\n\n"
            if "data:" in sse_event:
                try:
                    # Extract JSON data from SSE event
                    data_line = [line for line in sse_event.split("\n") if line.startswith("data:")]
                    if data_line:
                        event_data = json.loads(data_line[0][5:].strip())
                        event_type = event_data.get("type")

                        # Extract usage from message_start (initial usage)
                        if event_type == "message_start" and "message" in event_data:
                            message = event_data["message"]
                            if "usage" in message:
                                usage = message["usage"]
                                accumulated_tokens["input"] = usage.get("input_tokens", 0)
                                # cache tokens may be present in message_start
                                accumulated_tokens["cached"] = usage.get("cache_read_input_tokens", 0)
                                accumulated_tokens["cache_write"] = usage.get("cache_creation_input_tokens", 0)

                        # Extract usage from message_delta (final usage)
                        elif event_type == "message_delta" and "usage" in event_data:
                            usage = event_data["usage"]
                            # message_delta typically has output_tokens
                            if "output_tokens" in usage:
                                accumulated_tokens["output"] = usage["output_tokens"]
                            if "input_tokens" in usage:
                                accumulated_tokens["input"] = usage["input_tokens"]
                            # Also check for cache tokens
                            if "cache_read_input_tokens" in usage:
                                accumulated_tokens["cached"] = usage["cache_read_input_tokens"]
                            if "cache_creation_input_tokens" in usage:
                                accumulated_tokens["cache_write"] = usage["cache_creation_input_tokens"]
                except (json.JSONDecodeError, IndexError, KeyError):
                    # Ignore parse errors - not all events have usage data
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
            cache_write_input_tokens=accumulated_tokens["cache_write"],
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
