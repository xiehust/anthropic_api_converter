"""Tests for OpenAI-compat configuration settings."""
import os
import pytest
from unittest.mock import patch


def test_openai_compat_defaults():
    """Test that OpenAI-compat settings have correct defaults."""
    import app.core.config as config_module
    config_module.get_settings.cache_clear()
    with patch.dict(os.environ, {}, clear=False):
        settings = config_module.get_settings()
        assert settings.enable_openai_compat is False
        assert settings.openai_api_key == ""
        assert settings.openai_base_url == ""
        assert settings.openai_compat_thinking_high_threshold == 10000
        assert settings.openai_compat_thinking_medium_threshold == 4000
    config_module.get_settings.cache_clear()


def test_openai_compat_enabled_from_env():
    """Test enabling OpenAI-compat via env var."""
    import app.core.config as config_module
    config_module.get_settings.cache_clear()
    with patch.dict(os.environ, {
        "ENABLE_OPENAI_COMPAT": "True",
        "OPENAI_API_KEY": "test-key-123",
        "OPENAI_BASE_URL": "https://bedrock-mantle.us-east-1.api.aws/v1",
    }, clear=False):
        settings = config_module.get_settings()
        assert settings.enable_openai_compat is True
        assert settings.openai_api_key == "test-key-123"
        assert settings.openai_base_url == "https://bedrock-mantle.us-east-1.api.aws/v1"
    config_module.get_settings.cache_clear()
