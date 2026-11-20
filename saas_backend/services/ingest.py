"""Metadata ingestion: validate → tenant-bind → idempotent store → metrics → health → AIOps."""
from __future__ import annotations

import json
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saas_backend.ai_engine import detector
from saas_backend.correlation.engine import correlate
from saas_backend.models.models import Collector, IngestAudit, MetricPoint, ReceivedEvent, Source
from saas_backend.services.health import evaluate_source, tenant_thresholds
from shared.contract import EgressPayload, canonical_json, parse_iso, sha256_hex

MAX_BATCH = 500
NUMERIC_METRICS = ("cpu_pct", "mem_pct", "disk_pct", "net_mbps", "latency_ms", "packet_loss_pct",
                   "app_response_ms", "auth_failures")


class BadRequest(Exception):
    pass


def _naive(ts: str) -> datetime:
    return parse_iso(ts).replace(tzinfo=None)


def ingest_batch(db: Session, collector: Collector, body: bytes, now: datetime, stale_after_s: float) -> dict:
    try:
        doc = json.loads(body)
    except ValueError:
        raise BadRequest("body is not valid JSON")
    if not isinstance(doc, dict) or set(doc.keys()) != {"events"} or not isinstance(doc["events"], list):
        raise BadRequest("body must be exactly {\"events\": [...]}")
    if len(doc["events"]) > MAX_BATCH:
        raise BadRequest(f"batch larger than {MAX_BATCH}")

    thresholds = tenant_thresholds(db, collector.tenant_id)
    results, counts = [], {"accepted": 0, "duplicate": 0, "rejected": 0}
    touched_sources: dict[str, Source] = {}
    for raw in doc["events"]:
        eid = raw.get("event_id") if isinstance(raw, dict) else None
        if not isinstance(raw, dict):
            results.append({"event_id": None, "status": "rejected", "reason": "event is not an object"})
            counts["rejected"] += 1
            continue
        sha = sha256_hex(canonical_json(raw))          # hash of exactly what was received
        try:
            p = EgressPayload.model_validate(raw)
        except ValidationError as e:
            errs = "; ".join(f"{err['type']} at {'.'.join(map(str, err['loc']))}" for err in e.errors()[:5])
            results.append({"event_id": eid, "status": "rejected", "reason": f"schema: {errs}"})
            counts["rejected"] += 1
            continue
        if p.tenant_id != collector.tenant_id or p.collector_id != collector.id:
            results.append({"event_id": eid, "status": "rejected",
                            "reason": "identity: payload tenant/collector does not match authenticated collector"})
            counts["rejected"] += 1
            continue
        if db.get(ReceivedEvent, (p.tenant_id, p.event_id)) is not None:
            results.append({"event_id": eid, "status": "duplicate"})
            counts["duplicate"] += 1
            continue
        try:
            with db.begin_nested():
                db.add(ReceivedEvent(tenant_id=p.tenant_id, event_id=p.event_id, collector_id=p.collector_id,
                                     source_id=p.source_id, category=p.category, event_time=_naive(p.event_time),
                                     window_start=_naive(p.window_start), received_at=now,
                                     health_state=p.health_state, severity=p.severity, context_code=p.context_code,
                                     payload_json=canonical_json(raw), payload_sha256=sha))
        except IntegrityError:
            results.append({"event_id": eid, "status": "duplicate"})
            counts["duplicate"] += 1
            continue
        src = _process(db, p, now, thresholds)
        if src is not None:
            touched_sources[src.source_id] = src
        results.append({"event_id": eid, "status": "accepted", "sha256": sha})
        counts["accepted"] += 1

    for src in touched_sources.values():
        evaluate_source(db, src, collector, thresholds, now, stale_after_s)
    outcome = "ok" if counts["rejected"] == 0 else ("rejected" if counts["accepted"] == 0 else "partial")
    db.add(IngestAudit(tenant_id=collector.tenant_id, collector_id=collector.id, endpoint="/api/v1/ingest",
                       outcome=outcome, accepted=counts["accepted"], duplicates=counts["duplicate"],
                       rejected=counts["rejected"], request_bytes=len(body),
                       detail=json.dumps([r for r in results if r["status"] == "rejected"][:20])))
    db.commit()
    return {"accepted": counts["accepted"], "duplicates": counts["duplicate"], "rejected": counts["rejected"],
            "results": results}


def _process(db: Session, p: EgressPayload, now: datetime, thresholds: dict) -> Source | None:
    if p.category != "metrics":
        return None   # security summaries are stored as received events; detection uses metrics payloads
    et = _naive(p.event_time)
    ws = _naive(p.window_start)
    src = db.get(Source, (p.tenant_id, p.source_id))
    if src is None:
        src = Source(tenant_id=p.tenant_id, source_id=p.source_id, collector_id=p.collector_id, role=p.source_role,
                     first_seen=now, health_state="excellent", latest_metrics_json="{}")
        db.add(src)
    m = p.metrics.model_dump(exclude_none=True)
    # Out-of-order handling: an older event never overwrites the latest known state.
    if src.last_event_time is None or et >= src.last_event_time:
        src.last_event_time = et
        src.latest_metrics_json = json.dumps(m)
        src.reported_health = p.health_state
    src.last_received_at = now
    db.flush()
    for metric in NUMERIC_METRICS:
        if metric not in m:
            continue
        point = MetricPoint(tenant_id=p.tenant_id, source_id=p.source_id, metric=metric, value=float(m[metric]),
                            event_time=et)
        db.add(point)
        db.flush()
        anomaly, _ = detector.evaluate(db, point, ws, now, thresholds)
        if anomaly is not None:
            correlate(db, anomaly, p.collector_id, now)
    return src
