"""Four-state health classification shared by the collector (local analysis) and SaaS.

Thresholds are (excellent_below, good_below, fair_below): a value below the first limit is
EXCELLENT, below the second GOOD, below the third FAIR, otherwise POOR. The worst metric wins.
SaaS thresholds are configurable per tenant (Settings → Thresholds).
"""
from __future__ import annotations

from typing import Mapping

HEALTH_ORDER = ["excellent", "good", "fair", "poor"]

DEFAULT_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "cpu_pct": (70, 80, 90),
    "mem_pct": (75, 85, 92),
    "disk_pct": (75, 85, 92),
    "latency_ms": (60, 120, 250),
    "packet_loss_pct": (0.5, 1.5, 4.0),
    "app_response_ms": (400, 900, 2500),
    "auth_failures": (3, 6, 15),
}

_STATE_FOR_INDEX = {0: "excellent", 1: "good", 2: "fair", 3: "poor"}


def metric_state(value: float, limits: tuple[float, float, float]) -> str:
    for i, lim in enumerate(limits):
        if value < lim:
            return _STATE_FOR_INDEX[i]
    return "poor"


def worse(a: str, b: str) -> str:
    return a if HEALTH_ORDER.index(a) >= HEALTH_ORDER.index(b) else b


def downgrade(state: str, levels: int = 1) -> str:
    return HEALTH_ORDER[min(len(HEALTH_ORDER) - 1, HEALTH_ORDER.index(state) + levels)]


def classify(metrics: Mapping[str, object],
             thresholds: Mapping[str, tuple[float, float, float]] | None = None) -> tuple[str, str | None, str]:
    """Return (state, triggering_metric, human-readable reason)."""
    thresholds = thresholds or DEFAULT_THRESHOLDS
    if metrics.get("app_up") is False:
        return "poor", "app_up", "Application health check failing (app_up=false)"
    state, trigger, reason = "excellent", None, "All metrics within expected limits"
    for metric, limits in thresholds.items():
        v = metrics.get(metric)
        if v is None:
            continue
        s = metric_state(float(v), limits)
        if HEALTH_ORDER.index(s) > HEALTH_ORDER.index(state):
            state, trigger = s, metric
            idx = HEALTH_ORDER.index(s)
            reason = f"{metric}={float(v):.2f} ≥ {HEALTH_ORDER[idx - 1]} limit {limits[idx - 1]}"
    return state, trigger, reason


def severity_for(state: str) -> str:
    return {"poor": "critical", "fair": "warning"}.get(state, "info")
