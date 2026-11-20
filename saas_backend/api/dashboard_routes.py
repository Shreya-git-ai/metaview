"""Dashboard API — every query is scoped by the server-resolved TenantContext."""
from __future__ import annotations

import json
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saas_backend.api import serializers as ser
from saas_backend.config import DEMO_USERS
from saas_backend.database.db import to_iso, utcnow
from saas_backend.models.models import (Anomaly, AuditLog, Collector, HealthTransition, Incident, MetricPoint,
                                        PerfSample, ReceivedEvent, Recommendation, Source, Tenant, Threshold, User)
from saas_backend.security.auth import (Principal, TenantContext, audit, get_db, issue_jwt, require_user,
                                        tenant_context, verify_password)
from saas_backend.services.health import summarise
from saas_backend.services.performance import performance
from shared.contract import METRIC_LABELS, METRIC_UNITS
from shared.health import DEFAULT_THRESHOLDS

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


# ------------------------------------------------------------------ auth
class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=80)
    password: str = Field(max_length=200)


@router.get("/auth/demo-users")
def demo_users():
    return [{"username": u["username"], "role": u["role"], "tenant_id": u["tenant_id"], "display": u["display"]}
            for u in DEMO_USERS]


@router.post("/auth/login")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == req.username))
    if user is None or not verify_password(req.password, user.password_hash):
        db.add(AuditLog(actor=req.username[:80], action="login_failed", detail="{}"))
        db.commit()
        raise HTTPException(401, "invalid username or password")
    db.add(AuditLog(actor=user.username, tenant_id=user.tenant_id, action="login", detail="{}"))
    db.commit()
    return {"token": issue_jwt(user, request.app.state.settings.jwt_secret),
            "user": {"username": user.username, "display_name": user.display_name, "role": user.role,
                     "tenant_id": user.tenant_id}}


@router.get("/auth/me")
def me(p: Principal = Depends(require_user)):
    return {"username": p.username, "role": p.role, "tenant_id": p.tenant_id}


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    now = utcnow()
    t = ctx.tenant_id
    sources = db.scalars(select(Source).where(Source.tenant_id == t)).all()
    cols = db.scalars(select(Collector).where(Collector.tenant_id == t)).all()
    health = summarise(sources)
    open_inc = db.scalars(select(Incident).where(Incident.tenant_id == t, Incident.status.in_(["open", "acknowledged"]))
                          .order_by(Incident.updated_at.desc())).all()
    recent = db.scalars(select(Anomaly).where(Anomaly.tenant_id == t).order_by(Anomaly.detected_at.desc()).limit(8)).all()
    recs = db.scalars(select(Recommendation).where(Recommendation.tenant_id == t, Recommendation.status == "proposed",
                                                   Recommendation.incident_id.in_([i.id for i in open_inc] or [""]))
                      .order_by(Recommendation.created_at.desc()).limit(6)).all()
    latest = db.scalar(select(func.max(ReceivedEvent.event_time)).where(ReceivedEvent.tenant_id == t))
    last_received = db.scalar(select(func.max(ReceivedEvent.received_at)).where(ReceivedEvent.tenant_id == t))
    tenant = db.get(Tenant, t)
    return {
        "tenant": {"id": tenant.id, "name": tenant.name, "region": tenant.region},
        "health": health,
        "kpis": {
            "monitored_sources": len(sources),
            "collectors_total": len(cols),
            "collectors_online": sum(1 for c in cols if c.status == "online"),
            "collectors_offline": sum(1 for c in cols if c.status == "offline"),
            "active_incidents": len(open_inc),
            "critical_alerts": sum(1 for i in open_inc if i.severity == "critical"),
            "active_anomalies": db.scalar(select(func.count()).select_from(Anomaly).where(
                Anomaly.tenant_id == t, Anomaly.status == "active")),
        },
        "freshness": {"latest_event_time": to_iso(latest), "last_received_at": to_iso(last_received),
                      "age_s": round((now - latest).total_seconds(), 1) if latest else None,
                      "server_time": to_iso(now)},
        "sources": [ser.source(s, now) for s in sources],
        "incidents": [ser.incident(i) for i in open_inc[:6]],
        "recent_anomalies": [ser.anomaly(a) for a in recent],
        "recommendations": [ser.recommendation(r) for r in recs],
    }


