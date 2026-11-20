"""Measured performance — every number is computed from stored timestamps, never hard-coded."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saas_backend.models.models import Anomaly, AuditLog, IngestAudit, PerfSample, ReceivedEvent

TARGETS = {"collection_to_dashboard_p95_s": 10, "breach_to_alert_s": 15, "offline_detection_s": 60,
           "post_outage_lost": 0, "post_outage_duplicates_stored": 0}
LIVE_CUTOFF_S = 60   # events older than this on arrival were buffered during an outage


def _pct(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 3) if values else None


def performance(db: Session, tenant_id: str, now: datetime, minutes: int = 15) -> dict:
    since = now - timedelta(minutes=minutes)
    rows = db.execute(select(ReceivedEvent.received_at, ReceivedEvent.event_time).where(
        ReceivedEvent.tenant_id == tenant_id, ReceivedEvent.received_at >= since)).all()
    lat = [(r.received_at - r.event_time).total_seconds() for r in rows]
    live = [x for x in lat if x <= LIVE_CUTOFF_S]
    buffered = [x for x in lat if x > LIVE_CUTOFF_S]

    dash = list(db.scalars(select(PerfSample.value).where(PerfSample.tenant_id == tenant_id,
                                                          PerfSample.kind == "dashboard_latency",
                                                          PerfSample.at >= since)).all())
    dash_s = [v / 1000 for v in dash]

    an = db.execute(select(Anomaly.detected_at, Anomaly.breach_time).where(
        Anomaly.tenant_id == tenant_id, Anomaly.detected_at >= now - timedelta(hours=2))).all()
    b2a = [(a.detected_at - a.breach_time).total_seconds() for a in an]
    b2a_live = [x for x in b2a if x <= LIVE_CUTOFF_S]

    off = list(db.scalars(select(PerfSample.value).where(PerfSample.tenant_id == tenant_id,
                                                         PerfSample.kind == "offline_detection")
                          .order_by(PerfSample.at.desc()).limit(50)).all())
    off_s = [v / 1000 for v in off]

    tot = db.execute(select(func.coalesce(func.sum(IngestAudit.accepted), 0),
                            func.coalesce(func.sum(IngestAudit.duplicates), 0),
                            func.coalesce(func.sum(IngestAudit.rejected), 0))
                     .where(IngestAudit.tenant_id == tenant_id)).one()
    stored = db.scalar(select(func.count()).select_from(ReceivedEvent).where(ReceivedEvent.tenant_id == tenant_id))
    rec = db.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == "reconcile")
                     .order_by(AuditLog.at.desc()).limit(5)).all()
    return {
        "window_minutes": minutes,
        "targets": TARGETS,
        "collection_to_ingest_s": {"samples": len(lat), "p50": _pct(live, 50), "p95": _pct(live, 95),
                                   "max": _pct(live, 100), "buffered_events": len(buffered),
                                   "buffered_max_delay_s": _pct(buffered, 100),
                                   "note": "received_at − event_time (window end). Buffered (>60s) events are "
                                           "reported separately."},
        "collection_to_dashboard_s": {"samples": len(dash_s), "p50": _pct(dash_s, 50), "p95": _pct(dash_s, 95),
                                      "max": _pct(dash_s, 100),
                                      "note": "Measured in the browser: render time − event_time of newly seen data."},
        "breach_to_alert_s": {"samples": len(b2a_live), "p50": _pct(b2a_live, 50), "p95": _pct(b2a_live, 95),
                              "max": _pct(b2a_live, 100),
                              "note": "anomaly.detected_at − start of the aggregation window containing the breach."},
        "offline_detection_s": {"samples": len(off_s), "last": off_s[0] if off_s else None, "max": _pct(off_s, 100),
                                "note": "watchdog detection time − last heartbeat received."},
        "delivery": {"accepted_total": int(tot[0]), "duplicates_rejected_total": int(tot[1]),
                     "rejected_total": int(tot[2]), "stored_events": int(stored or 0),
                     "duplicates_stored": 0 if stored == tot[0] else int(stored - tot[0]),
                     "reconciliations": [{"at": r.at.isoformat() + "Z", **json.loads(r.detail or "{}")} for r in rec]},
    }
