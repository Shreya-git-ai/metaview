"""Secret scrubbing and pseudonymisation of raw log text (runs locally only).

`redact()` returns the scrubbed text, per-rule findings, and the list of secret values that were
removed. Those values are kept ONLY in memory for the current window (as canaries for the policy
engine) and are never persisted or transmitted.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    kind: str          # "secret" (removed) or "identifier" (pseudonymised)
    pattern: re.Pattern
    description: str


RULES: list[Rule] = [
    Rule("R-PKEY", "Private key block", "secret",
         re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
         "PEM private key material"),
    Rule("R-URLCRED", "URL credential", "secret",
         re.compile(r"(?P<pre>[a-z][a-z0-9+.-]*://[^:/\s@]+:)(?P<secret>[^@\s]+)(?=@)", re.I),
         "Password embedded in a connection URL"),
    Rule("R-BEARER", "Bearer token", "secret",
         re.compile(r"(?P<pre>\bBearer\s+)(?P<secret>[A-Za-z0-9\-_.=+/]{8,})", re.I),
         "HTTP Authorization bearer token"),
    Rule("R-JWT", "JSON Web Token", "secret",
         re.compile(r"(?P<secret>\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"),
         "Encoded JWT"),
    Rule("R-AWSKEY", "AWS access key id", "secret",
         re.compile(r"(?P<secret>\bAKIA[0-9A-Z]{12,20}\b)"), "AWS-style access key id (lenient length)"),
    Rule("R-APIKEY", "API key literal", "secret",
         re.compile(r"(?P<secret>\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,})"), "Provider API key (sk_/pk_ style)"),
    Rule("R-PASSWORD", "Password assignment", "secret",
         re.compile(r"(?P<pre>(?:pass(?:word|wd)?|pwd)\s*[=:]\s*)(?P<secret>[^\s,;&\"']+)", re.I),
         "password=/passwd=/pwd= key-value pairs (also --db-password=)"),
    Rule("R-KV-SECRET", "Secret/token assignment", "secret",
         re.compile(r"(?P<pre>\b(?:api[_-]?key|apikey|secret|token|access[_-]?key)\s*[=:]\s*)(?P<secret>[^\s,;&\"']+)", re.I),
         "api_key=/secret=/token= key-value pairs"),
    Rule("R-EMAIL", "E-mail address", "identifier",
         re.compile(r"(?P<secret>\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)"), "Pseudonymised e-mail"),
    Rule("R-IPV4", "IPv4 address", "identifier",
         re.compile(r"(?P<secret>\b(?:\d{1,3}\.){3}\d{1,3}\b)"), "Pseudonymised IP address"),
    Rule("R-USER", "Account name", "identifier",
         re.compile(r"(?P<pre>\b(?:user=|for (?:invalid user )?))(?P<secret>[A-Za-z0-9._-]+)"), "Pseudonymised account name"),
]

SECRET_RULES = [r for r in RULES if r.kind == "secret"]


class Redactor:
    def __init__(self, pseudonym_key: str) -> None:
        self._key = pseudonym_key.encode()

    def pseudonym(self, kind: str, value: str) -> str:
        return f"[{kind}:{hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:8]}]"

    def redact(self, text: str) -> tuple[str, list[dict], list[str]]:
        findings: list[dict] = []
        secrets: list[str] = []
        for rule in RULES:
            count = 0

            def _sub(m: re.Match, rule: Rule = rule) -> str:
                nonlocal count
                val = m.group("secret") if "secret" in m.groupdict() else m.group(0)
                if val.startswith("[") and val.endswith("]"):
                    return m.group(0)  # already replaced by an earlier rule
                count += 1
                pre = m.groupdict().get("pre") or ""
                if rule.kind == "secret":
                    secrets.append(val)
                    return f"{pre}[REDACTED:{rule.id}]"
                return pre + self.pseudonym(rule.id.split("-")[1], val)

            text = rule.pattern.sub(_sub, text)
            if count:
                findings.append({"rule_id": rule.id, "rule": rule.name, "kind": rule.kind, "matches": count})
        return text, findings, secrets


def scan_for_secrets(text: str) -> list[str]:
    """Return ids of secret rules that match `text` (used by the policy engine on final payloads)."""
    hits = []
    for rule in SECRET_RULES:
        for m in rule.pattern.finditer(text):
            val = m.group("secret") if "secret" in m.groupdict() else m.group(0)
            if not val.startswith("[REDACTED"):
                hits.append(rule.id)
                break
    return hits


def rules_description() -> list[dict]:
    return [{"id": r.id, "name": r.name, "kind": r.kind, "description": r.description,
             "action": "remove" if r.kind == "secret" else "pseudonymise (HMAC, local key)"} for r in RULES]


Pseudonymiser = Callable[[str], str]
