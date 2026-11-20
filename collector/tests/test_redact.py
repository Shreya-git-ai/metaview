"""Stage 1 tests for local text redaction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redact import redact_text


def test_credentials_and_identifiers_are_redacted() -> None:
    raw = "password=Abc@123 api_key=sk-live-9f2c81 token=eyJhbGciOi.abc.def user=rahul ip=10.0.0.7 mail=x@y.com"
    clean, rules = redact_text(raw)
    assert "Abc@123" not in clean
    assert "sk-live-9f2c81" not in clean
    assert "eyJhbGciOi" not in clean
    assert "rahul" not in clean
    assert "10.0.0.7" not in clean
    assert "x@y.com" not in clean
    assert {"password", "api_key", "token", "user", "ipv4", "email"}.issubset(set(rules))


def test_redaction_is_idempotent() -> None:
    clean, _ = redact_text("Authorization: Bearer abcdefghijklmnop")
    clean_again, _ = redact_text(clean)
    assert clean_again == clean


def test_common_key_prefix_and_high_entropy_token() -> None:
    clean, rules = redact_text("ghp_abcdefghijklmnopqrstuvwxyz Qx9Zp3Lm7Ka2Vb5Nc8Rd")
    assert "ghp_" not in clean
    assert "Qx9Zp3Lm7Ka2Vb5Nc8Rd" not in clean
    assert "common_key" in rules
    assert "high_entropy" in rules
