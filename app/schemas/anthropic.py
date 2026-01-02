"""
Pydantic models for Anthropic Messages API format.

These models represent the request and response structures for the Anthropic API,
enabling validation, serialization, and documentation.
"""
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


# Content Block Types
class TextContent(BaseModel):
    """Text content block."""
    type: Literal["text"] = "text"
    text: str
    cache_control: Optional["CacheControl"] = None


class ImageSource(BaseModel):
    """Image source data."""
    type: Literal["base64"] = "base64"
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
    data: str  # base64 encoded


class ImageContent(BaseModel):
    """Image content block."""
    type: Literal["image"] = "image"
    source: ImageSource
    cache_control: Optional["CacheControl"] = None


class DocumentSource(BaseModel):
    """Document source data."""
    type: Literal["base64"] = "base64"
    media_type: Literal["application/pdf"]
    data: str  # base64 encoded


class DocumentContent(BaseModel):
    """Document content block (PDF support)."""
    type: Literal["document"] = "document"
    source: DocumentSource
    cache_control: Optional["CacheControl"] = None


class ThinkingContent(BaseModel):
    """Extended thinking content block."""
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: Optional[str] = None


class RedactedThinkingContent(BaseModel):
    """Redacted thinking content block (when thinking is hidden for safety/policy reasons)."""
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str  # Base64 encoded redacted data


class CallerInfo(BaseModel):
    """Information about who invoked a tool (for PTC)."""
    type: Literal["direct", "code_execution_20250825"]
    tool_id: Optional[str] = None


class ToolUseContent(BaseModel):
    """Tool use content block in assistant messages."""
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]
    caller: Optional[CallerInfo] = None  # PTC: who called the tool


class ServerToolUseContent(BaseModel):
    """Server tool use content block (e.g., code_execution for PTC)."""
    type: Literal["server_tool_use"] = "server_tool_use"
    id: str
    name: str
    input: Dict[str, Any]


class CodeExecutionResultContent(BaseModel):
    """Content block for code execution result."""
    type: Literal["code_execution_result"] = "code_execution_result"
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


class ServerToolResultContent(BaseModel):
    """Server tool result content block (result of server_tool_use like code_execution)."""
    type: Literal["server_tool_result"] = "server_tool_result"
    tool_use_id: str
    content: List[CodeExecutionResultContent]


class ToolResultContent(BaseModel):
    """Tool result content block in user messages."""
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Union[str, List[Union[TextContent, ImageContent]]]
    is_error: Optional[bool] = None
    cache_control: Optional["CacheControl"] = None


# Union of all content types
ContentBlock = Union[
    TextContent,
    ImageContent,
    DocumentContent,
    ThinkingContent,
    RedactedThinkingContent,
    ToolUseContent,
    ToolResultContent,
    ServerToolUseContent,  # PTC server tool use
    ServerToolResultContent,  # PTC server tool result
]


# Cache Control
class CacheControl(BaseModel):
    """Cache control for prompt caching."""
    type: Literal["ephemeral"] = "ephemeral"


# Message Structure
class Message(BaseModel):
    """Message in the conversation."""
    role: Literal["user", "assistant"]
    content: Union[str, List[ContentBlock]]

    @field_validator("content", mode="before")
    @classmethod
    def convert_string_to_list(cls, v):
        """Convert string content to list of TextContent blocks."""
        if isinstance(v, str):
            return [{"type": "text", "text": v}]
        return v


# Tool Definition
class ToolInputSchema(BaseModel):
    """JSON schema for tool input."""
    type: Literal["object"] = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: Optional[List[str]] = None


class Tool(BaseModel):
    """Tool definition for function calling."""
    name: str
    description: str
    input_schema: ToolInputSchema
    cache_control: Optional[CacheControl] = None
    # Input examples (beta feature: tool-examples-2025-10-29)
    # Array of example input objects to help Claude understand how to use the tool
    input_examples: Optional[List[Dict[str, Any]]] = None
    # PTC-specific fields
    type: Optional[str] = None  # e.g., "code_execution_20250825" for PTC
    allowed_callers: Optional[List[Literal["direct", "code_execution_20250825"]]] = None


class CodeExecutionTool(BaseModel):
    """Code execution tool for Programmatic Tool Calling."""
    type: Literal["code_execution_20250825"] = "code_execution_20250825"
    name: Literal["code_execution"] = "code_execution"


