"""Construct and validate the metadata payload allowed across the trust boundary."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

import config
from redact import redact_text

_TOP_LEVEL_FIELDS = {
    "payload_id",
    "tenant_id",
    "collector_id",
    "collector_version",
    "source_id",
    "event_time_utc",
    "source_tz",
    "category",
    "metrics",
    "event",
    "health_state",
    "alert_context",
}
_METRIC_RANGES: dict[str, tuple[float, float]] = {
    "cpu_pct": (0.0, 100.0),
    "mem_pct": (0.0, 100.0),
    "latency_ms": (0.0, 86_400_000.0),
    "packet_loss_pct": (0.0, 100.0),
    "disk_pct": (0.0, 100.0),
    "bandwidth_mbps": (0.0, 1_000_000_000.0),
}
_EVENT_TYPES = {
    "cpu_anomaly",
    "latency_anomaly",
    "mem_anomaly",
    "packet_loss_anomaly",
    "auth_failures",
    "secret_redacted",
}
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_CATEGORIES = {"infra", "network", "security", "collector"}
_HEALTH_STATES = {"Excellent", "Good", "Fair", "Poor"}
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def source_id_for_host(host: str, salt: str | None = None) -> str:
    """Return an 8-hex pseudonymous identifier without exposing the hostname."""
    digest = hashlib.sha256(((config.SOURCE_SALT if salt is None else salt) + host).encode("utf-8")).hexdigest()
    return f"src-{digest[:8]}"


def build_payload(
    host: str,
    event_time_utc: str,
    metrics: dict[str, Any] | None = None,
    *,
    payload_id: str = "",
    category: str = "infra",
    event: dict[str, Any] | None = None,
    health_state: str | None = None,
    alert_context: str | None = None,
) -> dict[str, Any]:
    """Build a candidate payload using configured identity and a pseudonymous source."""
    candidate: dict[str, Any] = {
        "payload_id": payload_id,
        "tenant_id": config.TENANT_ID,
        "collector_id": config.COLLECTOR_ID,
        "collector_version": config.COLLECTOR_VERSION,
        "source_id": source_id_for_host(host),
        "event_time_utc": event_time_utc,
        "source_tz": config.SOURCE_TZ,
        "category": category,
    }
    if metrics is not None:
        candidate["metrics"] = metrics
    if event is not None:
        candidate["event"] = event
    if health_state is not None:
        candidate["health_state"] = health_state
    if alert_context is not None:
        clean_context, _ = redact_text(alert_context)
        candidate["alert_context"] = clean_context[:120]
    return candidate


def _drop(dropped: list[dict[str, str]], field: str, reason: str) -> None:
    """Record one rejected payload field."""
    dropped.append({"field": field, "reason": reason})


def _valid_timestamp(value: Any) -> bool:
    """Check that a timestamp is ISO-8601 UTC with the required Z suffix."""
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
        return True
    except ValueError:
        return False


def _valid_metric_value(name: str, value: Any) -> bool:
    """Validate one approved metric's numeric type and allowed range."""
    bounds = _METRIC_RANGES.get(name)
    if bounds is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return bounds[0] <= value <= bounds[1]


def _clean_metrics(value: Any, dropped: list[dict[str, str]]) -> dict[str, int | float] | None:
    """Keep only recognized metrics with numeric in-range values."""
    if not isinstance(value, dict):
        _drop(dropped, "metrics", "wrong_type")
        return None
    clean: dict[str, int | float] = {}
    for name, metric in value.items():
        field = f"metrics.{name}"
        if name not in _METRIC_RANGES:
            _drop(dropped, field, "unknown_field")
        elif not _valid_metric_value(name, metric):
            _drop(dropped, field, "wrong_type_or_out_of_range")
        else:
            clean[name] = metric
    return clean


def _clean_event(value: Any, dropped: list[dict[str, str]]) -> dict[str, Any] | None:
    """Keep only approved event type, severity, and positive count fields."""
    if not isinstance(value, dict):
        _drop(dropped, "event", "wrong_type")
        return None
    clean: dict[str, Any] = {}
    accepted = {"type", "severity", "count"}
    for name in value:
        if name not in accepted:
            _drop(dropped, f"event.{name}", "unknown_field")
    event_type = value.get("type")
    if isinstance(event_type, str) and event_type in _EVENT_TYPES:
        clean["type"] = event_type
    elif "type" in value:
        _drop(dropped, "event.type", "invalid_value_or_wrong_type")
    severity = value.get("severity")
    if isinstance(severity, str) and severity in _SEVERITIES:
        clean["severity"] = severity
    elif "severity" in value:
        _drop(dropped, "event.severity", "invalid_value_or_wrong_type")
    count = value.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 1:
        clean["count"] = count
    elif "count" in value:
        _drop(dropped, "event.count", "wrong_type_or_out_of_range")
    return clean


def apply_allowlist(candidate: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return a deny-by-default clean payload and descriptions of every dropped field."""
    clean: dict[str, Any] = {}
    dropped: list[dict[str, str]] = []
    if not isinstance(candidate, dict):
        return clean, [{"field": "payload", "reason": "wrong_type"}]

    for field in candidate:
        if field not in _TOP_LEVEL_FIELDS:
            _drop(dropped, str(field), "unknown_field")

    string_fields = (
        "payload_id",
        "tenant_id",
        "collector_id",
        "collector_version",
        "source_id",
        "source_tz",
    )
    for field in string_fields:
        if field not in candidate:
            continue
        value = candidate[field]
        if isinstance(value, str):
            clean[field] = value
        else:
            _drop(dropped, field, "wrong_type")

    timestamp = candidate.get("event_time_utc")
    if _valid_timestamp(timestamp):
        clean["event_time_utc"] = timestamp
    elif "event_time_utc" in candidate:
        _drop(dropped, "event_time_utc", "invalid_utc_iso8601")

    category = candidate.get("category")
    if isinstance(category, str) and category in _CATEGORIES:
        clean["category"] = category
    elif "category" in candidate:
        _drop(dropped, "category", "invalid_value_or_wrong_type")

    if "metrics" in candidate:
        metrics = _clean_metrics(candidate["metrics"], dropped)
        if metrics is not None:
            clean["metrics"] = metrics

    if "event" in candidate:
        event = _clean_event(candidate["event"], dropped)
        if event is not None:
            clean["event"] = event

    health = candidate.get("health_state")
    if isinstance(health, str) and health in _HEALTH_STATES:
        clean["health_state"] = health
    elif "health_state" in candidate:
        _drop(dropped, "health_state", "invalid_value_or_wrong_type")

    context = candidate.get("alert_context")
    if isinstance(context, str):
        redacted, _ = redact_text(context)
        clean["alert_context"] = redacted[:120]
    elif "alert_context" in candidate:
        _drop(dropped, "alert_context", "wrong_type")

    return clean, dropped
