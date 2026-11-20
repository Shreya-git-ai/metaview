"""Local-only text redaction helpers."""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter


_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|api_key|apikey|secret|token|access_key)(\s*[=:]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)(\bauthorization\s*:\s*bearer\s+)([^\s,;]+)")
_JWT = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
_USER = re.compile(r"(?i)\b(username|user)(\s*=\s*)([^\s,;]+)")
_COMMON_KEY = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{6,}|AKIA[A-Z0-9]{12,}|ghp_[A-Za-z0-9]{10,}|xox[A-Za-z0-9-]{6,})\b")
_TOKEN = re.compile(r"\S+")


def _entropy(value: str) -> float:
    """Return Shannon entropy in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _high_entropy(value: str) -> bool:
    """Identify long mixed alphanumeric tokens with high entropy."""
    return len(value) >= 20 and any(char.isalpha() for char in value) and any(char.isdigit() for char in value) and _entropy(value) >= 3.5


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact credentials and direct identifiers, returning fired rule names."""
    fired: list[str] = []

    def mark(rule: str) -> None:
        if rule not in fired:
            fired.append(rule)

    def replace_assignment(match: re.Match[str]) -> str:
        mark(match.group(1).lower())
        return f"{match.group(1)}{match.group(2)}[REDACTED]"

    result = _ASSIGNMENT_SECRET.sub(replace_assignment, text)

    def replace_bearer(match: re.Match[str]) -> str:
        mark("authorization_bearer")
        return f"{match.group(1)}[REDACTED]"

    result = _BEARER.sub(replace_bearer, result)

    def replace_jwt(match: re.Match[str]) -> str:
        mark("jwt")
        return "[REDACTED]"

    result = _JWT.sub(replace_jwt, result)

    def replace_key(match: re.Match[str]) -> str:
        mark("common_key")
        return "[REDACTED]"

    result = _COMMON_KEY.sub(replace_key, result)

    def replace_email(match: re.Match[str]) -> str:
        mark("email")
        return "[EMAIL]"

    result = _EMAIL.sub(replace_email, result)

    salt = os.getenv("SOURCE_SALT", "change-me")

    def replace_ip(match: re.Match[str]) -> str:
        mark("ipv4")
        digest = hashlib.sha256((salt + match.group(0)).encode("utf-8")).hexdigest()
        return f"ip-{digest[:6]}"

    result = _IPV4.sub(replace_ip, result)

    def replace_user(match: re.Match[str]) -> str:
        mark("user")
        return f"user=[USER]"

    result = _USER.sub(replace_user, result)

    def replace_entropy(match: re.Match[str]) -> str:
        value = match.group(0)
        if _high_entropy(value):
            mark("high_entropy")
            return "[REDACTED]"
        return value

    result = _TOKEN.sub(replace_entropy, result)
    return result, fired
