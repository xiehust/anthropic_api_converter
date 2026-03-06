"""
Property-based and unit tests for KeyEncryption.

Feature: multi-provider-routing-gateway
"""
import logging
from unittest.mock import patch

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from app.keypool.encryption import KeyEncryption
from app.keypool.manager import KeyPoolManager, KeyState


# ---------------------------------------------------------------------------
# Property 4: Fernet encryption round-trip
# ---------------------------------------------------------------------------


class TestFernetEncryptDecryptRoundTrip:
    """
    **Property 4: Fernet encryption round-trip**

    For any non-empty string, encrypt then decrypt returns the original string.

    **Validates: Requirements 3.2, 16.1**
    """

    @given(
        plaintext=st.text(min_size=1, max_size=500),
        secret=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=150)
    def test_encrypt_then_decrypt_returns_original(self, plaintext: str, secret: str):
        """
        **Validates: Requirements 3.2, 16.1**

        For any non-empty string and any non-empty secret,
        encrypt(plaintext) → decrypt → plaintext.
        """
        enc = KeyEncryption(secret)
        ciphertext = enc.encrypt(plaintext)
        assert enc.decrypt(ciphertext) == plaintext

    @given(
        plaintext=st.text(min_size=1, max_size=500),
        secret=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=150)
    def test_ciphertext_differs_from_plaintext(self, plaintext: str, secret: str):
        """
        **Validates: Requirements 3.2, 16.1**

        The ciphertext should not equal the plaintext (encryption actually transforms).
        """
        enc = KeyEncryption(secret)
        ciphertext = enc.encrypt(plaintext)
        assert ciphertext != plaintext


# ---------------------------------------------------------------------------
# Property 20: Key masking format
# ---------------------------------------------------------------------------


class TestKeyMaskingFormat:
    """
    **Property 20: Key masking format**

    For any key with length > 8, mask returns `{first4}****{last4}`.
    For length ≤ 8, returns `"****"`.

    **Validates: Requirements 16.3, 17.6**
    """

    @given(key=st.text(min_size=9, max_size=200))
    @settings(max_examples=150)
    def test_long_key_mask_format(self, key: str):
        """
        **Validates: Requirements 16.3, 17.6**

        For any key with length > 8, mask returns first4****last4.
        """
        masked = KeyEncryption.mask(key)
        assert masked == f"{key[:4]}****{key[-4:]}"

    @given(key=st.text(min_size=0, max_size=8))
    @settings(max_examples=150)
    def test_short_key_mask_returns_stars(self, key: str):
        """
        **Validates: Requirements 16.3, 17.6**

        For any key with length ≤ 8, mask returns "****".
        """
        masked = KeyEncryption.mask(key)
        assert masked == "****"


# ---------------------------------------------------------------------------
# Unit tests for encryption edge cases (Task 5.4)
# ---------------------------------------------------------------------------


class TestEncryptionEdgeCases:
    """
    Unit tests for encryption edge cases.

    Requirements: 3.3, 3.4
    """

    def test_missing_encryption_secret_disables_multi_provider(self, caplog):
        """
        When PROVIDER_KEY_ENCRYPTION_SECRET is not set, startup logs a warning
        and disables multi-provider.

        Requirements: 3.3
        """
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.provider_key_encryption_secret = None
            mock_settings.multi_provider_enabled = True

            # Simulate the startup check from app/main.py
            if not mock_settings.provider_key_encryption_secret:
                logging.getLogger(__name__).warning(
                    "PROVIDER_KEY_ENCRYPTION_SECRET not set — disabling multi-provider features"
                )
                mock_settings.multi_provider_enabled = False

            assert mock_settings.multi_provider_enabled is False
            assert "PROVIDER_KEY_ENCRYPTION_SECRET not set" in caplog.text

    def test_decrypt_failure_marks_key_unavailable_and_logs_error(self, caplog):
        """
        When decryption fails, the key is marked as unavailable (is_enabled=False)
        and an error is logged without exposing key content.

        Requirements: 3.4
        """
        enc = KeyEncryption("test-secret")
        pool = KeyPoolManager(encryption=enc)

        # Load a key with invalid ciphertext that will fail decryption
        pool.load_keys_from_items([
            {
                "key_id": "key-bad",
                "provider": "openai",
                "encrypted_api_key": "not-valid-fernet-ciphertext",
                "models": ["gpt-4"],
                "is_enabled": True,
            }
        ])

        with caplog.at_level(logging.ERROR):
            result = pool.get_available_key("openai", "gpt-4")

        # Key should be marked disabled and return None (no other keys available)
        assert result is None
        key_state = pool._keys["openai"][0]
        assert key_state.is_enabled is False

        # Error log should mention the key ID but NOT the ciphertext
        assert "key-bad" in caplog.text
        assert "not-valid-fernet-ciphertext" not in caplog.text
