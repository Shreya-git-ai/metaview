from __future__ import annotations

import json

from saas_backend.database.db import to_iso
from saas_backend.models.models import Anomaly, Collector, Incident, Recommendation, Source
from shared.contract import METRIC_LABELS, METRIC_UNITS


def anomaly(a: Anomaly) -> dict:
    return {"id": a.id, "source_id": a.source_id, "metric": a.metric, "label": METRIC_LABELS.get(a.metric, a.metric),
            "unit": METRIC_UNITS.get(a.metric), "observed": a.observed, "peak": a.peak,
            "baseline_median": a.baseline_median, "baseline_mad": a.baseline_mad, "baseline_points": a.baseline_points,
            "expected_low": a.expected_low, "expected_high": a.expected_high, "robust_z": a.robust_z,
            "deviation_pct": a.deviation_pct, "severity": a.severity, "method": a.method, "reason": a.reason,
            "breach_time": to_iso(a.breach_time), "event_time": to_iso(a.event_time),
            "detected_at": to_iso(a.detected_at), "last_seen_at": to_iso(a.last_seen_at), "points": a.points,
            "status": a.status, "incident_id": a.incident_id}


def incident(i: Incident) -> dict:
    return {"id": i.id, "kind": i.kind, "title": i.title, "status": i.status, "severity": i.severity,
            "rule_id": i.rule_id, "probable_cause": i.probable_cause, "cause_label": i.cause_label,
            "explanation": i.explanation, "source_ids": json.loads(i.source_ids_json),
            "signals": json.loads(i.signals_json), "collector_id": i.collector_id, "opened_at": to_iso(i.opened_at),
            "updated_at": to_iso(i.updated_at), "acknowledged_at": to_iso(i.acknowledged_at),
            "resolved_at": to_iso(i.resolved_at), "resolution_note": i.resolution_note}


def recommendation(r: Recommendation) -> dict:
    return {"id": r.id, "incident_id": r.incident_id, "action_code": r.action_code, "title": r.title,
            "rationale": r.rationale, "kind": r.kind, "requires_approval": r.requires_approval, "status": r.status,
            "created_at": to_iso(r.created_at), "updated_at": to_iso(r.updated_at)}


def collector(c: Collector, now) -> dict:
    age = (now - c.last_heartbeat_at).total_seconds() if c.last_heartbeat_at else None
    return {"id": c.id, "tenant_id": c.tenant_id, "status": c.status, "status_reason": c.status_reason,
            "status_changed_at": to_iso(c.status_changed_at), "version": c.version,
            "registered_at": to_iso(c.registered_at), "last_heartbeat_at": to_iso(c.last_heartbeat_at),
            "heartbeat_age_s": round(age, 1) if age is not None else None, "heartbeats": c.heartbeats,
            "uptime_s": c.uptime_s, "agent_cpu_pct": c.agent_cpu_pct, "agent_mem_mb": c.agent_mem_mb,
            "queue": {"pending": c.queue_pending, "retrying": c.queue_retrying, "delivered": c.queue_delivered,
                      "failed": c.queue_failed},
            "policy_blocked": c.policy_blocked, "config_hash": c.config_hash,
            "config_status": ("unknown" if not c.config_hash else
                              "in_sync" if c.config_hash == c.expected_config_hash else "drift"),
            "queue_reported_at": to_iso(c.last_heartbeat_at),
            "queue_note": "as of last heartbeat" if c.status == "online" else "STALE — as of last heartbeat"}


def source(s: Source, now) -> dict:
    age = (now - s.last_event_time).total_seconds() if s.last_event_time else None
    return {"source_id": s.source_id, "collector_id": s.collector_id, "role": s.role,
            "metrics": json.loads(s.latest_metrics_json or "{}"), "health_state": s.health_state,
            "health_trigger": s.health_trigger, "health_reason": s.health_reason,
            "health_changed_at": to_iso(s.health_changed_at), "reported_health": s.reported_health,
            "stale": s.stale, "last_event_time": to_iso(s.last_event_time),
            "data_age_s": round(age, 1) if age is not None else None, "first_seen": to_iso(s.first_seen)}
