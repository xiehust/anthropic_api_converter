"""
FastAPI middleware for creating root request spans.
"""
import hashlib
import json
import logging
from typing import Optional, Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from opentelemetry import trace as trace_api
from opentelemetry.trace import StatusCode, SpanContext, TraceFlags, NonRecordingSpan

from app.tracing.attributes import (
    SPAN_PROXY_REQUEST,
    SPAN_TRACE_ROOT,
    PROXY_API_KEY_HASH,
    PROXY_SERVICE_TIER,
    LANGFUSE_USER_ID,
    LANGFUSE_SESSION_ID,
)
from app.tracing.provider import get_tracer
from app.tracing.session_store import get_session_store

logger = logging.getLogger(__name__)

# Paths to skip tracing
SKIP_PATHS: Set[str] = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}
SKIP_PATH_PREFIXES = ("/health/", "/v1/messages/count_tokens",)


def _derive_session_from_body(body: bytes) -> Optional[str]:
    """Try to derive a session ID from the request body JSON."""
    try:
        data = json.loads(body)
        model = data.get("model", "")

        # Check explicit metadata.session_id
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and metadata.get("session_id"):
            return metadata["session_id"]

        # Auto-derive from first user message + model
        messages = data.get("messages", [])
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                key = f"{model}:{content}"
                return f"auto-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        key = f"{model}:{block['text']}"
                        return f"auto-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
            break  # Only check first user message
    except Exception:
        pass
    return None


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware that creates root request spans for API calls."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip non-API paths
        if path in SKIP_PATHS or any(path.startswith(p) for p in SKIP_PATH_PREFIXES):
            return await call_next(request)

        tracer = get_tracer("app.middleware.tracing")

        # For POST /v1/messages: use turn-based tracing structure
        if request.method == "POST" and path == "/v1/messages":
            return await self._dispatch_messages(request, call_next, tracer)

        # For all other API paths: keep existing proxy.request span behavior
        return await self._dispatch_generic(request, call_next, tracer)

    async def _dispatch_messages(self, request: Request, call_next, tracer):
        """Handle /v1/messages with turn-based trace structure."""
        store = get_session_store()

        # Determine session ID
        session_id = request.headers.get("x-session-id") if hasattr(request, "headers") else None
        if not session_id:
            try:
                body = await request.body()
                if body:
                    session_id = _derive_session_from_body(body)
            except Exception:
                pass

        if not session_id:
            # No session ID — fall back to generic span behavior
            return await self._dispatch_generic(request, call_next, tracer)

        # Look up or create trace context for this session
        existing = store.get(session_id)
        is_first_turn = existing is None

        if is_first_turn:
            # First request: create a root span to anchor the trace
            root_span = tracer.start_span(SPAN_TRACE_ROOT)
            span_ctx = root_span.get_span_context()

            # Set session ID on root span
            root_span.set_attribute(LANGFUSE_SESSION_ID, session_id)

            store.put(session_id, span_ctx.trace_id, span_ctx.span_id, root_span)

            # Build parent context from the root span
            parent_ctx = trace_api.set_span_in_context(root_span)
        else:
            trace_id, span_id, turn_count, root_span = existing
            # Build parent context from stored trace/span IDs
            parent_span_context = SpanContext(
                trace_id=trace_id,
                span_id=span_id,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            parent_span = NonRecordingSpan(parent_span_context)
            parent_ctx = trace_api.set_span_in_context(parent_span)

        # Get next turn number
        turn_num = store.next_turn(session_id)

        # Store turn-based tracing info in request.state for messages.py to use
        request.state.trace_session_id = session_id
        request.state.trace_parent_ctx = parent_ctx
        request.state.trace_turn_num = turn_num
        request.state.trace_is_first_turn = is_first_turn
        request.state.trace_root_span = root_span

        # Set API key hash and user info on request state (set by auth middleware later)
        # These will be used by messages.py to set on spans

        try:
            response = await call_next(request)
            return response
        except Exception:
            raise

    async def _dispatch_generic(self, request: Request, call_next, tracer):
        """Handle non-messages API paths with standard proxy.request span."""
        session_id = None
        parent_ctx = None

        if hasattr(request, "headers"):
            session_id = request.headers.get("x-session-id")

        if not session_id and request.method == "POST":
            try:
                body = await request.body()
                if body:
                    session_id = _derive_session_from_body(body)
            except Exception:
                pass

        if session_id:
            store = get_session_store()
            existing = store.get(session_id)
            if existing:
                trace_id, span_id, turn_count, root_span = existing
                parent_span_context = SpanContext(
                    trace_id=trace_id,
                    span_id=span_id,
                    is_remote=True,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
                parent_span = NonRecordingSpan(parent_span_context)
                parent_ctx = trace_api.set_span_in_context(parent_span)

        with tracer.start_as_current_span(SPAN_PROXY_REQUEST, context=parent_ctx) as span:
            if session_id and parent_ctx is None:
                span_context = span.get_span_context()
                store = get_session_store()
                store.put(session_id, span_context.trace_id, span_context.span_id)

            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", str(request.url))
            span.set_attribute("http.route", request.url.path)

            api_key = getattr(request.state, "api_key", None) if hasattr(request, "state") else None
            if api_key:
                key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
                span.set_attribute(PROXY_API_KEY_HASH, key_hash)

            user_id = getattr(request.state, "user_id", None) if hasattr(request, "state") else None
            if user_id:
                span.set_attribute(LANGFUSE_USER_ID, user_id)

            service_tier = getattr(request.state, "service_tier", None) if hasattr(request, "state") else None
            if service_tier:
                span.set_attribute(PROXY_SERVICE_TIER, service_tier)

            request.state.trace_span = span

            try:
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 400:
                    span.set_status(StatusCode.ERROR, f"HTTP {response.status_code}")
                return response
            except Exception as e:
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                raise
