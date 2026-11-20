"""Explainable statistical anomaly detection (no ML model is claimed or used).

Method: rolling robust baseline per (tenant, source, metric) — median and MAD of the last
BASELINE_POINTS non-anomalous samples (≈3 min at a 3 s window). The robust z-score is
    z = (x − median) / max(1.4826·MAD, min_sigma)
A point is anomalous when
    (z ≥ 4)  or  (z ≥ 3 for two consecutive points)
AND its absolute deviation from the median ≥ a per-metric minimum (avoids alerting on tiny moves).
Confirmed anomalous points are excluded from future baselines so a sustained fault is not "learned".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.models.models import Anomaly, MetricPoint
from shared.contract import METRIC_LABELS, METRIC_UNITS
from shared.health import metric_state

BASELINE_POINTS = 60
WARMUP_POINTS = 20
Z_IMMEDIATE = 4.0
Z_SUSTAINED = 3.0
ACTIVE_MERGE_S = 30
METHOD = "Rolling robust z-score (median/MAD, last 60 non-anomalous points)"


@dataclass(frozen=True)
class Spec:
    min_delta: float
    min_sigma: float


DETECT: dict[str, Spec] = {
    "cpu_pct": Spec(15, 2.0),
    "mem_pct": Spec(12, 1.0),
    "disk_pct": Spec(8, 0.5),
    "latency_ms": Spec(40, 3.0),
    "packet_loss_pct": Spec(1.0, 0.1),
    "app_response_ms": Spec(300, 25.0),
    "auth_failures": Spec(5, 1.0),
}


def fmt(metric: str, v: float) -> str:
    unit = METRIC_UNITS.get(metric, "")
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "ms":
        return f"{v:,.0f} ms"
    return f"{v:.0f}"


def baseline(db: Session, tenant_id: str, source_id: str, metric: str, before: datetime) -> list[float]:
    rows = db.scalars(
        select(MetricPoint.value).where(
            MetricPoint.tenant_id == tenant_id, MetricPoint.source_id == source_id, MetricPoint.metric == metric,
            MetricPoint.event_time < before, MetricPoint.is_anomalous.is_(False))
        .order_by(MetricPoint.event_time.desc()).limit(BASELINE_POINTS)).all()
    return list(rows)


def robust_stats(values: list[float], spec: Spec) -> tuple[float, float, float]:
    arr = np.asarray(values, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    sigma = max(1.4826 * mad, spec.min_sigma)
    return med, mad, sigma


def evaluate(db: Session, point: MetricPoint, breach_time: datetime, now: datetime,
             thresholds: dict) -> tuple[Anomaly | None, bool]:
    """Score `point` against its baseline. Returns (anomaly, created_new)."""
    spec = DETECT.get(point.metric)
    if spec is None:
        return None, False
    hist = baseline(db, point.tenant_id, point.source_id, point.metric, point.event_time)
    if len(hist) < WARMUP_POINTS:
        return None, False
    med, mad, sigma = robust_stats(hist, spec)
    z = (point.value - med) / sigma
    point.z = round(z, 3)
    prev_z = db.scalar(select(MetricPoint.z).where(
        MetricPoint.tenant_id == point.tenant_id, MetricPoint.source_id == point.source_id,
        MetricPoint.metric == point.metric, MetricPoint.event_time < point.event_time,
        MetricPoint.id != point.id).order_by(MetricPoint.event_time.desc()).limit(1))
    delta = point.value - med
    is_anom = delta >= spec.min_delta and (z >= Z_IMMEDIATE or (z >= Z_SUSTAINED and (prev_z or 0) >= Z_SUSTAINED))
    if not is_anom:
        return None, False
    point.is_anomalous = True   # confirmed anomalous point: shown as a chart marker and excluded from future baselines

    lo, hi = med - 3 * sigma, med + 3 * sigma
    limits = thresholds.get(point.metric)
    state = metric_state(point.value, limits) if limits else "fair"
    severity = "critical" if state == "poor" or z >= 8 else "warning"
    label = METRIC_LABELS.get(point.metric, point.metric)
    dev_pct = (delta / med * 100) if med else 0.0
    delta_txt = f"{delta:.1f} pts" if METRIC_UNITS.get(point.metric) == "%" else fmt(point.metric, delta)
    reason = (f"{label} {fmt(point.metric, point.value)} is {delta_txt} above its rolling median "
              f"{fmt(point.metric, med)} (expected {fmt(point.metric, max(lo, 0))}–{fmt(point.metric, hi)}; "
              f"robust z={z:.1f} over {len(hist)} baseline points).")

    active = db.scalar(select(Anomaly).where(
        Anomaly.tenant_id == point.tenant_id, Anomaly.source_id == point.source_id, Anomaly.metric == point.metric,
        Anomaly.status == "active", Anomaly.last_seen_at >= now - timedelta(seconds=ACTIVE_MERGE_S)))
    if active:
        active.observed = point.value
        active.peak = max(active.peak, point.value)
        active.robust_z = max(active.robust_z, round(z, 2))
        active.points += 1
        active.last_seen_at = now
        active.event_time = max(active.event_time, point.event_time)
        if severity == "critical":
            active.severity = "critical"
        return active, False
    a = Anomaly(tenant_id=point.tenant_id, source_id=point.source_id, metric=point.metric, observed=point.value,
                peak=point.value, baseline_median=round(med, 3), baseline_mad=round(mad, 3), baseline_points=len(hist),
                expected_low=round(max(lo, 0), 3), expected_high=round(hi, 3), robust_z=round(z, 2),
                deviation_pct=round(dev_pct, 1), severity=severity, method=METHOD, reason=reason,
                breach_time=breach_time, event_time=point.event_time, detected_at=now, last_seen_at=now)
    db.add(a)
    db.flush()
    return a, True


def current_baselines(db: Session, tenant_id: str, source_ids: list[str], now: datetime) -> list[dict]:
    out = []
    for sid in source_ids:
        for metric, spec in DETECT.items():
            hist = baseline(db, tenant_id, sid, metric, now + timedelta(seconds=1))
            if not hist:
                continue
            latest = db.scalar(select(MetricPoint).where(
                MetricPoint.tenant_id == tenant_id, MetricPoint.source_id == sid, MetricPoint.metric == metric)
                .order_by(MetricPoint.event_time.desc()).limit(1))
            med, mad, sigma = robust_stats(hist, spec)
            out.append({"source_id": sid, "metric": metric, "label": METRIC_LABELS.get(metric, metric),
                        "unit": METRIC_UNITS.get(metric), "median": round(med, 2), "mad": round(mad, 3),
                        "expected_low": round(max(med - 3 * sigma, 0), 2), "expected_high": round(med + 3 * sigma, 2),
                        "points": len(hist), "warming_up": len(hist) < WARMUP_POINTS,
                        "latest": latest.value if latest else None, "latest_z": latest.z if latest else None})
    return out
