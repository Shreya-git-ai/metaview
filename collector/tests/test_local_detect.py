"""Deterministic tests for local telemetry detectors."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_detect import LocalDetector, health_hint


def _ts(second: int) -> str:
    return (datetime(2026, 11, 20, tzinfo=timezone.utc) + timedelta(seconds=second)).isoformat().replace("+00:00", "Z")


def _warm(detector: LocalDetector, samples: int = 20) -> None:
    for index in range(samples):
        detector.process("srv-app-01", {"cpu_pct": 35.0}, None, _ts(index))


def test_no_metric_detection_before_warmup() -> None:
    detector = LocalDetector(baseline_min_samples=20)
    for index in range(19):
        events = detector.process("srv-app-01", {"cpu_pct": 35.0}, None, _ts(index))
        assert events == []
    assert detector.process("srv-app-01", {"cpu_pct": 90.0}, None, _ts(19)) == []


def test_cpu_jump_after_warmup_emits_z_score_context() -> None:
    detector = LocalDetector(baseline_min_samples=20, anomaly_z=3.0)
    _warm(detector)
    events = detector.process("srv-app-01", {"cpu_pct": 90.0}, None, _ts(21))
    assert len(events) == 1
    assert events[0]["type"] == "cpu_anomaly"
    assert "sigma" in events[0]["alert_context"]
    assert "55.0" in events[0]["alert_context"]


def test_anomaly_does_not_poison_baseline() -> None:
    detector = LocalDetector(baseline_min_samples=20, anomaly_z=3.0)
    _warm(detector)
    detector.process("srv-app-01", {"cpu_pct": 90.0}, None, _ts(21))
    followup = detector.process("srv-app-01", {"cpu_pct": 91.0}, None, _ts(22))
    assert len(followup) == 1
    assert followup[0]["type"] == "cpu_anomaly"
    returned = detector.process("srv-app-01", {"cpu_pct": 35.0}, None, _ts(23))
    assert returned == []
    assert detector.process("srv-app-01", {"cpu_pct": 90.0}, None, _ts(24))[0]["type"] == "cpu_anomaly"


def test_eight_failed_logins_emit_one_safe_event() -> None:
    detector = LocalDetector(failed_login_threshold=5)
    found = []
    for index in range(8):
        found.extend(detector.process("srv-app-01", {}, "FAILED login user=admin ip=10.0.0.7", _ts(index)))
    assert len(found) == 1
    event = found[0]
    assert event["type"] == "auth_failures"
    assert event["count"] == 5
    assert event["severity"] == "medium"
    assert "admin" not in str(event)
    assert "10.0.0.7" not in str(event)


def test_second_failure_burst_after_event_time_cooldown_emits_again() -> None:
    detector = LocalDetector(failed_login_threshold=5)
    emitted = []
    for index in range(5):
        emitted.extend(detector.process("host", {}, "FAILED login user=x", _ts(index)))
    for index in range(5):
        emitted.extend(detector.process("host", {}, "FAILED login user=y", _ts(70 + index)))
    assert [event["type"] for event in emitted] == ["auth_failures", "auth_failures"]


def test_health_hint_cases() -> None:
    assert health_hint({"cpu_pct": 91}) == "Poor"
    assert health_hint({"cpu_pct": 71}) == "Fair"
    assert health_hint({"cpu_pct": 40}, [{"type": "cpu_anomaly", "severity": "low"}]) == "Good"
    assert health_hint({"cpu_pct": 40}) == "Excellent"
    assert health_hint({"cpu_pct": 40}, [{"severity": "critical"}]) == "Poor"


def test_db_down_requires_two_records_and_recovers_once() -> None:
    detector = LocalDetector()
    down_one = detector.process("postgres", {"db_up": 0}, None, _ts(0))
    down_two = detector.process("postgres", {"db_up": 0}, None, _ts(1))
    down_three = detector.process("postgres", {"db_up": 0}, None, _ts(2))
    recovered = detector.process("postgres", {"db_up": 1}, None, _ts(3))
    still_up = detector.process("postgres", {"db_up": 1}, None, _ts(4))
    db_events = [event for event in down_one + down_two + down_three + recovered + still_up if event["type"].startswith("db_")]
    assert [event["type"] for event in db_events] == ["db_unavailable", "db_recovered"]
    assert db_events[0]["severity"] == "high"
    assert db_events[0]["affected_services"] == ["postgres", "banking-app", "payment-service"]
    assert db_events[1]["severity"] == "low"
    assert db_events[1]["alert_context"] == "database recovered"
    assert "postgres" not in db_events[0]["alert_context"]


def test_service_down_saturation_and_error_rate_detectors() -> None:
    detector = LocalDetector()
    assert detector.process("banking-app", {"up": 0}, None, _ts(0)) == []
    down = detector.process("banking-app", {"up": 0}, None, _ts(1))
    assert len(down) == 1 and down[0]["type"] == "service_down"
    assert down[0]["affected_services"] == ["banking-app"]
    saturated = detector.process("postgres", {"db_conn_active": 96, "db_conn_max": 100}, None, _ts(2))
    assert len(saturated) == 1 and saturated[0]["severity"] == "high"
    error_events = []
    for index in range(5):
        error_events.extend(detector.process("payment-service", {"req_count": 100, "err_count": 10}, None, _ts(index + 3)))
    spikes = [event for event in error_events if event["type"] == "error_rate_spike"]
    assert len(spikes) == 1 and spikes[0]["severity"] == "medium"
