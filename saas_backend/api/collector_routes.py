"""Collector-facing API (TLS + collector bearer token)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.database.db import to_iso, utcnow
from saas_backend.models.models import (AuditLog, Collector, CollectorHeartbeat, EnrollmentToken, IngestAudit,
                                        ReceivedEvent)
from saas_backend.security.auth import get_db, hash_token, new_collector_token, require_collector
from saas_backend.services.ingest import BadRequest, ingest_batch
from saas_backend.services.maintenance import mark_online
from shared.contract import HeartbeatPayload, parse_iso

router = APIRouter(prefix="/api/v1", tags=["collector"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(pattern=r"^tenant-[a-z0-9-]{1,32}$")
    collector_id: str = Field(pattern=r"^col-[a-z0-9-]{1,40}$")
    enrollment_token: str = Field(min_length=10, max_length=200)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ids: list[str] = Field(max_length=10000)


@router.post("/collectors/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Exchange a tenant enrollment token for a per-collector credential (returned once)."""
    enr = db.get(EnrollmentToken, hash_token(req.enrollment_token))
    if enr is None or enr.tenant_id != req.tenant_id:
        db.add(IngestAudit(tenant_id=None, collector_id=req.collector_id, endpoint="/collectors/register",
                           outcome="auth_failed", detail="invalid enrollment token or tenant mismatch"))
        db.commit()
        raise HTTPException(401, "invalid enrollment token")
    col = db.get(Collector, req.collector_id)
    if col is not None and col.tenant_id != req.tenant_id:
        raise HTTPException(409, "collector id belongs to another tenant")
    token = new_collector_token()
    now = utcnow()
    if col is None:
        col = Collector(id=req.collector_id, tenant_id=req.tenant_id, token_hash=hash_token(token),
                        version=req.version, registered_at=now, status="registered", status_changed_at=now)
        db.add(col)
    else:   # re-enrollment rotates the credential
        col.token_hash = hash_token(token)
        col.version = req.version
    enr.uses += 1
    db.add(AuditLog(actor=f"collector:{req.collector_id}", tenant_id=req.tenant_id, action="collector_registered",
                    target=req.collector_id))
    db.commit()
    return {"collector_id": col.id, "tenant_id": col.tenant_id, "collector_token": token}


@router.post("/collectors/auth")
def authenticate(col: Collector = Depends(require_collector)):
    return {"authenticated": True, "collector_id": col.id, "tenant_id": col.tenant_id}


@router.post("/ingest")
async def ingest(request: Request, col: Collector = Depends(require_collector), db: Session = Depends(get_db)):
    body = await request.body()
    try:
        return ingest_batch(db, col, body, utcnow(), request.app.state.settings.stale_after_s)
    except BadRequest as e:
        db.rollback()
        db.add(IngestAudit(tenant_id=col.tenant_id, collector_id=col.id, endpoint="/api/v1/ingest",
                           outcome="rejected", request_bytes=len(body), detail=str(e)))
        db.commit()
        raise HTTPException(400, str(e))


@router.post("/collectors/heartbeat")
async def heartbeat(request: Request, col: Collector = Depends(require_collector), db: Session = Depends(get_db)):
    try:
        hb = HeartbeatPayload.model_validate_json(await request.body())
    except ValidationError as e:
        raise HTTPException(422, f"heartbeat schema violation: {e.errors()[0]['type']}")
    if hb.tenant_id != col.tenant_id or hb.collector_id != col.id:
        db.add(AuditLog(actor=f"collector:{col.id}", tenant_id=col.tenant_id, action="heartbeat_identity_mismatch",
                        target=hb.collector_id))
        db.commit()
        raise HTTPException(403, "identity mismatch")
    now = utcnow()
    col.last_heartbeat_at = now
    col.last_heartbeat_sent_at = parse_iso(hb.sent_at).replace(tzinfo=None)
    col.heartbeats += 1
    for f in ("version", "uptime_s", "agent_cpu_pct", "agent_mem_mb", "queue_pending", "queue_retrying",
              "queue_delivered", "queue_failed", "policy_blocked", "config_hash"):
        setattr(col, f, getattr(hb, f))
    sent = col.last_heartbeat_sent_at
    db.add(CollectorHeartbeat(
        tenant_id=col.tenant_id, collector_id=col.id, at=now, sent_at=sent, queue_pending=hb.queue_pending,
        queue_retrying=hb.queue_retrying, queue_delivered=hb.queue_delivered, queue_failed=hb.queue_failed,
        agent_cpu_pct=hb.agent_cpu_pct, agent_mem_mb=hb.agent_mem_mb, policy_blocked=hb.policy_blocked,
        counters_json=json.dumps({k: getattr(hb, k) for k in (
            "raw_records_total", "fields_dropped_total", "secrets_redacted_total", "identifiers_pseudonymised_total",
            "payloads_approved_total", "last_successful_send_at", "uptime_s")})))
    if col.expected_config_hash is None:
        col.expected_config_hash = hb.config_hash
    if col.status != "online":
        mark_online(db, col, now)
    db.commit()
    return {"ok": True, "server_time": to_iso(now)}


@router.get("/collectors/me/status")
def my_status(col: Collector = Depends(require_collector)):
    return {"collector_id": col.id, "tenant_id": col.tenant_id, "status": col.status,
            "last_heartbeat_at": to_iso(col.last_heartbeat_at)}


@router.get("/collectors/me/received/{event_id}")
def my_received(event_id: str, col: Collector = Depends(require_collector), db: Session = Depends(get_db)):
    """Lets a collector verify what SaaS stored for one of ITS events (tenant + collector scoped)."""
    ev = db.get(ReceivedEvent, (col.tenant_id, event_id))
    if ev is None or ev.collector_id != col.id:
        raise HTTPException(404, "not found")
    return {"event_id": ev.event_id, "payload_sha256": ev.payload_sha256, "received_at": to_iso(ev.received_at),
            "payload": json.loads(ev.payload_json)}


@router.post("/collectors/me/reconcile")
def reconcile(req: ReconcileRequest, col: Collector = Depends(require_collector), db: Session = Depends(get_db)):
    """Post-outage delivery verification: which of the collector's 'delivered' ids are missing here?"""
    ids = set(req.event_ids)
    present: set[str] = set()
    id_list = list(ids)
    for i in range(0, len(id_list), 500):
        chunk = id_list[i:i + 500]
        present |= set(db.scalars(select(ReceivedEvent.event_id).where(
            ReceivedEvent.tenant_id == col.tenant_id, ReceivedEvent.collector_id == col.id,
            ReceivedEvent.event_id.in_(chunk))).all())
    missing = sorted(ids - present)
    result = {"checked": len(ids), "present": len(present), "missing": len(missing), "missing_ids": missing[:50],
              "duplicates_stored": 0, "note": "(tenant_id, event_id) is the primary key — duplicates cannot be stored"}
    db.add(AuditLog(actor=f"collector:{col.id}", tenant_id=col.tenant_id, action="reconcile", target=col.id,
                    detail=json.dumps({"collector_id": col.id, "checked": len(ids), "present": len(present),
                                       "missing": len(missing)})))
    db.commit()
    return result