@router.get("/sources")
def sources(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    now = utcnow()
    return [ser.source(s, now) for s in db.scalars(select(Source).where(Source.tenant_id == ctx.tenant_id)).all()]


@router.get("/metrics/latest")
def metrics_latest(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    now = utcnow()
    return {s.source_id: {"metrics": json.loads(s.latest_metrics_json), "event_time": to_iso(s.last_event_time),
                          "stale": s.stale, "data_age_s": round((now - s.last_event_time).total_seconds(), 1)
                          if s.last_event_time else None}
            for s in db.scalars(select(Source).where(Source.tenant_id == ctx.tenant_id)).all()}


@router.get("/metrics/history")
def metrics_history(metric: str, minutes: int = 10, source_id: str | None = None,
                    ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    if metric not in METRIC_LABELS:
        raise HTTPException(400, "unknown metric")
    minutes = max(1, min(minutes, 240))
    since = utcnow() - timedelta(minutes=minutes)
    q = select(MetricPoint.source_id, MetricPoint.value, MetricPoint.event_time, MetricPoint.is_anomalous).where(
        MetricPoint.tenant_id == ctx.tenant_id, MetricPoint.metric == metric, MetricPoint.event_time >= since)
    if source_id:
        q = q.where(MetricPoint.source_id == source_id)
    rows = db.execute(q.order_by(MetricPoint.event_time)).all()
    buckets: dict[int, dict] = {}
    srcs: list[str] = []
    for r in rows:
        ms = int(r.event_time.replace(tzinfo=timezone.utc).timestamp() * 1000) // 1000 * 1000
        b = buckets.setdefault(ms, {"t": ms})
        b[r.source_id] = r.value
        if r.is_anomalous:
            b.setdefault("_anomalous", []).append(r.source_id)
        if r.source_id not in srcs:
            srcs.append(r.source_id)
    roles = {s.source_id: s.role for s in db.scalars(select(Source).where(Source.tenant_id == ctx.tenant_id)).all()}
    return {"metric": metric, "label": METRIC_LABELS[metric], "unit": METRIC_UNITS[metric], "minutes": minutes,
            "sources": [{"source_id": s, "role": roles.get(s)} for s in srcs],
            "points": [buckets[k] for k in sorted(buckets)]}


@router.get("/health")
def health(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    srcs = db.scalars(select(Source).where(Source.tenant_id == ctx.tenant_id)).all()
    return summarise(srcs)


@router.get("/health/transitions")
def health_transitions(limit: int = 50, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(HealthTransition).where(HealthTransition.tenant_id == ctx.tenant_id)
                      .order_by(HealthTransition.at.desc()).limit(min(limit, 500))).all()
    return [{"id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id, "prev_state": r.prev_state,
             "new_state": r.new_state, "trigger_metric": r.trigger_metric, "reason": r.reason, "at": to_iso(r.at)}
            for r in rows]


@router.get("/network")
def network(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    now = utcnow()
    out = []
    for s in db.scalars(select(Source).where(Source.tenant_id == ctx.tenant_id)).all():
        m = json.loads(s.latest_metrics_json or "{}")
        out.append({"source_id": s.source_id, "role": s.role, "latency_ms": m.get("latency_ms"),
                    "packet_loss_pct": m.get("packet_loss_pct"), "net_mbps": m.get("net_mbps"),
                    "connectivity": "stale" if s.stale else "reporting", "health_state": s.health_state,
                    "data_age_s": round((now - s.last_event_time).total_seconds(), 1) if s.last_event_time else None})
    return out


@router.get("/security/summaries")
def security_summaries(limit: int = 30, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(ReceivedEvent).where(ReceivedEvent.tenant_id == ctx.tenant_id,
                                                  ReceivedEvent.category == "security_summary")
                      .order_by(ReceivedEvent.event_time.desc()).limit(min(limit, 200))).all()
    out = []
    for r in rows:
        p = json.loads(r.payload_json)
        out.append({"event_id": r.event_id, "source_id": r.source_id, "event_time": to_iso(r.event_time),
                    "severity": r.severity, "category": r.category, "context_code": r.context_code,
                    "failed_logins": p["metrics"].get("auth_failures"),
                    "distinct_accounts": p["metrics"].get("auth_distinct_accounts"),
                    "distinct_sources": p["metrics"].get("auth_distinct_sources"),
                    "note": "Summarised locally by the collector — raw auth records never reach SaaS."})
    return out


# ------------------------------------------------------------------ collectors
@router.get("/collectors")
def collectors(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    now = utcnow()
    return [ser.collector(c, now) for c in db.scalars(select(Collector).where(Collector.tenant_id == ctx.tenant_id)
                                                       .order_by(Collector.id)).all()]


@router.get("/collectors/{collector_id}")
def collector_detail(collector_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    c = db.get(Collector, collector_id)
    if c is None or c.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "not found")
    now = utcnow()
    srcs = db.scalars(select(Source).where(Source.tenant_id == ctx.tenant_id, Source.collector_id == c.id)).all()
    trans = db.scalars(select(HealthTransition).where(HealthTransition.tenant_id == ctx.tenant_id,
                                                      HealthTransition.entity_id == c.id)
                       .order_by(HealthTransition.at.desc()).limit(20)).all()
    return {**ser.collector(c, now), "sources": [ser.source(s, now) for s in srcs],
            "transitions": [{"prev_state": t.prev_state, "new_state": t.new_state, "reason": t.reason,
                             "at": to_iso(t.at)} for t in trans]}


# ------------------------------------------------------------------ tenants
def _tenant_summary(db: Session, t: Tenant) -> dict:
    srcs = db.scalars(select(Source).where(Source.tenant_id == t.id)).all()
    cols = db.scalars(select(Collector).where(Collector.tenant_id == t.id)).all()
    return {"id": t.id, "name": t.name, "region": t.region, "created_at": to_iso(t.created_at),
            "health": summarise(srcs), "collectors": len(cols),
            "collectors_online": sum(1 for c in cols if c.status == "online"), "sources": len(srcs),
            "open_incidents": db.scalar(select(func.count()).select_from(Incident).where(
                Incident.tenant_id == t.id, Incident.status.in_(["open", "acknowledged"])))}


@router.get("/tenants")
def tenants(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    q = select(Tenant).order_by(Tenant.id)
    if p.role != "platform_operator":
        q = q.where(Tenant.id == p.tenant_id)
    return [_tenant_summary(db, t) for t in db.scalars(q).all()]


def _authorise_tenant(p: Principal, tenant_id: str, db: Session) -> None:
    if p.role == "platform_operator" and db.get(Tenant, tenant_id):
        return
    if p.role == "tenant_admin" and p.tenant_id == tenant_id:
        return
    db.add(AuditLog(actor=p.username, tenant_id=p.tenant_id, action="cross_tenant_denied", target=tenant_id,
                    detail=json.dumps({"via": "path parameter"})))
    db.commit()
    raise HTTPException(404, "not found")


@router.get("/tenants/{tenant_id}")
def tenant_detail(tenant_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    _authorise_tenant(p, tenant_id, db)
    return _tenant_summary(db, db.get(Tenant, tenant_id))


@router.get("/tenants/{tenant_id}/metrics")
def tenant_metrics(tenant_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    _authorise_tenant(p, tenant_id, db)
    now = utcnow()
    return [ser.source(s, now) for s in db.scalars(select(Source).where(Source.tenant_id == tenant_id)).all()]


@router.get("/tenants/{tenant_id}/incidents")
def tenant_incidents(tenant_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    _authorise_tenant(p, tenant_id, db)
    return [ser.incident(i) for i in db.scalars(select(Incident).where(Incident.tenant_id == tenant_id)
                                                .order_by(Incident.opened_at.desc()).limit(50)).all()]


# ------------------------------------------------------------------ settings
class ThresholdUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    excellent_below: float
    good_below: float
    fair_below: float


@router.get("/settings/thresholds")
def get_thresholds(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(Threshold).where(Threshold.tenant_id == ctx.tenant_id)).all()
    return [{"metric": r.metric, "label": METRIC_LABELS.get(r.metric), "unit": METRIC_UNITS.get(r.metric),
             "excellent_below": r.excellent_below, "good_below": r.good_below, "fair_below": r.fair_below,
             "default": DEFAULT_THRESHOLDS.get(r.metric)} for r in rows]


@router.put("/settings/thresholds")
def put_threshold(upd: ThresholdUpdate, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    if upd.metric not in DEFAULT_THRESHOLDS:
        raise HTTPException(400, "unknown metric")
    if not (0 <= upd.excellent_below < upd.good_below < upd.fair_below):
        raise HTTPException(400, "limits must be increasing: excellent < good < fair")
    row = db.get(Threshold, (ctx.tenant_id, upd.metric))
    before = (row.excellent_below, row.good_below, row.fair_below)
    row.excellent_below, row.good_below, row.fair_below = upd.excellent_below, upd.good_below, upd.fair_below
    audit(db, ctx, "threshold_updated", upd.metric, {"before": before,
                                                     "after": (upd.excellent_below, upd.good_below, upd.fair_below)})
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(limit: int = 50, action: str | None = None, ctx: TenantContext = Depends(tenant_context),
              db: Session = Depends(get_db)):
    q = select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id)
    if action:
        q = q.where(AuditLog.action == action)
    rows = db.scalars(q.order_by(AuditLog.at.desc()).limit(min(limit, 500))).all()
    return [{"id": r.id, "at": to_iso(r.at), "actor": r.actor, "action": r.action, "target": r.target,
             "detail": json.loads(r.detail or "{}")} for r in rows]


# ------------------------------------------------------------------ performance
class DashLatency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples_ms: list[float] = Field(max_length=50)


@router.get("/performance")
def perf(minutes: int = 15, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    return performance(db, ctx.tenant_id, utcnow(), max(1, min(minutes, 240)))


@router.post("/performance/dashboard-latency")
def perf_dash(body: DashLatency, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    for v in body.samples_ms:
        if 0 <= v <= 600_000:
            db.add(PerfSample(tenant_id=ctx.tenant_id, kind="dashboard_latency", value=v))
    db.commit()
    return {"ok": True}
