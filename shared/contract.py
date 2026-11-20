"""NEXORA egress contract — the ONLY data shape allowed to leave a customer environment.

Both tiers import this module:
  * the Local Collector validates every outgoing payload against it (policy engine), and
  * the SaaS backend re-validates every received payload against it (ingestion).

Any field not declared here is rejected (``extra="forbid"``).  Free text is not allowed:
alert context is an enumerated ``context_code`` plus numeric ``context_params`` only.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"

# --- Allow-listed metric keys with physical bounds --------------------------------------
METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    "cpu_pct": (0, 100),
    "mem_pct": (0, 100),
    "disk_pct": (0, 100),
    "net_mbps": (0, 100_000),
    "latency_ms": (0, 60_000),
    "packet_loss_pct": (0, 100),
    "app_response_ms": (0, 600_000),
    "auth_failures": (0, 1_000_000),
    "auth_distinct_accounts": (0, 1_000_000),
    "auth_distinct_sources": (0, 1_000_000),
}
METRIC_UNITS = {
    "cpu_pct": "%", "mem_pct": "%", "disk_pct": "%", "net_mbps": "Mbps", "latency_ms": "ms",
    "packet_loss_pct": "%", "app_response_ms": "ms", "auth_failures": "count/window",
    "auth_distinct_accounts": "count", "auth_distinct_sources": "count",
}
METRIC_LABELS = {
    "cpu_pct": "CPU", "mem_pct": "Memory", "disk_pct": "Disk", "net_mbps": "Bandwidth",
    "latency_ms": "Latency", "packet_loss_pct": "Packet loss", "app_response_ms": "App response",
    "auth_failures": "Failed logins", "auth_distinct_accounts": "Distinct accounts",
    "auth_distinct_sources": "Distinct sources",
}

CONTEXT_CODES = (
    "HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK", "HIGH_LATENCY", "PACKET_LOSS",
    "SLOW_APP_RESPONSE", "APP_DOWN", "AUTH_FAILURE_BURST", "LOCAL_ANOMALY",
)
ContextCode = Literal[
    "HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK", "HIGH_LATENCY", "PACKET_LOSS",
    "SLOW_APP_RESPONSE", "APP_DOWN", "AUTH_FAILURE_BURST", "LOCAL_ANOMALY",
]
HealthState = Literal["excellent", "good", "fair", "poor"]
Severity = Literal["info", "warning", "critical"]
Category = Literal["metrics", "security_summary", "local_alert"]
SourceRole = Literal["web", "app", "db", "api", "network", "host"]

HEALTH_ORDER = ["excellent", "good", "fair", "poor"]

_UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_TS_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Canonical UTC timestamp format used on the wire: 2026-11-20T10:15:00.123Z"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    cpu_pct: Optional[float] = Field(None, ge=0, le=100)
    mem_pct: Optional[float] = Field(None, ge=0, le=100)
    disk_pct: Optional[float] = Field(None, ge=0, le=100)
    net_mbps: Optional[float] = Field(None, ge=0, le=100_000)
    latency_ms: Optional[float] = Field(None, ge=0, le=60_000)
    packet_loss_pct: Optional[float] = Field(None, ge=0, le=100)
    app_response_ms: Optional[float] = Field(None, ge=0, le=600_000)
    app_up: Optional[bool] = None
    auth_failures: Optional[int] = Field(None, ge=0, le=1_000_000)
    auth_distinct_accounts: Optional[int] = Field(None, ge=0, le=1_000_000)
    auth_distinct_sources: Optional[int] = Field(None, ge=0, le=1_000_000)


RedactionRuleId = Literal["R-PKEY", "R-URLCRED", "R-BEARER", "R-JWT", "R-AWSKEY", "R-APIKEY", "R-PASSWORD",
                          "R-KV-SECRET", "R-EMAIL", "R-IPV4", "R-USER"]


class Processing(BaseModel):
    """Privacy-safe evidence of local processing for this window: counts and enumerated rule ids only —
    never the values that were removed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_records: int = Field(ge=0, le=1_000_000)
    fields_dropped: int = Field(ge=0, le=1_000_000)
    secrets_redacted: int = Field(ge=0, le=1_000_000)
    identifiers_pseudonymised: int = Field(ge=0, le=1_000_000)
    rules_triggered: list[RedactionRuleId] = Field(default_factory=list, max_length=11)


