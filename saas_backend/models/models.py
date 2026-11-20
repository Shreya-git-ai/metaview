"""SaaS metadata schema. Every tenant-owned table carries tenant_id and every query filters on it."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from saas_backend.database.db import Base, utcnow


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))                 # tenant_admin | platform_operator
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    uses: Mapped[int] = mapped_column(Integer, default=0)


class Collector(Base):
    __tablename__ = "collectors"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[str | None] = mapped_column(String(20))
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_heartbeat_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="registered")   # registered|online|offline
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status_reason: Mapped[str | None] = mapped_column(String(200))
    uptime_s: Mapped[int | None] = mapped_column(Integer)
    agent_cpu_pct: Mapped[float | None] = mapped_column(Float)
    agent_mem_mb: Mapped[float | None] = mapped_column(Float)
    queue_pending: Mapped[int] = mapped_column(Integer, default=0)
    queue_retrying: Mapped[int] = mapped_column(Integer, default=0)
    queue_delivered: Mapped[int] = mapped_column(Integer, default=0)
    queue_failed: Mapped[int] = mapped_column(Integer, default=0)
    policy_blocked: Mapped[int] = mapped_column(Integer, default=0)
    config_hash: Mapped[str | None] = mapped_column(String(12))
    expected_config_hash: Mapped[str | None] = mapped_column(String(12))
    heartbeats: Mapped[int] = mapped_column(Integer, default=0)


class ReceivedEvent(Base):
    """Exactly what the collector sent (canonical JSON) + the SHA-256 computed on receipt."""
    __tablename__ = "received_events"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collector_id: Mapped[str] = mapped_column(String(50), index=True)
    source_id: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(30))
    event_time: Mapped[datetime] = mapped_column(DateTime)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    health_state: Mapped[str] = mapped_column(String(12))
    severity: Mapped[str] = mapped_column(String(12))
    context_code: Mapped[str | None] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (Index("ix_recv_tenant_time", "tenant_id", "event_time"),
                      Index("ix_recv_tenant_received", "tenant_id", "received_at"))


class MetricPoint(Base):
    __tablename__ = "metric_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(20))
    metric: Mapped[str] = mapped_column(String(30))
    value: Mapped[float] = mapped_column(Float)
    event_time: Mapped[datetime] = mapped_column(DateTime)
    z: Mapped[float | None] = mapped_column(Float)
    is_anomalous: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_mp_series", "tenant_id", "source_id", "metric", "event_time"),
                      Index("ix_mp_tenant_metric_time", "tenant_id", "metric", "event_time"))


class Source(Base):
    __tablename__ = "sources"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    collector_id: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_event_time: Mapped[datetime | None] = mapped_column(DateTime)
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime)
    latest_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    reported_health: Mapped[str | None] = mapped_column(String(12))
    health_state: Mapped[str] = mapped_column(String(12), default="excellent")
    health_trigger: Mapped[str | None] = mapped_column(String(40))
    health_reason: Mapped[str | None] = mapped_column(String(300))
    health_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class HealthTransition(Base):
    __tablename__ = "health_transitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))      # source | collector
    entity_id: Mapped[str] = mapped_column(String(50))
    prev_state: Mapped[str | None] = mapped_column(String(12))
    new_state: Mapped[str] = mapped_column(String(12))
    trigger_metric: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Anomaly(Base):
    __tablename__ = "anomalies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(20))
    metric: Mapped[str] = mapped_column(String(30))
    observed: Mapped[float] = mapped_column(Float)
    peak: Mapped[float] = mapped_column(Float)
    baseline_median: Mapped[float] = mapped_column(Float)
    baseline_mad: Mapped[float] = mapped_column(Float)
    baseline_points: Mapped[int] = mapped_column(Integer)
    expected_low: Mapped[float] = mapped_column(Float)
    expected_high: Mapped[float] = mapped_column(Float)
    robust_z: Mapped[float] = mapped_column(Float)
    deviation_pct: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(12))
    method: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500))
    breach_time: Mapped[datetime] = mapped_column(DateTime)
    event_time: Mapped[datetime] = mapped_column(DateTime)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    points: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="active")     # active | cleared
    incident_id: Mapped[str | None] = mapped_column(String(20), index=True)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="anomaly")     # anomaly | collector
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="open")      # open | acknowledged | resolved
    severity: Mapped[str] = mapped_column(String(12))
    rule_id: Mapped[str | None] = mapped_column(String(20))
    probable_cause: Mapped[str | None] = mapped_column(String(500))
    cause_label: Mapped[str] = mapped_column(String(40), default="hypothesis")
    explanation: Mapped[str | None] = mapped_column(Text)
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    signals_json: Mapped[str] = mapped_column(Text, default="[]")
    collector_id: Mapped[str | None] = mapped_column(String(50))
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(String(300))


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    incident_id: Mapped[str] = mapped_column(String(20), index=True)
    action_code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(String(800))
    kind: Mapped[str] = mapped_column(String(20))              # investigate | remediate
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|executed|reverted|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RemediationAction(Base):
    __tablename__ = "remediation_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    recommendation_id: Mapped[int] = mapped_column(Integer)
    incident_id: Mapped[str] = mapped_column(String(20))
    action_code: Mapped[str] = mapped_column(String(40))
    approved_by: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))            # executed_simulated | reverted
    detail: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime)


class IngestAudit(Base):
    __tablename__ = "ingest_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    tenant_id: Mapped[str | None] = mapped_column(String(40), index=True)
    collector_id: Mapped[str | None] = mapped_column(String(50))
    endpoint: Mapped[str] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(30))     # ok | auth_failed | rejected | partial
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    request_bytes: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[str | None] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")


class Threshold(Base):
    __tablename__ = "thresholds"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    metric: Mapped[str] = mapped_column(String(30), primary_key=True)
    excellent_below: Mapped[float] = mapped_column(Float)
    good_below: Mapped[float] = mapped_column(Float)
    fair_below: Mapped[float] = mapped_column(Float)


class PerfSample(Base):
    __tablename__ = "perf_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    tenant_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(40))        # dashboard_latency | offline_detection | reconcile
    value: Mapped[float] = mapped_column(Float)
    detail: Mapped[str] = mapped_column(Text, default="")


class CollectorHeartbeat(Base):
    """Heartbeat history (allow-listed heartbeat fields only) — powers the heartbeat timeline."""
    __tablename__ = "collector_heartbeats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    collector_id: Mapped[str] = mapped_column(String(50))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    queue_pending: Mapped[int] = mapped_column(Integer, default=0)
    queue_retrying: Mapped[int] = mapped_column(Integer, default=0)
    queue_delivered: Mapped[int] = mapped_column(Integer, default=0)
    queue_failed: Mapped[int] = mapped_column(Integer, default=0)
    agent_cpu_pct: Mapped[float | None] = mapped_column(Float)
    agent_mem_mb: Mapped[float | None] = mapped_column(Float)
    policy_blocked: Mapped[int] = mapped_column(Integer, default=0)
    counters_json: Mapped[str] = mapped_column(Text, default="{}")
    __table_args__ = (Index("ix_hb_collector_at", "collector_id", "at"),)
