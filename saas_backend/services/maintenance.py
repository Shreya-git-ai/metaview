"""Background maintenance: heartbeat watchdog, stale-data health, anomaly clearing, incident auto-resolve."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.correlation.engine import AUTO_RESOLVE_QUIET_S, open_collector_incident, resolve
from saas_backend.models.models import (Anomaly, AuditLog, Collector, CollectorHeartbeat, HealthTransition, Incident,
                                        PerfSample, Source)
from saas_backend.services.health import evaluate_source, tenant_thresholds

ANOMALY_CLEAR_S = 30


def mark_offline(db: Session, col: Collector, now: datetime, timeout_s: float) -> None:
    lag = (now - col.last_heartbeat_at).total_seconds() if col.last_heartbeat_at else None
    prev = col.status
    col.status, col.status_changed_at = "offline", now
    col.status_reason = f"No heartbeat for {lag:.0f}s (timeout {timeout_s:.0f}s)" if lag is not None else "never seen"
    db.add(HealthTransition(tenant_id=col.tenant_id, entity_type="collector", entity_id=col.id,
                            prev_state="excellent" if prev == "online" else None, new_state="poor",
                            trigger_metric="heartbeat", reason=col.status_reason, at=now))
    if lag is not None:
        db.add(PerfSample(tenant_id=col.tenant_id, kind="offline_detection", value=lag * 1000,
                          detail=json.dumps({"collector_id": col.id})))
    db.add(AuditLog(actor="watchdog", tenant_id=col.tenant_id, action="collector_offline", target=col.id,
                    detail=json.dumps({"silence_s": lag})))
    open_collector_incident(db, col.tenant_id, col.id, col.last_heartbeat_at, now)


def mark_online(db: Session, col: Collector, now: datetime) -> None:
    was_offline = col.status == "offline"
    col.status, col.status_changed_at, col.status_reason = "online", now, "Heartbeat received"
    if was_offline:
        db.add(HealthTransition(tenant_id=col.tenant_id, entity_type="collector", entity_id=col.id,
                                prev_state="poor", new_state="excellent", trigger_metric="heartbeat",
                                reason="Heartbeat resumed", at=now))
        db.add(AuditLog(actor="watchdog", tenant_id=col.tenant_id, action="collector_online", target=col.id))
        for inc in db.scalars(select(Incident).where(Incident.tenant_id == col.tenant_id, Incident.kind == "collector",
                                                     Incident.collector_id == col.id,
                                                     Incident.status.in_(["open", "acknowledged"]))).all():
            resolve(db, inc, now, "Heartbeat resumed — collector back online", "watchdog")


def run_maintenance(db: Session, now: datetime, heartbeat_timeout_s: float, stale_after_s: float) -> dict:
    stats = {"offline": 0, "transitions": 0, "cleared": 0, "resolved": 0}
    # 1. heartbeat watchdog
    for col in db.scalars(select(Collector).where(Collector.status == "online")).all():
        if col.last_heartbeat_at is None or (now - col.last_heartbeat_at).total_seconds() > heartbeat_timeout_s:
            mark_offline(db, col, now, heartbeat_timeout_s)
            stats["offline"] += 1
    # 2. stale-data aware health for every source
    collectors = {c.id: c for c in db.scalars(select(Collector)).all()}
    thresholds_cache: dict[str, dict] = {}
    for src in db.scalars(select(Source)).all():
        th = thresholds_cache.setdefault(src.tenant_id, tenant_thresholds(db, src.tenant_id))
        if evaluate_source(db, src, collectors.get(src.collector_id), th, now, stale_after_s):
            stats["transitions"] += 1
    # 3. clear anomalies that stopped recurring
    for a in db.scalars(select(Anomaly).where(Anomaly.status == "active",
                                              Anomaly.last_seen_at < now - timedelta(seconds=ANOMALY_CLEAR_S))).all():
        a.status = "cleared"
        stats["cleared"] += 1
    # 4. auto-resolve quiet anomaly incidents
    for inc in db.scalars(select(Incident).where(Incident.kind == "anomaly",
                                                 Incident.status.in_(["open", "acknowledged"]),
                                                 Incident.updated_at < now - timedelta(seconds=AUTO_RESOLVE_QUIET_S))).all():
        active = db.scalar(select(Anomaly.id).where(Anomaly.incident_id == inc.id, Anomaly.status == "active").limit(1))
        reopened = db.scalar(select(AuditLog.id).where(AuditLog.target == inc.id,
                                                       AuditLog.action == "incident_reopened").limit(1))
        if active is None and reopened is None:   # a human reopened it → only a human resolves it
            resolve(db, inc, now, f"Auto-resolved: no anomalous signals for {AUTO_RESOLVE_QUIET_S}s; "
                                  "metrics back within baseline", "aiops")
            stats["resolved"] += 1
    # 5. keep 2 h of heartbeat history
    db.query(CollectorHeartbeat).filter(CollectorHeartbeat.at < now - timedelta(hours=2)).delete()
    db.commit()
    return stats
