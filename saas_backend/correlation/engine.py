"""Rule-based event correlation, probable-cause hypotheses and explainable recommendations.

Correlation: an anomaly joins an open incident when it is on the same pseudonymous source, or is a
network signal on the same collector, and the incident was updated within CORRELATION_WINDOW_S.
The set of anomalous metrics in the incident is matched against ordered rule signatures; the first
rule whose required signals are all present supplies the title, the probable cause (always labelled
a HYPOTHESIS) and the recommendation set. Every recommendation quotes the evidence it is based on.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.ai_engine.detector import fmt
from saas_backend.models.models import Anomaly, AuditLog, Incident, Recommendation
from shared.contract import METRIC_LABELS
from shared.health import HEALTH_ORDER

CORRELATION_WINDOW_S = 120
AUTO_RESOLVE_QUIET_S = 90
NETWORK_METRICS = {"latency_ms", "packet_loss_pct"}


@dataclass(frozen=True)
class Action:
    code: str
    title: str
    why: str
    evidence: tuple[str, ...]
    kind: str = "investigate"          # investigate | remediate (remediate => approval + simulated)


ACTIONS = {
    "INVESTIGATE_PROCESSES": Action("INVESTIGATE_PROCESSES", "Investigate high-resource processes on the affected source",
                                    "CPU and/or memory are far outside their learned baseline; identifying the "
                                    "consuming process is the fastest way to confirm or reject the hypothesis.",
                                    ("cpu_pct", "mem_pct")),
    "REVIEW_WORKLOAD": Action("REVIEW_WORKLOAD", "Review recent application workload and deployments",
                              "Application response time degraded at the same time as resource usage rose, which is "
                              "typical of a load surge or an inefficient release.", ("app_response_ms", "cpu_pct")),
    "CHECK_MEMORY_LEAK": Action("CHECK_MEMORY_LEAK", "Check for memory leaks / heap growth",
                                "Memory rose well above baseline; a sustained rise without a matching load increase "
                                "often indicates a leak.", ("mem_pct",)),
    "SCALE_OUT": Action("SCALE_OUT", "Scale out the application tier (+1 instance)",
                        "Adds capacity to relieve resource saturation while the cause is investigated. Reversible.",
                        ("cpu_pct", "app_response_ms"), "remediate"),
    "RESTART_SERVICE": Action("RESTART_SERVICE", "Restart the affected application service",
                              "Releases leaked memory and stuck workers. Reversible but causes a brief interruption.",
                              ("mem_pct", "app_response_ms"), "remediate"),
    "CHECK_CONNECTIVITY": Action("CHECK_CONNECTIVITY", "Check network connectivity and path health",
                                 "Latency and/or packet loss rose across the affected sources; check uplinks, "
                                 "switch ports and upstream provider status.", ("latency_ms", "packet_loss_pct")),
    "REVIEW_LINK_UTILISATION": Action("REVIEW_LINK_UTILISATION", "Review link utilisation and QoS policy",
                                      "Congestion is a common driver of simultaneous latency and loss.",
                                      ("latency_ms",)),
    "FAILOVER_ROUTE": Action("FAILOVER_ROUTE", "Fail over to the secondary network path",
                             "Moves traffic away from the degraded path. Reversible (fail-back).",
                             ("packet_loss_pct", "latency_ms"), "remediate"),
    "INVESTIGATE_AUTH": Action("INVESTIGATE_AUTH", "Investigate repeated authentication failures",
                               "Failed logins far exceed the source's baseline from multiple distinct accounts/"
                               "sources — consistent with brute-force or credential stuffing.", ("auth_failures",)),
    "ENFORCE_LOCKOUT": Action("ENFORCE_LOCKOUT", "Enforce temporary account lockout / rate limiting",
                              "Rate-limiting stops guessing attacks while legitimate users are verified. Reversible.",
                              ("auth_failures",), "remediate"),
    "EXPAND_DISK": Action("EXPAND_DISK", "Free or expand disk capacity",
                          "Disk usage rose significantly above baseline; running out of space causes outages.",
                          ("disk_pct",)),
    "CHECK_COLLECTOR": Action("CHECK_COLLECTOR", "Check the collector host and its network path",
                              "No heartbeat has been received within the timeout; health of every source behind "
                              "this collector is unknown.", ()),
}


@dataclass(frozen=True)
class Rule:
    id: str
    requires: frozenset
    title: str
    cause: str
    actions: tuple[str, ...]


RULES: list[Rule] = [
    Rule("R-RES-01", frozenset({"cpu_pct", "mem_pct", "app_response_ms"}), "Application performance degradation",
         "Resource exhaustion — concurrent CPU and memory saturation is likely degrading application response time.",
         ("INVESTIGATE_PROCESSES", "REVIEW_WORKLOAD", "SCALE_OUT", "RESTART_SERVICE")),
    Rule("R-RES-02", frozenset({"cpu_pct", "app_response_ms"}), "CPU saturation degrading the application",
         "CPU saturation is likely slowing application request processing.",
         ("INVESTIGATE_PROCESSES", "REVIEW_WORKLOAD", "SCALE_OUT")),
    Rule("R-RES-03", frozenset({"mem_pct", "app_response_ms"}), "Memory pressure degrading the application",
         "Memory pressure (possible leak or swapping) is likely slowing the application.",
         ("CHECK_MEMORY_LEAK", "RESTART_SERVICE")),
    Rule("R-NET-01", frozenset({"latency_ms", "packet_loss_pct"}), "Network path degradation",
         "A degraded network path (congestion or a faulty link) is likely causing both higher latency and packet loss.",
         ("CHECK_CONNECTIVITY", "REVIEW_LINK_UTILISATION", "FAILOVER_ROUTE")),
    Rule("R-SEC-01", frozenset({"auth_failures"}), "Repeated authentication failures",
         "Possible brute-force or credential-stuffing attempt against login endpoints.",
         ("INVESTIGATE_AUTH", "ENFORCE_LOCKOUT")),
    Rule("R-RES-04", frozenset({"cpu_pct", "mem_pct"}), "Resource saturation",
         "A workload surge or runaway process is likely saturating CPU and memory.",
         ("INVESTIGATE_PROCESSES", "SCALE_OUT")),
    Rule("R-NET-02", frozenset({"latency_ms"}), "Elevated network latency",
         "Network congestion or routing change is likely increasing latency.",
         ("CHECK_CONNECTIVITY", "REVIEW_LINK_UTILISATION")),
    Rule("R-NET-03", frozenset({"packet_loss_pct"}), "Packet loss detected",
         "A faulty link or interface errors are likely dropping packets.", ("CHECK_CONNECTIVITY", "FAILOVER_ROUTE")),
    Rule("R-CPU-01", frozenset({"cpu_pct"}), "CPU saturation", "A runaway process or load surge is likely saturating CPU.",
         ("INVESTIGATE_PROCESSES", "SCALE_OUT")),
    Rule("R-MEM-01", frozenset({"mem_pct"}), "Memory pressure", "Memory growth — possibly a leak — on the source.",
         ("CHECK_MEMORY_LEAK", "RESTART_SERVICE")),
    Rule("R-APP-01", frozenset({"app_response_ms"}), "Slow application response",
         "The application is slower than baseline without a matching resource signal; a dependency may be slow.",
         ("REVIEW_WORKLOAD",)),
    Rule("R-DSK-01", frozenset({"disk_pct"}), "Disk capacity pressure", "Disk usage is growing abnormally.",
         ("EXPAND_DISK",)),
]
COLLECTOR_RULE = Rule("R-COL-01", frozenset(), "Collector offline — telemetry gap",
                      "The collector process, its host or its network path to SaaS is unavailable.",
                      ("CHECK_COLLECTOR",))


def match_rule(signals: set[str]) -> Rule | None:
    for r in RULES:
        if r.requires <= signals:
            return r
    return None


def _sev_max(a: str, b: str) -> str:
    order = ["info", "warning", "critical"]
    return a if order.index(a) >= order.index(b) else b


def _new_id() -> str:
    return "INC-" + uuid.uuid4().hex[:6].upper()


def correlate(db: Session, anomaly: Anomaly, collector_id: str, now: datetime) -> Incident:
    inc = db.get(Incident, anomaly.incident_id) if anomaly.incident_id else None
    if inc is None:
        candidates = db.scalars(select(Incident).where(
            Incident.tenant_id == anomaly.tenant_id, Incident.kind == "anomaly",
            Incident.status.in_(["open", "acknowledged"]),
            Incident.updated_at >= now - timedelta(seconds=CORRELATION_WINDOW_S))
            .order_by(Incident.updated_at.desc())).all()
        for c in candidates:
            sources = set(json.loads(c.source_ids_json))
            signals = {s["metric"] for s in json.loads(c.signals_json)}
            same_source = anomaly.source_id in sources
            network_wide = (anomaly.metric in NETWORK_METRICS and signals & NETWORK_METRICS
                            and c.collector_id == collector_id)
            if same_source or network_wide:
                inc = c
                break
        if inc is None:
            inc = Incident(id=_new_id(), tenant_id=anomaly.tenant_id, kind="anomaly", title="Anomalous behaviour",
                           status="open", severity=anomaly.severity, collector_id=collector_id,
                           opened_at=now, updated_at=now)
            db.add(inc)
            db.add(AuditLog(actor="aiops", tenant_id=inc.tenant_id, action="incident_opened", target=inc.id,
                            detail=json.dumps({"first_signal": anomaly.metric, "source_id": anomaly.source_id})))
        anomaly.incident_id = inc.id
        db.flush()
    refresh_incident(db, inc, now)
    return inc


def refresh_incident(db: Session, inc: Incident, now: datetime) -> None:
    anomalies = db.scalars(select(Anomaly).where(Anomaly.tenant_id == inc.tenant_id,
                                                 Anomaly.incident_id == inc.id)).all()
    if not anomalies:
        return
    signals, sources, sev = {}, [], "info"
    for a in sorted(anomalies, key=lambda a: a.detected_at):
        key = (a.metric, a.source_id)
        signals[key] = {"metric": a.metric, "label": METRIC_LABELS.get(a.metric, a.metric), "source_id": a.source_id,
                        "observed": a.observed, "peak": a.peak, "expected_low": a.expected_low,
                        "expected_high": a.expected_high, "robust_z": a.robust_z, "severity": a.severity,
                        "anomaly_id": a.id, "detected_at": a.detected_at.isoformat() + "Z"}
        if a.source_id not in sources:
            sources.append(a.source_id)
        sev = _sev_max(sev, a.severity)
    metric_set = {m for m, _ in signals}
    rule = match_rule(metric_set)
    sig_list = list(signals.values())
    inc.signals_json = json.dumps(sig_list)
    inc.source_ids_json = json.dumps(sources)
    inc.severity = _sev_max(inc.severity, sev)
    inc.updated_at = now
    evidence = "; ".join(f"{s['label']} on {s['source_id']}: peak {fmt(s['metric'], s['peak'])} vs expected "
                         f"{fmt(s['metric'], s['expected_low'])}–{fmt(s['metric'], s['expected_high'])}" for s in sig_list)
    if rule:
        matched = len(rule.requires & metric_set)
        explanation = (f"Grouped {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'} across "
                       f"{len(sources)} source(s) occurring within {CORRELATION_WINDOW_S}s. Matched correlation rule "
                       f"{rule.id} (requires {', '.join(sorted(METRIC_LABELS[m] for m in rule.requires))}; "
                       f"{matched}/{len(rule.requires)} present). Evidence — {evidence}. "
                       "The probable cause is a hypothesis inferred from correlated signals, not a confirmed root cause.")
        if rule.id != inc.rule_id:
            inc.rule_id, inc.title, inc.probable_cause = rule.id, rule.title, rule.cause
            _regenerate_recommendations(db, inc, rule, signals, now)
        inc.explanation = explanation
    else:
        inc.explanation = f"Anomalies grouped by source/time; no correlation rule matched. Evidence — {evidence}."


def _evidence_for(action: Action, signals: dict) -> str:
    parts = []
    for (metric, sid), s in signals.items():
        if metric in action.evidence:
            parts.append(f"{s['label']} {fmt(metric, s['peak'])} on {sid} (expected ≤ {fmt(metric, s['expected_high'])})")
    return "; ".join(parts)


def _regenerate_recommendations(db: Session, inc: Incident, rule: Rule, signals: dict, now: datetime) -> None:
    for r in db.scalars(select(Recommendation).where(Recommendation.incident_id == inc.id,
                                                     Recommendation.status == "proposed")).all():
        db.delete(r)
    existing = {r.action_code for r in db.scalars(select(Recommendation).where(
        Recommendation.incident_id == inc.id)).all()}
    for code in rule.actions:
        if code in existing:
            continue
        act = ACTIONS[code]
        ev = _evidence_for(act, signals)
        rationale = f"Why: {act.why}" + (f" Evidence: {ev}." if ev else "") + f" (rule {rule.id})"
        if act.kind == "remediate":
            rationale += " Requires approval; execution is SIMULATED in this MVP and can be reverted."
        db.add(Recommendation(tenant_id=inc.tenant_id, incident_id=inc.id, action_code=code, title=act.title,
                              rationale=rationale, kind=act.kind, requires_approval=act.kind == "remediate",
                              created_at=now, updated_at=now))


def open_collector_incident(db: Session, tenant_id: str, collector_id: str, last_hb: datetime | None,
                            now: datetime) -> Incident:
    existing = db.scalar(select(Incident).where(Incident.tenant_id == tenant_id, Incident.kind == "collector",
                                                Incident.collector_id == collector_id,
                                                Incident.status.in_(["open", "acknowledged"])))
    if existing:
        return existing
    silence = (now - last_hb).total_seconds() if last_hb else None
    inc = Incident(id=_new_id(), tenant_id=tenant_id, kind="collector", title=COLLECTOR_RULE.title,
                   status="open", severity="critical", rule_id=COLLECTOR_RULE.id, probable_cause=COLLECTOR_RULE.cause,
                   collector_id=collector_id, opened_at=now, updated_at=now,
                   signals_json=json.dumps([{"metric": "heartbeat", "label": "Heartbeat", "source_id": collector_id,
                                             "observed": silence, "severity": "critical"}]),
                   explanation=(f"No heartbeat from collector {collector_id} for {silence:.0f}s (timeout exceeded). "
                                "All sources behind it are marked stale/poor. Hypothesis — not confirmed."
                                if silence is not None else f"Collector {collector_id} has never sent a heartbeat."))
    db.add(inc)
    db.flush()
    act = ACTIONS["CHECK_COLLECTOR"]
    db.add(Recommendation(tenant_id=tenant_id, incident_id=inc.id, action_code=act.code, title=act.title,
                          rationale=f"Why: {act.why} (rule {COLLECTOR_RULE.id})", kind="investigate",
                          created_at=now, updated_at=now))
    db.add(AuditLog(actor="watchdog", tenant_id=tenant_id, action="incident_opened", target=inc.id,
                    detail=json.dumps({"collector_id": collector_id})))
    return inc


def resolve(db: Session, inc: Incident, now: datetime, note: str, actor: str) -> None:
    inc.status, inc.resolved_at, inc.resolution_note, inc.updated_at = "resolved", now, note, now
    db.add(AuditLog(actor=actor, tenant_id=inc.tenant_id, action="incident_resolved", target=inc.id,
                    detail=json.dumps({"note": note})))


__all__ = ["correlate", "refresh_incident", "open_collector_incident", "resolve", "RULES", "ACTIONS",
           "HEALTH_ORDER", "AUTO_RESOLVE_QUIET_S"]
