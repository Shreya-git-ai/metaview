"""Tests for payload construction and strict allow-list enforcement."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from allowlist import apply_allowlist, build_payload


def _candidate(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "payload_id": "col-01-000001",
        "tenant_id": "city-hospital",
        "collector_id": "col-01",
        "collector_version": "0.1.0",
        "source_id": "src-12345678",
        "event_time_utc": "2026-11-20T10:20:15Z",
        "source_tz": "Asia/Kolkata",
        "category": "infra",
        "metrics": {"cpu_pct": 34.0, "latency_ms": 22},
    }
    payload.update(updates)
    return payload


def test_unknown_field_is_dropped_and_reported() -> None:
    clean, dropped = apply_allowlist(_candidate(password="raw-secret"))
    assert "password" not in clean
    assert {"field": "password", "reason": "unknown_field"} in dropped


def test_wrong_type_and_out_of_range_values_are_dropped() -> None:
    clean, dropped = apply_allowlist(
        _candidate(
            metrics={"cpu_pct": 101, "mem_pct": "high", "latency_ms": 20},
            event={"type": "auth_failures", "severity": "medium", "count": "8"},
        )
    )
    assert clean["metrics"] == {"latency_ms": 20}
    assert "count" not in clean["event"]
    fields = {item["field"] for item in dropped}
    assert {"metrics.cpu_pct", "metrics.mem_pct", "event.count"}.issubset(fields)


def test_source_id_is_pseudonymous_and_eight_hex_characters() -> None:
    host = "srv-app-01"
    payload = build_payload(host, "2026-11-20T10:20:15Z", {"cpu_pct": 34.0})
    clean, _ = apply_allowlist(payload)
    expected = hashlib.sha256((config.SOURCE_SALT + host).encode()).hexdigest()[:8]
    assert clean["source_id"] == f"src-{expected}"
    serialized = str(clean)
    for hostname in ("srv-app-01", "srv-db-01", "router-01"):
        assert hostname not in serialized


def test_alert_context_is_redacted_then_truncated_to_120_characters() -> None:
    candidate = _candidate(alert_context="password=secret " + ("x" * 130))
    clean, _ = apply_allowlist(candidate)
    context = clean["alert_context"]
    assert len(context) == 120
    assert "secret" not in context
    assert "[REDACTED]" in context


def test_event_time_must_be_utc_iso8601_ending_in_z() -> None:
    valid, _ = apply_allowlist(_candidate())
    invalid, dropped = apply_allowlist(_candidate(event_time_utc="2026-11-20T10:20:15+00:00"))
    assert valid["event_time_utc"].endswith("Z")
    assert "event_time_utc" not in invalid
    assert any(item["field"] == "event_time_utc" for item in dropped)


def test_build_payload_redacts_and_truncates_context() -> None:
    candidate = build_payload(
        "srv-db-01",
        "2026-11-20T10:20:15Z",
        event={"type": "secret_redacted", "severity": "medium", "count": 1},
        category="security",
        alert_context="token=secret " + ("a" * 130),
    )
    clean, _ = apply_allowlist(candidate)
    assert clean["event"]["type"] == "secret_redacted"
    assert len(clean["alert_context"]) == 120
    assert "secret" not in clean["alert_context"]