class EgressPayload(BaseModel):
    """The approved-metadata envelope. One per source per aggregation window."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    event_id: str = Field(pattern=_UUID_RE)
    tenant_id: str = Field(pattern=r"^tenant-[a-z0-9-]{1,32}$")
    collector_id: str = Field(pattern=r"^col-[a-z0-9-]{1,40}$")
    source_id: str = Field(pattern=r"^src-[0-9a-f]{12}$")
    source_role: SourceRole
    category: Category
    window_start: str = Field(pattern=_TS_RE)
    event_time: str = Field(pattern=_TS_RE)
    metrics: Metrics
    health_state: HealthState
    severity: Severity
    context_code: Optional[ContextCode] = None
    context_params: dict[str, float | int] = Field(default_factory=dict, max_length=6)
    processing: Optional[Processing] = None   # optional (schema 1.0 payloads without it remain valid)

    @field_validator("context_params")
    @classmethod
    def _params_keys(cls, v: dict) -> dict:
        for k, val in v.items():
            if not re.fullmatch(r"[a-z_]{1,24}", k):
                raise ValueError(f"context_params key '{k}' not allowed")
            if isinstance(val, bool):
                raise ValueError("context_params values must be numeric")
        return v

    @field_validator("window_start", "event_time")
    @classmethod
    def _valid_ts(cls, v: str) -> str:
        parse_iso(v)
        return v


class HeartbeatPayload(BaseModel):
    """Allow-listed collector heartbeat (also strictly validated)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    tenant_id: str = Field(pattern=r"^tenant-[a-z0-9-]{1,32}$")
    collector_id: str = Field(pattern=r"^col-[a-z0-9-]{1,40}$")
    sent_at: str = Field(pattern=_TS_RE)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    uptime_s: int = Field(ge=0)
    agent_cpu_pct: float = Field(ge=0, le=100 * 256)
    agent_mem_mb: float = Field(ge=0)
    queue_pending: int = Field(ge=0)
    queue_retrying: int = Field(ge=0)
    queue_delivered: int = Field(ge=0)
    queue_failed: int = Field(ge=0)
    policy_blocked: int = Field(ge=0)
    config_hash: str = Field(pattern=r"^[0-9a-f]{12}$")
    # optional cumulative local-processing counters (counts only; no content)
    raw_records_total: int = Field(0, ge=0)
    fields_dropped_total: int = Field(0, ge=0)
    secrets_redacted_total: int = Field(0, ge=0)
    identifiers_pseudonymised_total: int = Field(0, ge=0)
    payloads_approved_total: int = Field(0, ge=0)
    last_successful_send_at: Optional[str] = Field(None, pattern=_TS_RE)


ALLOWED_PAYLOAD_FIELDS = list(EgressPayload.model_fields.keys())
ALLOWED_METRIC_FIELDS = list(Metrics.model_fields.keys())
ALLOWED_PROCESSING_FIELDS = list(Processing.model_fields.keys())


def canonical_json(obj: dict) -> str:
    """Deterministic serialisation — the exact bytes hashed for egress proof."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_description() -> dict:
    """Machine-readable description of the allow-list (shown in both consoles)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "payload_fields": ALLOWED_PAYLOAD_FIELDS,
        "metric_fields": ALLOWED_METRIC_FIELDS,
        "processing_fields": ALLOWED_PROCESSING_FIELDS,
        "metric_bounds": METRIC_BOUNDS,
        "metric_units": METRIC_UNITS,
        "context_codes": list(CONTEXT_CODES),
        "unknown_fields": "rejected (extra=forbid)",
        "free_text": "not permitted — context is an enumerated code + numeric params",
    }
