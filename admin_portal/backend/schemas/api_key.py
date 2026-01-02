"""API Key schemas."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    """Schema for creating a new API key."""

    user_id: str = Field(..., description="User identifier")
    name: str = Field(..., description="Human-readable name for the key")
    owner_name: Optional[str] = Field(None, description="Display name for the owner")
    role: Optional[str] = Field("Full Access", description="Role type")
    monthly_budget: Optional[float] = Field(0, description="Monthly budget limit in USD")
    tpm_limit: Optional[int] = Field(100000, description="Tokens per minute limit")
    rate_limit: Optional[int] = Field(None, description="Custom rate limit")
    service_tier: Optional[str] = Field(None, description="Bedrock service tier")


class ApiKeyUpdate(BaseModel):
    """Schema for updating an API key."""

    name: Optional[str] = None
    owner_name: Optional[str] = None
    role: Optional[str] = None
    monthly_budget: Optional[float] = None
    budget_used: Optional[float] = None
    tpm_limit: Optional[int] = None
    rate_limit: Optional[int] = None
    service_tier: Optional[str] = None
    is_active: Optional[bool] = None


class ApiKeyResponse(BaseModel):
    """Schema for API key response."""

    api_key: str
    user_id: str
    name: str
    created_at: int
    is_active: bool
    rate_limit: int
    service_tier: str
    owner_name: Optional[str] = None
    role: Optional[str] = None
    monthly_budget: Optional[float] = 0
    budget_used: Optional[float] = 0  # Total cumulative budget (never resets)
    budget_used_mtd: Optional[float] = 0  # Month-to-date budget (resets monthly)
    budget_mtd_month: Optional[str] = None  # Month for MTD tracking (YYYY-MM)
    budget_history: Optional[str] = None  # Monthly budget history as JSON string (e.g., {"2025-11": 32.11})
    tpm_limit: Optional[int] = 100000
    updated_at: Optional[int] = None
    deactivated_reason: Optional[str] = None  # Reason for deactivation
    metadata: Optional[Dict[str, Any]] = None
    # Usage stats (aggregated from usage_stats table)
    total_input_tokens: Optional[int] = 0
    total_output_tokens: Optional[int] = 0
    total_requests: Optional[int] = 0

    class Config:
        extra = "allow"


class ApiKeyListResponse(BaseModel):
    """Schema for paginated API key list response."""

    items: List[ApiKeyResponse]
    count: int
    last_key: Optional[Dict[str, Any]] = None