# System Message with Cache Control
class SystemMessage(BaseModel):
    """System message with optional cache control."""
    type: Literal["text"] = "text"
    text: str
    cache_control: Optional[CacheControl] = None


# Metadata
class Metadata(BaseModel):
    """Request metadata."""
    user_id: Optional[str] = None


# Request Models
class MessageRequest(BaseModel):
    """Anthropic Messages API request."""
    model: str
    messages: List[Message]
    max_tokens: int = Field(default=4096, ge=1)

    # Optional parameters
    system: Optional[Union[str, List[SystemMessage]]] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(None, ge=1)
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False

    # Tool use (supports both Tool and CodeExecutionTool for PTC)
    tools: Optional[List[Any]] = None  # Can include Tool or CodeExecutionTool
    tool_choice: Optional[Union[
        Literal["auto", "any"],
        Dict[str, str]  # {"type": "tool", "name": "tool_name"}
    ]] = None

    # Extended thinking
    thinking: Optional[Dict[str, Any]] = None

    # Metadata
    metadata: Optional[Metadata] = None

    # PTC container for session reuse (just the container ID string)
    container: Optional[str] = None

    @field_validator("system", mode="before")
    @classmethod
    def convert_system_string_to_list(cls, v):
        """Convert string system to list of SystemMessage."""
        if isinstance(v, str):
            return [{"type": "text", "text": v}]
        return v


# Response Models
class Usage(BaseModel):
    """Token usage statistics."""
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None


class MessageResponse(BaseModel):
    """Anthropic Messages API response (non-streaming)."""
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: List[ContentBlock]
    model: str
    stop_reason: Optional[Literal[
        "end_turn", "max_tokens", "stop_sequence", "tool_use"
    ]] = None
    stop_sequence: Optional[str] = None
    usage: Usage


# Streaming Event Models
class MessageStartEvent(BaseModel):
    """Stream event: message_start."""
    type: Literal["message_start"] = "message_start"
    message: Dict[str, Any]  # Partial message with id, type, role, model, usage


class ContentBlockStartEvent(BaseModel):
    """Stream event: content_block_start."""
    type: Literal["content_block_start"] = "content_block_start"
    index: int
    content_block: Dict[str, Any]  # Partial content block with type and initial fields


class ContentBlockDeltaEvent(BaseModel):
    """Stream event: content_block_delta."""
    type: Literal["content_block_delta"] = "content_block_delta"
    index: int
    delta: Dict[str, Any]  # Delta with type and changed fields


class ContentBlockStopEvent(BaseModel):
    """Stream event: content_block_stop."""
    type: Literal["content_block_stop"] = "content_block_stop"
    index: int


class MessageDeltaEvent(BaseModel):
    """Stream event: message_delta."""
    type: Literal["message_delta"] = "message_delta"
    delta: Dict[str, Any]  # Delta with stop_reason, stop_sequence
    usage: Optional[Dict[str, int]] = None  # Output token usage


class MessageStopEvent(BaseModel):
    """Stream event: message_stop."""
    type: Literal["message_stop"] = "message_stop"


class PingEvent(BaseModel):
    """Stream event: ping (keep-alive)."""
    type: Literal["ping"] = "ping"


class ErrorEvent(BaseModel):
    """Stream event: error."""
    type: Literal["error"] = "error"
    error: Dict[str, Any]


# Union of all stream events
StreamEvent = Union[
    MessageStartEvent,
    ContentBlockStartEvent,
    ContentBlockDeltaEvent,
    ContentBlockStopEvent,
    MessageDeltaEvent,
    MessageStopEvent,
    PingEvent,
    ErrorEvent,
]


# Error Response
class ErrorDetail(BaseModel):
    """Error detail structure."""
    type: str
    message: str


class ErrorResponse(BaseModel):
    """Error response format."""
    type: Literal["error"] = "error"
    error: ErrorDetail


# Count Tokens Models
class CountTokensRequest(BaseModel):
    """Request to count tokens in a set of messages."""
    model: str
    messages: List[Message]
    system: Optional[Union[str, List[SystemMessage]]] = None
    tools: Optional[List[Tool]] = None

    @field_validator("system", mode="before")
    @classmethod
    def convert_system_string_to_list(cls, v):
        """Convert string system to list of SystemMessage."""
        if isinstance(v, str):
            return [{"type": "text", "text": v}]
        return v


class CountTokensResponse(BaseModel):
    """Response with token count."""
    input_tokens: int
