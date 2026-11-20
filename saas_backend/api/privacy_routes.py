"""Privacy API — the SaaS side of the egress proof. Only metadata that SaaS actually received.

There is deliberately NO endpoint for raw logs: SaaS never receives them.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from saas_backend.database.db import to_iso
from saas_backend.models.models import IngestAudit, ReceivedEvent
from saas_backend.security.auth import TenantContext, audit, get_db, tenant_context
from shared.contract import schema_description

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])


def _ev(r: ReceivedEvent, with_payload: bool = True) -> dict:
    d = {"event_id": r.event_id, "collector_id": r.collector_id, "source_id": r.source_id, "category": r.category,
         "event_time": to_iso(r.event_time), "received_at": to_iso(r.received_at),
         "latency_s": round((r.received_at - r.event_time).total_seconds(), 3), "health_state": r.health_state,
         "severity": r.severity, "context_code": r.context_code, "payload_sha256": r.payload_sha256,
         "payload_bytes": len(r.payload_json)}
    if with_payload:
        d["payload"] = json.loads(r.payload_json)
    return d


@router.get("/received")
def received(limit: int = 30, category: str | None = None, collector_id: str | None = None,
             redacted_only: bool = False, q: str | None = None, ctx: TenantContext = Depends(tenant_context),
             db: Session = Depends(get_db)):
    query = select(ReceivedEvent).where(ReceivedEvent.tenant_id == ctx.tenant_id)
    if category:
        query = query.where(ReceivedEvent.category == category)
    if collector_id:
        query = query.where(ReceivedEvent.collector_id == collector_id)
    if redacted_only:
        query = query.where(func.json_extract(ReceivedEvent.payload_json, "$.processing.secrets_redacted") > 0)
    if q:
        query = query.where(or_(ReceivedEvent.event_id.like(f"{q.strip()[:36]}%"),
                                ReceivedEvent.source_id.like(f"%{q.strip()[:20]}%")))
    q = query
    rows = db.scalars(q.order_by(ReceivedEvent.received_at.desc()).limit(min(limit, 500))).all()
    return [_ev(r) for r in rows]


@router.get("/received/{event_id}")
def received_one(event_id: str, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    r = db.get(ReceivedEvent, (ctx.tenant_id, event_id))
    if r is None:
        other = db.scalar(select(ReceivedEvent.tenant_id).where(ReceivedEvent.event_id == event_id))
        if other is not None:
            audit(db, ctx, "cross_tenant_denied", event_id, {"via": "event id"})
            db.commit()
        raise HTTPException(404, "not found")
    return _ev(r)


@router.get("/ingest-audit")
def ingest_audit(limit: int = 50, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    q = select(IngestAudit).where(IngestAudit.tenant_id == ctx.tenant_id)
    if ctx.principal.role == "platform_operator":
        q = select(IngestAudit).where(or_(IngestAudit.tenant_id == ctx.tenant_id, IngestAudit.tenant_id.is_(None)))
    rows = db.scalars(q.order_by(IngestAudit.at.desc()).limit(min(limit, 500))).all()
    return [{"id": r.id, "at": to_iso(r.at), "collector_id": r.collector_id, "endpoint": r.endpoint,
             "outcome": r.outcome, "accepted": r.accepted, "duplicates": r.duplicates, "rejected": r.rejected,
             "request_bytes": r.request_bytes, "detail": r.detail} for r in rows]


@router.get("/rejections")
def rejections(limit: int = 50, ctx: TenantContext = Depends(tenant_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(IngestAudit).where(IngestAudit.tenant_id == ctx.tenant_id,
                                                or_(IngestAudit.rejected > 0, IngestAudit.outcome != "ok"))
                      .order_by(IngestAudit.at.desc()).limit(min(limit, 200))).all()
    return [{"at": to_iso(r.at), "collector_id": r.collector_id, "outcome": r.outcome, "rejected": r.rejected,
             "detail": r.detail} for r in rows]


@router.get("/policy")
def policy():
    return {"contract": schema_description(),
            "saas_checks": ["TLS (server certificate from local dev CA)", "collector bearer token (SHA-256 stored)",
                            "payload tenant_id/collector_id must equal the authenticated collector",
                            "strict schema re-validation (unknown fields rejected)",
                            "idempotent insert on (tenant_id, event_id)",
                            "SHA-256 of received canonical bytes stored for egress-proof comparison"],
            "raw_data_endpoints": "none — SaaS never receives raw logs"}
