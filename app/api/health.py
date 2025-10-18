"""
Health check endpoint.

Provides application health status and readiness checks.
"""
import time
from datetime import datetime

from fastapi import APIRouter, status

from app.core.config import settings

router = APIRouter()

# Store application start time
START_TIME = time.time()


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Check application health and readiness.",
    tags=["monitoring"],
)
async def health_check():
    """
    Health check endpoint.

    Returns application health status, uptime, and configuration info.

    Returns:
        Dictionary with health status information
    """
    uptime_seconds = int(time.time() - START_TIME)

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": uptime_seconds,
        "version": settings.app_version,
        "environment": settings.environment,
        "services": {
            "bedrock": {
                "status": "available",
                "region": settings.aws_region,
            },
            "dynamodb": {
                "status": "available",
                "region": settings.aws_region,
            },
        },
        "features": {
            "streaming": True,
            "tool_use": settings.enable_tool_use,
            "extended_thinking": settings.enable_extended_thinking,
            "document_support": settings.enable_document_support,
            "prompt_caching": settings.prompt_caching_enabled,
        },
    }


@router.get(
    "/ready",
    status_code=status.HTTP_200_OK,
    summary="Readiness check",
    description="Check if application is ready to serve requests.",
    tags=["monitoring"],
)
async def readiness_check():
    """
    Readiness check endpoint.

    Used by orchestrators (Kubernetes, ECS) to determine if the application
    is ready to receive traffic.

    Returns:
        Dictionary with readiness status
    """
    # In a production system, you might check:
    # - Database connectivity
    # - AWS credentials validity
    # - Required environment variables
    # - Dependent service availability

    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get(
    "/liveness",
    status_code=status.HTTP_200_OK,
    summary="Liveness check",
    description="Check if application is alive (used by orchestrators).",
    tags=["monitoring"],
)
async def liveness_check():
    """
    Liveness check endpoint.

    Used by orchestrators to determine if the application is alive.
    Should be a lightweight check that doesn't test dependencies.

    Returns:
        Dictionary with liveness status
    """
    return {
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat(),
    }
