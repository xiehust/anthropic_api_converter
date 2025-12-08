"""
Application configuration management using Pydantic Settings.

Loads configuration from environment variables with validation and type safety.
"""
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_parse_none_str="null",  # Don't parse empty strings as None
    )

    # Application Settings
    app_name: str = Field(default="Anthropic-Bedrock API Proxy", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Server Settings
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    workers: int = Field(default=1, alias="WORKERS")
    reload: bool = Field(default=False, alias="RELOAD")

    # API Settings
    api_prefix: str = Field(default="/v1", alias="API_PREFIX")
    docs_url: Optional[str] = Field(default="/docs", alias="DOCS_URL")
    openapi_url: Optional[str] = Field(default="/openapi.json", alias="OPENAPI_URL")
    cors_origins: Union[str, List[str]] = Field(
        default=["*"],
        alias="CORS_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: Union[str, List[str]] = Field(
        default=["*"], alias="CORS_ALLOW_METHODS"
    )
    cors_allow_headers: Union[str, List[str]] = Field(
        default=["*"], alias="CORS_ALLOW_HEADERS"
    )

    # AWS Settings
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: Optional[str] = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(
        default=None, alias="AWS_SECRET_ACCESS_KEY"
    )
    aws_session_token: Optional[str] = Field(default=None, alias="AWS_SESSION_TOKEN")
    bedrock_endpoint_url: Optional[str] = Field(
        default=None, alias="BEDROCK_ENDPOINT_URL"
    )

    # DynamoDB Settings
    dynamodb_endpoint_url: Optional[str] = Field(
        default=None, alias="DYNAMODB_ENDPOINT_URL"
    )
    dynamodb_api_keys_table: str = Field(
        default="anthropic-proxy-api-keys", alias="DYNAMODB_API_KEYS_TABLE"
    )
    dynamodb_usage_table: str = Field(
        default="anthropic-proxy-usage", alias="DYNAMODB_USAGE_TABLE"
    )
    dynamodb_cache_table: str = Field(
        default="anthropic-proxy-cache", alias="DYNAMODB_CACHE_TABLE"
    )
    dynamodb_model_mapping_table: str = Field(
        default="anthropic-proxy-model-mapping", alias="DYNAMODB_MODEL_MAPPING_TABLE"
    )

    # Authentication Settings
    api_key_header: str = Field(default="x-api-key", alias="API_KEY_HEADER")
    require_api_key: bool = Field(default=True, alias="REQUIRE_API_KEY")
    master_api_key: Optional[str] = Field(default=None, alias="MASTER_API_KEY")

    # Rate Limiting Settings
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(
        default=1000, alias="RATE_LIMIT_REQUESTS"
    )  # requests per window
    rate_limit_window: int = Field(
        default=60, alias="RATE_LIMIT_WINDOW"
    )  # window in seconds

    # Caching Settings
    cache_enabled: bool = Field(default=False, alias="CACHE_ENABLED")
    cache_ttl: int = Field(default=3600, alias="CACHE_TTL")  # seconds
    prompt_caching_enabled: bool = Field(
        default=True, alias="PROMPT_CACHING_ENABLED"
    )  # Bedrock prompt caching
    # Model Mapping
    default_model_mapping: Dict[str, str] = Field(
        default={
            # Anthropic model IDs -> Bedrock model ARNs
            "claude-opus-4-5-20251101": "global.anthropic.claude-opus-4-5-20251101-v1:0",
            "claude-sonnet-4-5-20250929": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "claude-haiku-4-5-20251001": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "claude-3-5-haiku-20241022": "us.anthropic.claude-3-5-haiku-20241022-v1:0"

        },
        alias="DEFAULT_MODEL_MAPPING",
    )

    # Streaming Settings
    streaming_chunk_size: int = Field(
        default=1024, alias="STREAMING_CHUNK_SIZE"
    )  # bytes
    streaming_timeout: int = Field(default=1800, alias="STREAMING_TIMEOUT")  # seconds

    # Monitoring & Observability
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")
    enable_tracing: bool = Field(default=False, alias="ENABLE_TRACING")
    sentry_dsn: Optional[str] = Field(default=None, alias="SENTRY_DSN")

    # Request Timeouts
    bedrock_timeout: int = Field(default=1800, alias="BEDROCK_TIMEOUT")  # seconds
    dynamodb_timeout: int = Field(default=10, alias="DYNAMODB_TIMEOUT")  # seconds

    # Feature Flags
    enable_tool_use: bool = Field(default=True, alias="ENABLE_TOOL_USE")
    enable_extended_thinking: bool = Field(
        default=True, alias="ENABLE_EXTENDED_THINKING"
    )
    enable_document_support: bool = Field(
        default=True, alias="ENABLE_DOCUMENT_SUPPORT"
    )
    fine_grained_tool_streaming_enabled: bool = Field(
        default=True, alias="FINE_GRAINED_TOOL_STREAMING_ENABLED"
    )
    interleaved_thinking_enabled: bool = Field(
        default=True, alias="INTERLEAVED_THINKING_ENABLED"
    )

    @field_validator("cors_origins", "cors_allow_methods", "cors_allow_headers", mode="before")
    @classmethod
    def parse_list_fields(cls, v: Any) -> List[str]:
        """Parse list fields from comma-separated string or return as-is."""
        if isinstance(v, str):
            # Handle comma-separated values
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return v
        return [str(v)]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v):
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}")
        return v

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        """Validate environment."""
        valid_envs = ["development", "staging", "production"]
        v = v.lower()
        if v not in valid_envs:
            raise ValueError(f"Environment must be one of {valid_envs}")
        return v


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Using lru_cache ensures settings are loaded only once.
    """
    return Settings()


# Export settings instance
settings = get_settings()
