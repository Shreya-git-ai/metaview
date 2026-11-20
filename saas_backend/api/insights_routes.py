"""Read-only insight endpoints for the full NEXORA UI. All tenant-scoped via TenantContext.

Nothing here can return raw telemetry — SaaS only holds approved metadata.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from saas_backend.api import serializers as ser
from saas_backend.database.db import to_iso, utcnow
from saas_backend.models.models import (Anomaly, AuditLog, Collector, CollectorHeartbeat, HealthTransition, IngestAudit,
                                        Incident, MetricPoint, ReceivedEvent, Source, Tenant)
from saas_backend.security.auth import TenantContext, get_db, tenant_context
from saas_backend.services.health import tenant_thresholds
from shared.contract import METRIC_LABELS

router = APIRouter(prefix="/api/v1", tags=["insights"])

EVENT_ACTIONS = {"incident_opened", "incident_resolved", "incident_acknowledged", "incident_reopened",
                 "collector_offline", "collector_online", "collector_registered", "remediation_executed",
                 "remediation_reverted", "threshold_updated", "cross_tenant_denied"}


def _pct(vals, q):
    return round(float(np.percentile(vals, q)), 3) if vals else None


# ------------------------------------------------------------------ sources
@router.get("/sources/{source_id}")
def source_detail(source_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    s = db.get(Source, (ctx.tenant_id, source_id))
    if s is None:
        raise HTTPException(404, "not found")
    now = utcnow()
    col = db.get(Collector, s.collector_id)
    anoms = db.scalars(select(Anomaly).where(Anomaly.tenant_id == ctx.tenant_id, Anomaly.source_id == source_id)
                       .order_by(Anomaly.detected_at.desc()).limit(50)).all()
    incs = db.scalars(select(Incident).where(Incident.tenant_id == ctx.tenant_id,
                                             Incident.source_ids_json.like(f'%"{source_id}"%'))
                      .order_by(Incident.opened_at.desc()).limit(20)).all()
    trans = db.scalars(select(HealthTransition).where(HealthTransition.tenant_id == ctx.tenant_id,
                                                      HealthTransition.entity_id == source_id)
                       .order_by(HealthTransition.at.desc()).limit(30)).all()
    th = tenant_thresholds(db, ctx.tenant_id)
    return {**ser.source(s, now),
            "collector": ser.collector(col, now) if col else None,
            "anomalies": [ser.anomaly(a) for a in anoms],
            "incidents": [ser.incident(i) for i in incs],
            "transitions": [{"at": to_iso(t.at), "prev_state": t.prev_state, "new_state": t.new_state,
                             "trigger_metric": t.trigger_metric, "reason": t.reason} for t in trans],
            "thresholds": {m: list(v) for m, v in th.items()}}


# ------------------------------------------------------------------ events feed & search
@router.get("/events/recent")
def recent_events(limit: int = 40, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    limit = min(limit, 200)
    out = []
    for t in db.scalars(select(HealthTransition).where(HealthTransition.tenant_id == ctx.tenant_id)
                        .order_by(HealthTransition.at.desc()).limit(limit)).all():
        sev = "critical" if t.new_state == "poor" else "warning" if t.new_state == "fair" else "info"
        out.append({"at": to_iso(t.at), "type": "health_transition", "severity": sev, "entity": t.entity_id,
                    "entity_type": t.entity_type,
                    "text": f"{t.entity_type} {t.entity_id}: {t.prev_state or '—'} → {t.new_state} ({t.reason})"})
    for a in db.scalars(select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id, AuditLog.action.in_(EVENT_ACTIONS))
                        .order_by(AuditLog.at.desc()).limit(limit)).all():
        sev = "critical" if a.action in ("collector_offline", "cross_tenant_denied") else \
              "warning" if a.action == "incident_opened" else "info"
        out.append({"at": to_iso(a.at), "type": a.action, "severity": sev, "entity": a.target, "entity_type": "audit",
                    "text": f"{a.action.replace('_', ' ')} · {a.target or ''} · by {a.actor}"})
    for an in db.scalars(select(Anomaly).where(Anomaly.tenant_id == ctx.tenant_id)
                         .order_by(Anomaly.detected_at.desc()).limit(limit)).all():
        out.append({"at": to_iso(an.detected_at), "type": "anomaly_detected", "severity": an.severity,
                    "entity": an.source_id, "entity_type": "source",
                    "text": f"{METRIC_LABELS.get(an.metric, an.metric)} anomaly on {an.source_id} "
                            f"(z={an.robust_z:.1f}, peak {an.peak:g})"})
    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:limit]


@router.get("/search")
def search(q: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    q = q.strip()[:60]
    if len(q) < 2:
        return {"sources": [], "incidents": [], "collectors": []}
    like = f"%{q}%"
    t = ctx.tenant_id
    return {
        "sources": [{"source_id": s.source_id, "role": s.role, "health_state": s.health_state}
                    for s in db.scalars(select(Source).where(Source.tenant_id == t, or_(
                        Source.source_id.ilike(like), Source.role.ilike(like))).limit(8)).all()],
        "incidents": [{"id": i.id, "title": i.title, "status": i.status, "severity": i.severity}
                      for i in db.scalars(select(Incident).where(Incident.tenant_id == t, or_(
                          Incident.id.ilike(like), Incident.title.ilike(like), Incident.rule_id.ilike(like)))
                          .order_by(Incident.updated_at.desc()).limit(8)).all()],
        "collectors": [{"id": c.id, "status": c.status}
                       for c in db.scalars(select(Collector).where(Collector.tenant_id == t,
                                                                   Collector.id.ilike(like)).limit(8)).all()],
    }


# ------------------------------------------------------------------ fleet
def _collector_health(c: Collector, now) -> tuple[str, str, list[str]]:
    """(status, health_state, reasons) — 'degraded' is derived from reported facts only."""
    if c.status == "offline":
        return "offline", "poor", [c.status_reason or "no heartbeat"]
    if c.status != "online":
        return "registered", "fair", ["registered, no heartbeat yet"]
    reasons = []
    age = (now - c.last_heartbeat_at).total_seconds() if c.last_heartbeat_at else None
    if age is not None and age > 12:
        reasons.append(f"heartbeat late ({age:.0f}s)")
    if c.queue_retrying:
        reasons.append(f"{c.queue_retrying} payload(s) retrying")
    if c.queue_failed:
        reasons.append(f"{c.queue_failed} payload(s) rejected")
    if c.config_hash and c.expected_config_hash and c.config_hash != c.expected_config_hash:
        reasons.append("configuration drift")
    if c.agent_cpu_pct is not None and c.agent_cpu_pct > 80:
        reasons.append(f"agent CPU {c.agent_cpu_pct:.0f}%")
    return ("degraded", "fair", reasons) if reasons else ("online", "excellent", ["heartbeat and delivery normal"])


def fleet_row(db: Session, c: Collector, now) -> dict:
    status, health, reasons = _collector_health(c, now)
    tenant = db.get(Tenant, c.tenant_id)
    last_recv = db.scalar(select(func.max(ReceivedEvent.received_at)).where(
        ReceivedEvent.tenant_id == c.tenant_id, ReceivedEvent.collector_id == c.id))
    n5 = db.scalar(select(func.count()).select_from(ReceivedEvent).where(
        ReceivedEvent.tenant_id == c.tenant_id, ReceivedEvent.collector_id == c.id,
        ReceivedEvent.received_at >= now - timedelta(minutes=5)))
    hb = db.scalar(select(CollectorHeartbeat).where(CollectorHeartbeat.collector_id == c.id)
                   .order_by(CollectorHeartbeat.at.desc()).limit(1))
    counters = json.loads(hb.counters_json) if hb else {}
    sources = db.scalar(select(func.count()).select_from(Source).where(Source.tenant_id == c.tenant_id,
                                                                      Source.collector_id == c.id))
    return {**ser.collector(c, now), "fleet_status": status, "health_state": health, "health_reasons": reasons,
            "region": tenant.region if tenant else None, "tenant_name": tenant.name if tenant else None,
            "sources": sources,
            "ingestion": {"last_event_received_at": to_iso(last_recv), "events_last_5m": n5,
                          "events_per_min": round(n5 / 5, 1),
                          "status": "receiving" if last_recv and (now - last_recv).total_seconds() < 15 else "stalled"},
            "local_processing": counters}


@router.get("/fleet")
def fleet(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    now = utcnow()
    rows = [fleet_row(db, c, now) for c in db.scalars(select(Collector).where(Collector.tenant_id == ctx.tenant_id)
                                                     .order_by(Collector.id)).all()]
    summary = Counter(r["fleet_status"] for r in rows)
    return {"summary": {"total": len(rows), "online": summary["online"] + summary["degraded"],
                        "healthy": summary["online"], "degraded": summary["degraded"], "offline": summary["offline"],
                        "registered": summary["registered"]}, "collectors": rows}


def _own_collector(db: Session, ctx: TenantContext, collector_id: str) -> Collector:
    c = db.get(Collector, collector_id)
    if c is None or c.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "not found")
    return c


@router.get("/collectors/{collector_id}/heartbeats")
def heartbeats(collector_id: str, minutes: int = 30, ctx: TenantContext = Depends(tenant_context),
               db: Session = Depends(get_db)):
    c = _own_collector(db, ctx, collector_id)
    since = utcnow() - timedelta(minutes=max(1, min(minutes, 120)))
    rows = db.scalars(select(CollectorHeartbeat).where(CollectorHeartbeat.collector_id == c.id,
                                                       CollectorHeartbeat.at >= since)
                      .order_by(CollectorHeartbeat.at)).all()
    out, gaps, prev = [], [], None
    for r in rows:
        gap = (r.at - prev).total_seconds() if prev else None
        if gap and gap > 15:
            gaps.append({"from": to_iso(prev), "to": to_iso(r.at), "seconds": round(gap, 1)})
        out.append({"at": to_iso(r.at), "t": int(r.at.replace(tzinfo=timezone.utc).timestamp() * 1000),
                    "gap_s": round(gap, 1) if gap else None,
                    "transport_ms": round((r.at - r.sent_at).total_seconds() * 1000, 1) if r.sent_at else None,
                    "queue_pending": r.queue_pending, "queue_retrying": r.queue_retrying,
                    "queue_delivered": r.queue_delivered, "agent_cpu_pct": r.agent_cpu_pct,
                    "agent_mem_mb": r.agent_mem_mb})
        prev = r.at
    return {"collector_id": c.id, "minutes": minutes, "heartbeats": out, "gaps": gaps,
            "expected_interval_s": 5, "offline_after_s": 30}


@router.get("/collectors/{collector_id}/events")
def collector_events(collector_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    c = _own_collector(db, ctx, collector_id)
    logs = db.scalars(select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id, or_(
        AuditLog.target == c.id, AuditLog.actor == f"collector:{c.id}")).order_by(AuditLog.at.desc()).limit(40)).all()
    ingests = db.scalars(select(IngestAudit).where(IngestAudit.collector_id == c.id)
                         .order_by(IngestAudit.at.desc()).limit(20)).all()
    return {"audit": [{"at": to_iso(r.at), "action": r.action, "actor": r.actor, "detail": json.loads(r.detail or "{}")}
                      for r in logs],
            "transmissions": [{"at": to_iso(r.at), "outcome": r.outcome, "accepted": r.accepted,
                               "duplicates": r.duplicates, "rejected": r.rejected, "bytes": r.request_bytes}
                              for r in ingests]}


# ------------------------------------------------------------------ privacy summary
@router.get("/privacy/summary")
def privacy_summary(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    t = ctx.tenant_id
    by_cat = dict(db.execute(select(ReceivedEvent.category, func.count()).where(ReceivedEvent.tenant_id == t)
                             .group_by(ReceivedEvent.category)).all())
    je = func.json_extract
    sums = db.execute(select(
        func.coalesce(func.sum(je(ReceivedEvent.payload_json, "$.processing.raw_records")), 0),
        func.coalesce(func.sum(je(ReceivedEvent.payload_json, "$.processing.fields_dropped")), 0),
        func.coalesce(func.sum(je(ReceivedEvent.payload_json, "$.processing.secrets_redacted")), 0),
        func.coalesce(func.sum(je(ReceivedEvent.payload_json, "$.processing.identifiers_pseudonymised")), 0),
        func.count(je(ReceivedEvent.payload_json, "$.processing")),
    ).where(ReceivedEvent.tenant_id == t)).one()
    rules = Counter()
    for (pj,) in db.execute(select(ReceivedEvent.payload_json).where(
            ReceivedEvent.tenant_id == t,
            func.json_array_length(je(ReceivedEvent.payload_json, "$.processing.rules_triggered")) > 0)
            .order_by(ReceivedEvent.received_at.desc()).limit(5000)).all():
        rules.update(json.loads(pj)["processing"]["rules_triggered"])
    redacted_events = db.scalar(select(func.count()).select_from(ReceivedEvent).where(
        ReceivedEvent.tenant_id == t, je(ReceivedEvent.payload_json, "$.processing.secrets_redacted") > 0))
    ing = db.execute(select(func.coalesce(func.sum(IngestAudit.accepted), 0), func.coalesce(func.sum(IngestAudit.duplicates), 0),
                            func.coalesce(func.sum(IngestAudit.rejected), 0)).where(IngestAudit.tenant_id == t)).one()
    collectors = []
    for c in db.scalars(select(Collector).where(Collector.tenant_id == t)).all():
        hb = db.scalar(select(CollectorHeartbeat).where(CollectorHeartbeat.collector_id == c.id)
                       .order_by(CollectorHeartbeat.at.desc()).limit(1))
        collectors.append({"collector_id": c.id, "status": c.status, "reported_at": to_iso(hb.at) if hb else None,
                           "policy_blocked": c.policy_blocked, **(json.loads(hb.counters_json) if hb else {})})
    return {
        "received_by_category": by_cat, "received_total": sum(by_cat.values()),
        "events_with_processing_evidence": sums[4],
        "processing": {"raw_records": int(sums[0]), "fields_dropped": int(sums[1]), "secrets_redacted": int(sums[2]),
                       "identifiers_pseudonymised": int(sums[3]), "events_with_secrets_removed": redacted_events},
        "rules_triggered": dict(rules.most_common()),
        "ingest": {"accepted": int(ing[0]), "duplicates": int(ing[1]), "rejected_by_saas": int(ing[2])},
        "collectors": collectors,
        "policy_blocked_total": sum(c["policy_blocked"] or 0 for c in collectors),
        "notes": ["Counts and rule ids are reported by the collector as allow-listed metadata — no removed values.",
                  "Blocked payloads never leave the customer environment; SaaS only sees the collector's count."],
    }


# ------------------------------------------------------------------ security & network
@router.get("/security/overview")
def security_overview(minutes: int = 60, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    t = ctx.tenant_id
    since = utcnow() - timedelta(minutes=max(5, min(minutes, 1440)))
    per_source = db.execute(select(MetricPoint.source_id, func.sum(MetricPoint.value), func.max(MetricPoint.value))
                            .where(MetricPoint.tenant_id == t, MetricPoint.metric == "auth_failures",
                                   MetricPoint.event_time >= since).group_by(MetricPoint.source_id)).all()
    roles = {s.source_id: s.role for s in db.scalars(select(Source).where(Source.tenant_id == t)).all()}
    bursts = db.scalar(select(func.count()).select_from(ReceivedEvent).where(
        ReceivedEvent.tenant_id == t, ReceivedEvent.category == "security_summary", ReceivedEvent.event_time >= since))
    incs = db.scalars(select(Incident).where(Incident.tenant_id == t, Incident.rule_id == "R-SEC-01")
                      .order_by(Incident.opened_at.desc()).limit(20)).all()
    anoms = db.scalars(select(Anomaly).where(Anomaly.tenant_id == t, Anomaly.metric == "auth_failures")
                       .order_by(Anomaly.detected_at.desc()).limit(20)).all()
    total = sum(int(r[1] or 0) for r in per_source)
    return {"minutes": minutes, "failed_logins_total": total, "burst_summaries": bursts,
            "active_security_incidents": sum(1 for i in incs if i.status != "resolved"),
            "by_source": sorted([{"source_id": r[0], "role": roles.get(r[0]), "failed_logins": int(r[1] or 0),
                                  "max_per_window": int(r[2] or 0)} for r in per_source],
                                key=lambda x: -x["failed_logins"]),
            "incidents": [ser.incident(i) for i in incs], "anomalies": [ser.anomaly(a) for a in anoms],
            "privacy_note": "Usernames, IPs and raw auth records are summarised locally; only counts reach SaaS."}


@router.get("/network/overview")
def network_overview(minutes: int = 15, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    t = ctx.tenant_id
    now = utcnow()
    since = now - timedelta(minutes=max(1, min(minutes, 240)))
    th = tenant_thresholds(db, t)
    out = []
    for s in db.scalars(select(Source).where(Source.tenant_id == t)).all():
        row = {"source_id": s.source_id, "role": s.role, "health_state": s.health_state, "stale": s.stale,
               "data_age_s": round((now - s.last_event_time).total_seconds(), 1) if s.last_event_time else None,
               "latest": {k: json.loads(s.latest_metrics_json or "{}").get(k)
                          for k in ("latency_ms", "packet_loss_pct", "net_mbps")}}
        for metric in ("latency_ms", "packet_loss_pct", "net_mbps"):
            vals = list(db.scalars(select(MetricPoint.value).where(
                MetricPoint.tenant_id == t, MetricPoint.source_id == s.source_id, MetricPoint.metric == metric,
                MetricPoint.event_time >= since)).all())
            row[metric] = {"avg": round(float(np.mean(vals)), 3) if vals else None, "p95": _pct(vals, 95),
                           "max": _pct(vals, 100), "samples": len(vals)}
        lat, loss = row["latest"]["latency_ms"], row["latest"]["packet_loss_pct"]
        degraded = []
        if lat is not None and lat >= th["latency_ms"][0]:
            degraded.append(f"latency {lat:.0f} ms ≥ {th['latency_ms'][0]:g} ms")
        if loss is not None and loss >= th["packet_loss_pct"][0]:
            degraded.append(f"packet loss {loss:.2f}% ≥ {th['packet_loss_pct'][0]:g}%")
        row["connectivity"] = "stale" if s.stale else ("degraded" if degraded else "healthy")
        row["degradation"] = degraded
        out.append(row)
    incs = db.scalars(select(Incident).where(Incident.tenant_id == t, Incident.rule_id.like("R-NET-%"))
                      .order_by(Incident.opened_at.desc()).limit(20)).all()
    anoms = db.scalars(select(Anomaly).where(Anomaly.tenant_id == t,
                                             Anomaly.metric.in_(["latency_ms", "packet_loss_pct"]))
                       .order_by(Anomaly.detected_at.desc()).limit(30)).all()
    return {"minutes": minutes, "sources": out, "thresholds": {k: th[k] for k in ("latency_ms", "packet_loss_pct")},
            "incidents": [ser.incident(i) for i in incs], "anomalies": [ser.anomaly(a) for a in anoms]}


# ------------------------------------------------------------------ platform (NEXORA itself)
@router.get("/platform/health")
def platform_health(request: Request, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    st = request.app.state
    now = utcnow()
    reqs = list(st.api_stats)
    window = [r for r in reqs if r["ts"] >= time.time() - 900]
    groups: dict[str, list] = {}
    for r in window:
        groups.setdefault(r["group"], []).append(r)
    by_group = []
    for g, rs in sorted(groups.items()):
        d = [r["ms"] for r in rs]
        by_group.append({"group": g, "requests": len(rs), "p50_ms": _pct(d, 50), "p95_ms": _pct(d, 95),
                         "errors_4xx": sum(1 for r in rs if 400 <= r["status"] < 500),
                         "errors_5xx": sum(1 for r in rs if r["status"] >= 500)})
    all_ms = [r["ms"] for r in window]
    # database health
    t0 = time.perf_counter()
    db.execute(text("SELECT 1"))
    db_ping = (time.perf_counter() - t0) * 1000
    path = st.settings.db_path
    size = os.path.getsize(path) if os.path.exists(path) else 0
    wal = str(path) + "-wal"
    counts = None
    if ctx.principal.role == "platform_operator":     # cross-tenant table sizes: operator only
        counts = {name: db.scalar(select(func.count()).select_from(model)) for name, model in
                  (("received_events", ReceivedEvent), ("metric_points", MetricPoint), ("anomalies", Anomaly),
                   ("incidents", Incident), ("audit_log", AuditLog))}
    # ingestion volume (tenant-scoped; operator sees the selected tenant)
    since = now - timedelta(minutes=30)
    vol = db.execute(select(func.strftime("%Y-%m-%dT%H:%M:00Z", ReceivedEvent.received_at), func.count())
                     .where(ReceivedEvent.tenant_id == ctx.tenant_id, ReceivedEvent.received_at >= since)
                     .group_by(func.strftime("%Y-%m-%dT%H:%M:00Z", ReceivedEvent.received_at))).all()
    lat = [(r.received_at - r.event_time).total_seconds() for r in db.execute(
        select(ReceivedEvent.received_at, ReceivedEvent.event_time).where(
            ReceivedEvent.tenant_id == ctx.tenant_id, ReceivedEvent.received_at >= now - timedelta(minutes=15))).all()]
    live = [x for x in lat if x <= 60]
    tids = [c.id for c in db.scalars(select(Collector).where(Collector.tenant_id == ctx.tenant_id)).all()]
    hbs = db.scalars(select(CollectorHeartbeat).where(CollectorHeartbeat.collector_id.in_(tids or [""]),
                                                      CollectorHeartbeat.at >= now - timedelta(minutes=15))).all()
    transport = [(h.at - h.sent_at).total_seconds() * 1000 for h in hbs if h.sent_at]
    total = len(window)
    return {
        "scope_note": "NEXORA platform health (this SaaS process) — not customer infrastructure.",
        "availability": {"status": "up", "started_at": to_iso(st.started_at),
                         "uptime_s": round((now - st.started_at).total_seconds()), "tls": True,
                         "maintenance_last_run": to_iso(st.maintenance_last_run),
                         "maintenance_last_ms": st.maintenance_last_ms},
        "api": {"window_s": 900, "requests": total, "p50_ms": _pct(all_ms, 50), "p95_ms": _pct(all_ms, 95),
                "error_rate_5xx": round(sum(1 for r in window if r["status"] >= 500) / total, 4) if total else 0,
                "error_rate_4xx": round(sum(1 for r in window if 400 <= r["status"] < 500) / total, 4) if total else 0,
                "requests_since_start": st.api_total, "by_group": by_group,
                "note": "measured in-process by request middleware since the SaaS process started"},
        "ingestion": {"per_minute": [{"minute": m, "events": n} for m, n in vol],
                      "processing_latency_p50_s": _pct(live, 50), "processing_latency_p95_s": _pct(live, 95),
                      "samples": len(live)},
        "collector_link": {"heartbeat_transport_p50_ms": _pct(transport, 50),
                           "heartbeat_transport_p95_ms": _pct(transport, 95), "samples": len(transport),
                           "note": "heartbeat received_at − sent_at (same host clock)"},
        "database": {"engine": "SQLite (WAL)", "ping_ms": round(db_ping, 2), "size_bytes": size,
                     "wal_bytes": os.path.getsize(wal) if os.path.exists(wal) else 0, "row_counts": counts},
    }
