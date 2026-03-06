"""
Property-based and unit tests for KeyEncryption.

Feature: multi-provider-routing-gateway
Tests: Property 4 (Fernet round-trip), Property 20 (Key masking), encryption edge cases.
"""
import logging

import hypothesis.strategies as st
import pytest
from cryptography.fernet import InvalidToken
from hypothesis import given, settings

from app.keypool.encryption import KeyEncryption
from app.keypool.manager import KeyPoolManager


# ---------------------------------------------------------------------------
# Property 4: Fernet encryption round-trip
# ---------------------------------------------------------------------------


class TestFernetEncryptDecryptRoundTrip:
    """
    **Property 4: Fernet encryption round-trip**

    For any non-empty string, encrypt then decrypt returns the original string.

    **Validates: Requirements 3.2, 16.1**
    """

    @given(plaintext=st.text(min_size=1, max_size=500))
    @settings(max_examples=150)
    def test_encrypt_then_decrypt_returns_original(self, plaintext: str):
        """
        **Validates: Requirements 3.2, 16.1**

        For any non-empty string, encrypt(plaintext) → decrypt → plaintext.
        """
        enc = KeyEncryption("test-secret-key")
        ciphertext = enc.encrypt(plaintext)
        assert enc.decrypt(ciphertext) == plaintext

    @given(plaintext=st.text(min_size=1, max_size=500))
    @settings(max_examples=150)
    def test_ciphertext_differs_from_plaintext(self, plaintext: str):
        """
        **Validates: Requirements 3.2, 16.1**

        The ciphertext should not equal the plaintext.
        """
        enc = KeyEncryption("test-secret-key")
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

    def test_decrypt_with_wrong_ciphertext_raises_invalid_token(self):
        """
        Decrypting invalid ciphertext raises InvalidToken.

        Requirements: 3.4
        """
        enc = KeyEncryption("test-secret")
        with pytest.raises(InvalidToken):
            enc.decrypt("not-valid-fernet-ciphertext")

    def test_decrypt_failure_marks_key_unavailable_and_logs_error(self, caplog):
        """
        When decryption fails, the key is marked as unavailable (is_enabled=False)
        and an error is logged without exposing key content.

        Requirements: 3.4
        """
        enc = KeyEncryption("test-secret")
        pool = KeyPoolManager(encryption=enc)

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

        # No key returned since decryption failed
        assert result is None
        # Key should be marked disabled
        key_state = pool._keys["openai"][0]
        assert key_state.is_enabled is False
        # Error log mentions key ID but NOT the ciphertext
        assert "key-bad" in caplog.text
        assert "not-valid-fernet-ciphertext" not in caplog.text
