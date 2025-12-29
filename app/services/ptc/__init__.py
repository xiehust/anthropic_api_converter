"""
Programmatic Tool Calling (PTC) module.

Provides Docker sandbox-based code execution for Anthropic's
Programmatic Tool Calling feature.
"""

from .sandbox import (
    PTCSandboxExecutor,
    SandboxConfig,
    SandboxSession,
    ToolCallRequest,
    BatchToolCallRequest,
    ExecutionResult,
    PendingToolCall,
)
from .exceptions import (
    PTCError,
    SandboxError,
    SandboxTimeoutError,
    CodeExecutionError,
    ContainerError,
    IPCError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
    DockerNotAvailableError,
    ToolExecutionError,
)

__all__ = [
    # Sandbox
    "PTCSandboxExecutor",
    "SandboxConfig",
    "SandboxSession",
    "ToolCallRequest",
    "BatchToolCallRequest",
    "ExecutionResult",
    "PendingToolCall",
    # Exceptions
    "PTCError",
    "SandboxError",
    "SandboxTimeoutError",
    "CodeExecutionError",
    "ContainerError",
    "IPCError",
    "SessionError",
    "SessionExpiredError",
    "SessionNotFoundError",
    "DockerNotAvailableError",
    "ToolExecutionError",
]
