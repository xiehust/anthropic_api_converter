"""
Unit tests for multi-provider gateway feature flag defaults.

Validates: Requirements 15.1, 15.2, 15.3, 15.4, 15.5, 15.6
"""
import pytest
from unittest.mock import patch

from pydantic_settings import SettingsConfigDict


class TestFeatureFlagDefaults:
    """Verify all feature flags have correct default values.

    We subclass Settings with env_file disabled so the project's .env
    doesn't interfere with default-value assertions.
    """

    def _make_clean_settings(self, **env_overrides):
        """Create a Settings instance that ignores .env files."""
        from app.core.config import Settings

        # Subclass to disable .env file loading
        class CleanSettings(Settings):
            model_config = SettingsConfigDict(
                env_file=None,
                case_sensitive=False,
                extra="ignore",
            )

        with patch.dict("os.environ", env_overrides, clear=True):
            return CleanSettings()

    def test_multi_provider_enabled_default_false(self):
        """Validates: Requirement 15.1 - MULTI_PROVIDER_ENABLED defaults to false."""
        s = self._make_clean_settings()
        assert s.multi_provider_enabled is False

    def test_routing_enabled_default_false(self):
        """Validates: Requirement 15.2 - ROUTING_ENABLED defaults to false."""
        s = self._make_clean_settings()
        assert s.routing_enabled is False

    def test_smart_routing_enabled_default_false(self):
        """Validates: Requirement 15.3 - SMART_ROUTING_ENABLED defaults to false."""
        s = self._make_clean_settings()
        assert s.smart_routing_enabled is False

    def test_failover_enabled_default_true(self):
        """Validates: Requirement 15.4 - FAILOVER_ENABLED defaults to true."""
        s = self._make_clean_settings()
        assert s.failover_enabled is True

    def test_compression_enabled_default_false(self):
        """Validates: Requirement 15.5 - COMPRESSION_ENABLED defaults to false."""
        s = self._make_clean_settings()
        assert s.compression_enabled is False

    def test_cache_aware_routing_enabled_default_true(self):
        """Validates: Requirement 12.6 - CACHE_AWARE_ROUTING_ENABLED defaults to true."""
        s = self._make_clean_settings()
        assert s.cache_aware_routing_enabled is True

    def test_all_defaults_no_new_code_paths(self):
        """
        Validates: Requirement 15.6 - When all flags are defaults,
        no new code paths are triggered. The master switch
        (multi_provider_enabled=False) keeps the gateway on the
        original BedrockService path.
        """
        s = self._make_clean_settings()

        assert s.multi_provider_enabled is False
        assert s.routing_enabled is False
        assert s.smart_routing_enabled is False
        assert s.compression_enabled is False
        # failover_enabled=True is fine — it only activates when
        # multi_provider_enabled=True, so it's a no-op at defaults.
        assert s.failover_enabled is True
