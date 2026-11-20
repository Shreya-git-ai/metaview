"""Local metric anomaly, authentication failure, and health hint detectors."""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import config

_METRIC_EVENT_TYPES = {
    "cpu_pct": ("cpu_anomaly", "cpu"),
    "latency_ms": ("latency_anomaly", "latency"),
    "mem_pct": ("mem_anomaly", "mem"),
    "packet_loss_pct": ("packet_loss_anomaly", "packet_loss"),
}
_FAILED_LOGIN = re.compile(r"FAILED\s+login", re.IGNORECASE)


def parse_event_time(value: str | datetime) -> datetime:
    """Parse an event timestamp as an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_number(value: float) -> str:
    """Format detector values compactly for safe human-readable context."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


class LocalDetector:
    """Maintain rolling baselines and event-time failed-login windows."""

    def __init__(
        self,
        baseline_min_samples: int | None = None,
        anomaly_z: float | None = None,
        failed_login_threshold: int | None = None,
        baseline_window: int = 60,
        failure_window_s: float = 60.0,
        cooldown_s: float = 60.0,
    ) -> None:
        """Create detector state, defaulting thresholds from collector config."""
        self.baseline_min_samples = baseline_min_samples if baseline_min_samples is not None else config.BASELINE_MIN_SAMPLES
        self.anomaly_z = anomaly_z if anomaly_z is not None else config.ANOMALY_Z
        self.failed_login_threshold = failed_login_threshold if failed_login_threshold is not None else config.FAILED_LOGIN_THRESHOLD
        self.baseline_window = baseline_window
        self.failure_window_s = failure_window_s
        self.cooldown_s = cooldown_s
        self._baselines: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=self.baseline_window))
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._last_auth_event: dict[str, float] = {}
        self._db_down_consecutive = 0
        self._db_down_active = False
        self._service_down_consecutive: dict[str, int] = defaultdict(int)
        self._service_down_active: set[str] = set()
        self._enterprise_last_event: dict[str, float] = {}
        self._error_history: dict[str, deque[tuple[float, float]]] = defaultdict(lambda: deque(maxlen=5))

    def _cooldown_ready(self, key: str, timestamp: float) -> bool:
        """Check an event-time cooldown and record its firing timestamp."""
        last = self._enterprise_last_event.get(key)
        if last is not None and timestamp - last < self.cooldown_s:
            return False
        self._enterprise_last_event[key] = timestamp
        return True

    @staticmethod
    def _enterprise_event(
        event_type: str,
        severity: str,
        context: str,
        affected_services: list[str],
        category: str = "infra",
    ) -> dict[str, Any]:
        """Build a service event with only approved non-identifying context."""
        return {
            "type": event_type,
            "severity": severity,
            "count": 1,
            "alert_context": context,
            "category": category,
            "affected_services": affected_services,
        }

    def _enterprise_events(
        self,
        host: str,
        metrics: dict[str, Any],
        event_time: datetime,
    ) -> list[dict[str, Any]]:
        """Detect service, database availability, saturation, and error-rate events."""
        events: list[dict[str, Any]] = []
        timestamp = event_time.timestamp()
        if host == "postgres" and metrics.get("db_up") in (0, 0.0):
            self._db_down_consecutive += 1
            if self._db_down_consecutive >= 2 and not self._db_down_active and self._cooldown_ready("db_unavailable", timestamp):
                self._db_down_active = True
                events.append(
                    self._enterprise_event(
                        "db_unavailable",
                        "high",
                        "database unavailable",
                        ["postgres", "banking-app", "payment-service"],
                    )
                )
        elif host == "postgres" and metrics.get("db_up") in (1, 1.0):
            self._db_down_consecutive = 0
            if self._db_down_active:
                self._db_down_active = False
                events.append(
                    self._enterprise_event(
                        "db_recovered",
                        "low",
                        "database recovered",
                        ["postgres", "banking-app", "payment-service"],
                    )
                )

        if host in {"banking-app", "payment-service"}:
            up = metrics.get("up")
            if up in (0, 0.0):
                self._service_down_consecutive[host] += 1
                cooldown_key = f"service_down:{host}"
                if (
                    self._service_down_consecutive[host] >= 2
                    and host not in self._service_down_active
                    and self._cooldown_ready(cooldown_key, timestamp)
                ):
                    self._service_down_active.add(host)
                    events.append(self._enterprise_event("service_down", "high", "service unavailable", [host]))
            elif up in (1, 1.0):
                self._service_down_consecutive[host] = 0
                self._service_down_active.discard(host)

        active = metrics.get("db_conn_active")
        maximum = metrics.get("db_conn_max")
        if (
            isinstance(active, (int, float))
            and not isinstance(active, bool)
            and isinstance(maximum, (int, float))
            and not isinstance(maximum, bool)
            and maximum > 0
        ):
            ratio = active / maximum
            if ratio >= 0.8 and self._cooldown_ready("db_conn_saturation", timestamp):
                severity = "high" if ratio >= 0.95 else "medium"
                events.append(
                    self._enterprise_event(
                        "db_conn_saturation",
                        severity,
                        f"database connections at {ratio * 100:.1f}% of capacity",
                        ["postgres"],
                    )
                )

        req_count = metrics.get("req_count")
        err_count = metrics.get("err_count")
        if (
            isinstance(req_count, (int, float))
            and not isinstance(req_count, bool)
            and isinstance(err_count, (int, float))
            and not isinstance(err_count, bool)
            and req_count >= 0
            and err_count >= 0
        ):
            history = self._error_history[host]
            history.append((float(req_count), float(err_count)))
            total_requests = sum(item[0] for item in history)
            total_errors = sum(item[1] for item in history)
            rate = total_errors / max(total_requests, 1.0)
            if rate >= 0.05 and self._cooldown_ready(f"error_rate_spike:{host}", timestamp):
                severity = "high" if rate >= 0.2 else "medium"
                affected = [host] if host in {"banking-app", "payment-service", "postgres"} else []
                events.append(
                    self._enterprise_event(
                        "error_rate_spike",
                        severity,
                        f"error rate {rate * 100:.1f}% over recent requests",
                        affected,
                    )
                )
        return events

    def _metric_events(self, host: str, metrics: dict[str, Any]) -> list[dict[str, Any]]:
        """Evaluate metrics against their local rolling baselines."""
        events: list[dict[str, Any]] = []
        for metric, (event_type, label) in _METRIC_EVENT_TYPES.items():
            value = metrics.get(metric)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            samples = self._baselines[(host, metric)]
            if len(samples) >= self.baseline_min_samples:
                mean = statistics.fmean(samples)
                std = statistics.pstdev(samples)
                scale = max(std, 1.0)
                z_score = (float(value) - mean) / scale
                if abs(z_score) >= self.anomaly_z:
                    direction = "above" if z_score >= 0 else "below"
                    events.append(
                        {
                            "type": event_type,
                            "severity": "high",
                            "count": 1,
                            "alert_context": (
                                f"{label} {abs(z_score):.1f} sigma {direction} local baseline "
                                f"(mean {_format_number(mean)}, now {_format_number(float(value))})"
                            ),
                            "category": "infra" if metric != "packet_loss_pct" else "network",
                        }
                    )
                    continue
            samples.append(float(value))
        return events

    def _failure_events(self, host: str, event_time: datetime, log_line: str | None) -> list[dict[str, Any]]:
        """Count failed logins per host using event-time windows and cooldowns."""
        if not isinstance(log_line, str) or not _FAILED_LOGIN.search(log_line):
            return []
        timestamp = event_time.timestamp()
        failures = self._failures[host]
        failures.append(timestamp)
        cutoff = timestamp - self.failure_window_s
        while failures and failures[0] < cutoff:
            failures.popleft()
        count = len(failures)
        last_event = self._last_auth_event.get(host)
        if count < self.failed_login_threshold or (last_event is not None and timestamp - last_event < self.cooldown_s):
            return []
        self._last_auth_event[host] = timestamp
        return [
            {
                "type": "auth_failures",
                "severity": "high" if count >= 10 else "medium",
                "count": count,
                "alert_context": f"{count} failed logins observed within {int(self.failure_window_s)} seconds",
                "category": "security",
            }
        ]

    def process(
        self,
        host: str,
        metrics: dict[str, Any] | None,
        log_line: str | None,
        event_time_utc: str | datetime,
    ) -> list[dict[str, Any]]:
        """Process one record and return detector events without raw identifiers."""
        event_time = parse_event_time(event_time_utc)
        events: list[dict[str, Any]] = []
        if isinstance(metrics, dict):
            events.extend(self._metric_events(host, metrics))
            events.extend(self._enterprise_events(host, metrics, event_time))
        events.extend(self._failure_events(host, event_time, log_line))
        return events

    def detect(
        self,
        host: str,
        metrics: dict[str, Any] | None,
        log_line: str | None,
        event_time_utc: str | datetime,
    ) -> list[dict[str, Any]]:
        """Alias for process, convenient for a single-record detector call."""
        return self.process(host, metrics, log_line, event_time_utc)


