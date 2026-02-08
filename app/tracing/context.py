"""
Session ID extraction and OTEL context propagation for threaded execution.
"""
from typing import Any, Optional

from opentelemetry import context as otel_context


def get_session_id(request: Any = None, request_data: Any = None) -> Optional[str]:
    """
    Extract session ID from request sources.

    Priority: x-session-id header > metadata.session_id > PTC container > None
    """
    # Try x-session-id header
    if request is not None:
        session_id = None
        if hasattr(request, "headers"):
            session_id = request.headers.get("x-session-id")
        if session_id:
            return session_id

    # Try metadata.session_id from request body
    if request_data is not None:
        metadata = getattr(request_data, "metadata", None)
        if metadata is not None:
            session_id = None
            if isinstance(metadata, dict):
                session_id = metadata.get("session_id")
            elif hasattr(metadata, "session_id"):
                session_id = getattr(metadata, "session_id", None)
            if session_id:
                return session_id

        # Try PTC container field
        container = getattr(request_data, "container", None)
        if container:
            if isinstance(container, str):
                return container
            elif hasattr(container, "id"):
                return container.id

    return None


def propagate_context_to_thread():
    """
    Capture current OTEL context for propagation to a worker thread.

    Call this in the async context BEFORE submitting work to ThreadPoolExecutor.
    Returns a context token that should be passed to attach_context_in_thread().
    """
    return otel_context.get_current()


def attach_context_in_thread(parent_context):
    """
    Attach OTEL context in a worker thread.

    Call this at the START of the thread worker function.
    Returns a token that must be passed to detach_context_in_thread().
    """
    if parent_context is None:
        return None
    return otel_context.attach(parent_context)


def detach_context_in_thread(token):
    """
    Detach OTEL context in a worker thread.

    Call this at the END of the thread worker function (in finally block).
    """
    if token is not None:
        otel_context.detach(token)
