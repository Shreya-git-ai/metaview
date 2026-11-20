"""AIOps API: anomalies, baselines, incidents, probable causes, recommendations, safe remediation."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from saas_backend.ai_engine.detector import DETECT, METHOD, WARMUP_POINTS, Z_IMMEDIATE, Z_SUSTAINED, current_baselines
from saas_backend.api import serializers as ser
from saas_backend.correlation.engine import ACTIONS, CORRELATION_WINDOW_S, RULES, resolve
from saas_backend.database.db import to_iso, utcnow
from saas_backend.models.models import (Anomaly, AuditLog, Incident, MetricPoint, Recommendation, RemediationAction,
                                        Source)
from saas_backend.security.auth import TenantContext, audit, get_db, tenant_context
from shared.contract import METRIC_LABELS

router = APIRouter(prefix="/api/v1/aiops", tags=["aiops"])


def _incident(db: Session, ctx: TenantContext, incident_id: str) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None or inc.tenant_id != ctx.tenant_id:     # 404, not 403: do not reveal existence
        if inc is not None:
            audit(db, ctx, "cross_tenant_denied", incident_id, {"via": "incident id"})
            db.commit()
        raise HTTPException(404, "not found")
    return inc


def _rec(db: Session, ctx: TenantContext, rec_id: int) -> Recommendation:
    r = db.get(Recommendation, rec_id)
    if r is None or r.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "not found")
    return r


@router.get("/method")
def method():
    return {
        "detector": METHOD,
        "statement": "Explainable statistics only — no machine-learning model and no confidence score is used.",
        "rules": {"warmup_points": WARMUP_POINTS, "z_immediate": Z_IMMEDIATE, "z_sustained_2_points": Z_SUSTAINED,
                  "min_absolute_deviation": {m: s.min_delta for m, s in DETECT.items()}},
        "correlation": f"Same pseudonymous source (or network signals on the same collector) within "
                       f"{CORRELATION_WINDOW_S}s → one incident; ordered rule signatures choose the hypothesis.",
        "correlation_rules": [{"id": r.id, "requires": sorted(METRIC_LABELS[m] for m in r.requires), "title": r.title,
                               "probable_cause": r.cause, "actions": list(r.actions)} for r in RULES],
    }


@router.get("/anomalies")
def anomalies(status: str | None = None, limit: int = 50, ctx: TenantContext = Depends(tenant_context),
              db: Session = Depends(get_db)):
    q = select(Anomaly).where(Anomaly.tenant_id == ctx.tenant_id)
    if status:
        q = q.where(Anomaly.status == status)
    return [ser.anomaly(a) for a in db.scalars(q.order_by(Anomaly.detected_at.desc()).limit(min(limit, 500))).all()]


@router.get("/baselines")
def baselines(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    sids = list(db.scalars(select(Source.source_id).where(Source.tenant_id == ctx.tenant_id)).all())
    return current_baselines(db, ctx.tenant_id, sids, utcnow())


@router.get("/incidents")
def incidents(status: str = "active", severity: str | None = None, q: str | None = None, source_id: str | None = None,
              kind: str | None = None, limit: int = 50, ctx: TenantContext = Depends(tenant_context),
              db: Session = Depends(get_db)):
    query = select(Incident).where(Incident.tenant_id == ctx.tenant_id)
    if status == "active":
        query = query.where(Incident.status.in_(["open", "acknowledged"]))
    elif status in ("open", "acknowledged", "resolved"):
        query = query.where(Incident.status == status)
    if severity in ("info", "warning", "critical"):
        query = query.where(Incident.severity == severity)
    if kind in ("anomaly", "collector"):
        query = query.where(Incident.kind == kind)
    if q:
        like = f"%{q.strip()[:60]}%"
        query = query.where(or_(Incident.id.ilike(like), Incident.title.ilike(like), Incident.rule_id.ilike(like),
                                Incident.source_ids_json.ilike(like)))
    if source_id:
        query = query.where(Incident.source_ids_json.like(f'%"{source_id}"%'))
    return [ser.incident(i) for i in db.scalars(query.order_by(Incident.updated_at.desc()).limit(min(limit, 500))).all()]


@router.get("/incidents/stats")
def incident_stats(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    rows = db.execute(select(Incident.status, Incident.severity, func.count()).where(Incident.tenant_id == ctx.tenant_id)
                      .group_by(Incident.status, Incident.severity)).all()
    out = {"open": 0, "acknowledged": 0, "resolved": 0, "critical_active": 0, "warning_active": 0, "total": 0}
    for st, sev, n in rows:
        out[st] = out.get(st, 0) + n
        out["total"] += n
        if st != "resolved" and sev in ("critical", "warning"):
            out[f"{sev}_active"] += n
    resolved = db.execute(select(Incident.opened_at, Incident.resolved_at).where(
        Incident.tenant_id == ctx.tenant_id, Incident.resolved_at.is_not(None))).all()
    mttr = [(r.resolved_at - r.opened_at).total_seconds() for r in resolved]
    out["mean_time_to_resolve_s"] = round(sum(mttr) / len(mttr), 1) if mttr else None
    return out


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    inc = _incident(db, ctx, incident_id)
    anoms = db.scalars(select(Anomaly).where(Anomaly.tenant_id == ctx.tenant_id, Anomaly.incident_id == inc.id)
                       .order_by(Anomaly.detected_at)).all()
    recs = db.scalars(select(Recommendation).where(Recommendation.tenant_id == ctx.tenant_id,
                                                   Recommendation.incident_id == inc.id)).all()
    rems = db.scalars(select(RemediationAction).where(RemediationAction.tenant_id == ctx.tenant_id,
                                                      RemediationAction.incident_id == inc.id)).all()
    logs = db.scalars(select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id, AuditLog.target == inc.id)).all()
    timeline = [{"at": to_iso(a.detected_at), "type": "anomaly",
                 "text": f"{METRIC_LABELS.get(a.metric, a.metric)} anomaly on {a.source_id}: {a.reason}"} for a in anoms]
    timeline += [{"at": to_iso(l.at), "type": l.action, "text": f"{l.action.replace('_', ' ')} by {l.actor}"
                  + (f" — {json.loads(l.detail).get('note')}" if json.loads(l.detail or '{}').get("note") else "")}
                 for l in logs]
    timeline += [{"at": to_iso(r.created_at), "type": "remediation",
                  "text": f"{r.action_code} {r.status} (approved by {r.approved_by}) — {r.detail}"} for r in rems]
    timeline.sort(key=lambda x: x["at"] or "")
    # metrics of each affected source at detection time (last point at or before the incident opened)
    at_detection = []
    for sid in json.loads(inc.source_ids_json):
        vals = {}
        for metric in ("cpu_pct", "mem_pct", "disk_pct", "latency_ms", "packet_loss_pct", "app_response_ms",
                       "auth_failures"):
            mp = db.scalar(select(MetricPoint).where(
                MetricPoint.tenant_id == ctx.tenant_id, MetricPoint.source_id == sid, MetricPoint.metric == metric,
                MetricPoint.event_time <= inc.opened_at).order_by(MetricPoint.event_time.desc()).limit(1))
            if mp:
                vals[metric] = {"value": mp.value, "event_time": to_iso(mp.event_time), "z": mp.z,
                                "anomalous": mp.is_anomalous}
        src = db.get(Source, (ctx.tenant_id, sid))
        at_detection.append({"source_id": sid, "role": src.role if src else None,
                             "current_health": src.health_state if src else None, "metrics": vals})
    return {**ser.incident(inc), "anomalies": [ser.anomaly(a) for a in anoms],
            "metrics_at_detection": at_detection,
            "recommendations": [ser.recommendation(r) for r in recs],
            "remediations": [{"id": r.id, "action_code": r.action_code, "status": r.status, "approved_by": r.approved_by,
                              "detail": r.detail, "created_at": to_iso(r.created_at),
                              "reverted_at": to_iso(r.reverted_at)} for r in rems],
            "timeline": timeline}


@router.get("/root-causes")
def root_causes(ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(Incident).where(Incident.tenant_id == ctx.tenant_id, Incident.probable_cause.is_not(None))
                      .order_by(Incident.updated_at.desc()).limit(20)).all()
    return [{"incident_id": i.id, "title": i.title, "status": i.status, "rule_id": i.rule_id,
             "probable_cause": i.probable_cause, "label": "Probable cause — hypothesis (not confirmed)",
             "explanation": i.explanation} for i in rows]


@router.get("/recommendations")
def recommendations(status: str | None = None, ctx: TenantContext = Depends(tenant_context),
                    db: Session = Depends(get_db)):
    q = select(Recommendation).where(Recommendation.tenant_id == ctx.tenant_id)
    if status:
        q = q.where(Recommendation.status == status)
    return [ser.recommendation(r) for r in db.scalars(q.order_by(Recommendation.created_at.desc()).limit(100)).all()]


@router.post("/incidents/{incident_id}/acknowledge")
def acknowledge(incident_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    inc = _incident(db, ctx, incident_id)
    if inc.status == "open":
        inc.status, inc.acknowledged_at = "acknowledged", utcnow()
        audit(db, ctx, "incident_acknowledged", inc.id)
        db.commit()
    return ser.incident(inc)


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    inc = _incident(db, ctx, incident_id)
    if inc.status != "resolved":
        resolve(db, inc, utcnow(), "Resolved manually by operator", ctx.principal.username)
        for a in db.scalars(select(Anomaly).where(Anomaly.incident_id == inc.id, Anomaly.status == "active")).all():
            a.status = "cleared"
        db.commit()
    return ser.incident(inc)


@router.post("/incidents/{incident_id}/reopen")
def reopen_incident(incident_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    inc = _incident(db, ctx, incident_id)
    if inc.status != "resolved":
        raise HTTPException(409, "only resolved incidents can be reopened")
    now = utcnow()
    inc.status, inc.resolved_at, inc.resolution_note, inc.updated_at = "open", None, None, now
    audit(db, ctx, "incident_reopened", inc.id)
    db.commit()
    return ser.incident(inc)


@router.post("/recommendations/{rec_id}/execute")
def execute(rec_id: int, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    """Safe remediation: policy-checked, approval recorded, SIMULATED and reversible."""
    r = _rec(db, ctx, rec_id)
    act = ACTIONS.get(r.action_code)
    if act is None or act.kind != "remediate":
        raise HTTPException(400, "only remediation actions can be executed; investigate actions are guidance")
    if r.status == "executed":
        raise HTTPException(409, "already executed")
    inc = _incident(db, ctx, r.incident_id)
    if inc.status == "resolved":
        raise HTTPException(409, "incident already resolved")
    now = utcnow()
    rem = RemediationAction(tenant_id=ctx.tenant_id, recommendation_id=r.id, incident_id=r.incident_id,
                            action_code=r.action_code, approved_by=ctx.principal.username,
                            status="executed_simulated",
                            detail="SIMULATED — no change was made to customer infrastructure; revert available.",
                            created_at=now)
    db.add(rem)
    r.status, r.updated_at = "executed", now
    audit(db, ctx, "remediation_executed", r.incident_id, {"action": r.action_code, "simulated": True,
                                                            "policy": "approval by authenticated tenant user"})
    db.commit()
    return {"ok": True, "remediation_id": rem.id, "status": rem.status, "simulated": True}


@router.post("/recommendations/{rec_id}/revert")
def revert(rec_id: int, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    r = _rec(db, ctx, rec_id)
    rem = db.scalar(select(RemediationAction).where(RemediationAction.tenant_id == ctx.tenant_id,
                                                    RemediationAction.recommendation_id == r.id,
                                                    RemediationAction.status == "executed_simulated"))
    if rem is None:
        raise HTTPException(409, "nothing to revert")
    now = utcnow()
    rem.status, rem.reverted_at = "reverted", now
    r.status, r.updated_at = "reverted", now
    audit(db, ctx, "remediation_reverted", r.incident_id, {"action": r.action_code})
    db.commit()
    return {"ok": True}
