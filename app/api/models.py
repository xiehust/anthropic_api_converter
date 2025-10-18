"""
Models API endpoints.

Implements GET /v1/models for listing available Bedrock models.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.services.bedrock_service import BedrockService

router = APIRouter()


def get_bedrock_service() -> BedrockService:
    """Get Bedrock service instance."""
    return BedrockService()


@router.get(
    "/models",
    summary="List available models",
    description="List all available models in AWS Bedrock that support text generation.",
)
async def list_models(
    bedrock_service: BedrockService = Depends(get_bedrock_service),
):
    """
    List available models.

    Returns a list of all available Bedrock models that support the Converse API.

    Returns:
        Dictionary with list of models and their details

    Raises:
        HTTPException: If failed to retrieve models
    """
    try:
        models = bedrock_service.list_available_models()

        return {
            "object": "list",
            "data": models,
            "has_more": False,
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": f"Failed to list models: {str(e)}",
            },
        )


@router.get(
    "/models/{model_id}",
    summary="Get model information",
    description="Get detailed information about a specific model.",
)
async def get_model(
    model_id: str,
    bedrock_service: BedrockService = Depends(get_bedrock_service),
):
    """
    Get model information.

    Args:
        model_id: Model identifier
        bedrock_service: Bedrock service instance

    Returns:
        Model information dictionary

    Raises:
        HTTPException: If model not found or error retrieving info
    """
    try:
        model_info = bedrock_service.get_model_info(model_id)

        if not model_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "type": "not_found_error",
                    "message": f"Model {model_id} not found",
                },
            )

        return {
            "object": "model",
            **model_info,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": f"Failed to get model info: {str(e)}",
            },
        )
