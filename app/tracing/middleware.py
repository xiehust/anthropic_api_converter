"""
FastAPI middleware for creating root request spans.
"""
import hashlib
import logging
from typing import Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from opentelemetry.trace import StatusCode

from app.tracing.attributes import (
    SPAN_PROXY_REQUEST,
    PROXY_API_KEY_HASH,
    PROXY_SERVICE_TIER,
    LANGFUSE_USER_ID,
)
from app.tracing.provider import get_tracer

logger = logging.getLogger(__name__)

# Paths to skip tracing
SKIP_PATHS: Set[str] = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}
SKIP_PATH_PREFIXES = ("/health/",)


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware that creates root request spans for API calls."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip non-API paths
        if path in SKIP_PATHS or any(path.startswith(p) for p in SKIP_PATH_PREFIXES):
            return await call_next(request)

        tracer = get_tracer("app.middleware.tracing")

        with tracer.start_as_current_span(SPAN_PROXY_REQUEST) as span:
            # Set HTTP attributes
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", str(request.url))
            span.set_attribute("http.route", path)

            # Set API key hash if available (set by auth middleware in request.state)
            api_key = getattr(request.state, "api_key", None) if hasattr(request, "state") else None
            if api_key:
                key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
                span.set_attribute(PROXY_API_KEY_HASH, key_hash)

            # Set user ID and service tier from auth middleware
            user_id = getattr(request.state, "user_id", None) if hasattr(request, "state") else None
            if user_id:
                span.set_attribute(LANGFUSE_USER_ID, user_id)

            service_tier = getattr(request.state, "service_tier", None) if hasattr(request, "state") else None
            if service_tier:
                span.set_attribute(PROXY_SERVICE_TIER, service_tier)

            # Store span in request state for child span creation
            request.state.trace_span = span

            try:
                response = await call_next(request)

                # Set response status
                span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 400:
                    span.set_status(StatusCode.ERROR, f"HTTP {response.status_code}")

                return response

            except Exception as e:
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                raise
