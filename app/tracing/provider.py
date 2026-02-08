"""
OpenTelemetry TracerProvider configuration and management.
"""
import logging
from typing import Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

from app.core.config import settings

logger = logging.getLogger(__name__)

_provider: Optional[TracerProvider] = None


def _parse_headers(headers_str: Optional[str]) -> Dict[str, str]:
    """Parse OTEL headers string 'key1=val1,key2=val2' into dict."""
    if not headers_str:
        return {}
    result = {}
    for pair in headers_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def init_tracing() -> None:
    """Initialize OpenTelemetry tracing with configured exporter."""
    global _provider

    if _provider is not None:
        logger.warning("Tracing already initialized")
        return

    if not settings.enable_tracing:
        logger.info("Tracing is disabled")
        return

    # Build resource
    resource = Resource.create({
        "service.name": settings.otel_service_name,
        "service.version": settings.app_version,
        "deployment.environment": settings.environment,
    })

    # Configure sampler
    sampler = TraceIdRatioBased(settings.otel_trace_sampling_ratio)

    # Create provider
    _provider = TracerProvider(resource=resource, sampler=sampler)

    # Configure exporter based on protocol
    endpoint = settings.otel_exporter_endpoint
    headers = _parse_headers(settings.otel_exporter_headers)

    if endpoint:
        if settings.otel_exporter_protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers=headers or None,
            )
        else:
            # Default: http/protobuf
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            # Append /v1/traces if not already present for HTTP protocol
            traces_endpoint = endpoint
            if not traces_endpoint.endswith("/v1/traces"):
                traces_endpoint = traces_endpoint.rstrip("/") + "/v1/traces"
            exporter = OTLPSpanExporter(
                endpoint=traces_endpoint,
                headers=headers or None,
            )

        # Add batch processor
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=settings.otel_batch_max_queue_size,
            schedule_delay_millis=settings.otel_batch_schedule_delay_ms,
        )
        _provider.add_span_processor(processor)
        logger.info(f"OTEL tracing initialized: endpoint={endpoint}, protocol={settings.otel_exporter_protocol}")
    else:
        logger.warning("OTEL tracing enabled but no endpoint configured (OTEL_EXPORTER_OTLP_ENDPOINT)")

    # Set global provider
    trace.set_tracer_provider(_provider)


def shutdown_tracing() -> None:
    """Flush pending spans and shut down the tracer provider."""
    global _provider
    if _provider is not None:
        try:
            _provider.shutdown()
            logger.info("OTEL tracing shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down OTEL tracing: {e}")
        finally:
            _provider = None


def get_tracer(name: str = "anthropic-bedrock-proxy") -> trace.Tracer:
    """Get a named tracer instance."""
    return trace.get_tracer(name)