def health_hint(metrics: dict[str, Any] | None, events: list[dict[str, Any]] | None = None) -> str:
    """Return a local health hint from metrics and detector event severity."""
    values = metrics if isinstance(metrics, dict) else {}
    event_list = events or []
    severity_critical = any(event.get("severity") == "critical" for event in event_list)
    cpu = values.get("cpu_pct")
    if (isinstance(cpu, (int, float)) and not isinstance(cpu, bool) and cpu > 90) or severity_critical:
        return "Poor"
    event_types = {event.get("type") for event in event_list}
    latency = values.get("latency_ms")
    packet_loss = values.get("packet_loss_pct")
    if (
        (isinstance(cpu, (int, float)) and not isinstance(cpu, bool) and cpu > 70)
        or (isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency > 80)
        or (isinstance(packet_loss, (int, float)) and not isinstance(packet_loss, bool) and packet_loss > 2)
        or "auth_failures" in event_types
    ):
        return "Fair"
    if event_list:
        return "Good"
    return "Excellent"


def detect_events(
    host: str,
    metrics: dict[str, Any] | None,
    log_line: str | None,
    event_time_utc: str | datetime,
    detector: LocalDetector,
) -> list[dict[str, Any]]:
    """Process one event with a caller-owned detector instance."""
    return detector.process(host, metrics, log_line, event_time_utc)
