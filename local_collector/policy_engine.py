"""Data-transfer policy engine — the last gate before anything leaves the customer environment.

A candidate payload is ALLOWED only if every check passes:
  1. Strict schema validation against the shared allow-list contract (unknown fields rejected).
  2. Identity check: tenant_id / collector_id must equal this collector's configuration.
  3. Secret scan: secret-detection rules re-run over the exact serialised bytes.
  4. Canary check: no secret value removed from this window's raw data may appear in the bytes.
Failures are recorded in the egress audit with the reasons; nothing is sent silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from shared.contract import EgressPayload, canonical_json, sha256_hex

from .redaction import scan_for_secrets


@dataclass
class PolicyDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    canonical: str = ""
    sha256: str = ""
    checks: dict = field(default_factory=dict)


class PolicyEngine:
    def __init__(self, tenant_id: str, collector_id: str) -> None:
        self.tenant_id = tenant_id
        self.collector_id = collector_id

    def evaluate(self, payload: dict, canaries: set[str] | None = None) -> PolicyDecision:
        reasons: list[str] = []
        checks = {"schema": "pass", "identity": "pass", "secret_scan": "pass", "canary": "pass"}
        try:
            EgressPayload.model_validate(payload)
        except ValidationError as e:
            checks["schema"] = "fail"
            for err in e.errors():
                loc = ".".join(str(x) for x in err["loc"])
                reasons.append(f"schema: {err['type']} at '{loc}'")
        if payload.get("tenant_id") != self.tenant_id or payload.get("collector_id") != self.collector_id:
            checks["identity"] = "fail"
            reasons.append("identity: tenant_id/collector_id do not match this collector")
        canonical = canonical_json(payload)
        hits = scan_for_secrets(canonical)
        if hits:
            checks["secret_scan"] = "fail"
            reasons.append("secret_scan: matched " + ", ".join(hits))
        leaked = [c for c in (canaries or ()) if len(c) >= 6 and c in canonical]
        if leaked:
            checks["canary"] = "fail"
            reasons.append(f"canary: {len(leaked)} secret value(s) removed from raw data appear in payload")
        return PolicyDecision(allowed=not reasons, reasons=reasons, canonical=canonical,
                              sha256=sha256_hex(canonical), checks=checks)
