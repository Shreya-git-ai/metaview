"""SaaS-side health classification with stale-data handling and transition recording."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.models.models import Collector, HealthTransition, Source, Threshold
from shared.health import DEFAULT_THRESHOLDS, HEALTH_ORDER, classify, downgrade, worse

STALE_POOR_FACTOR = 4   # no data for 4×stale_after → POOR


def tenant_thresholds(db: Session, tenant_id: str) -> dict:
    t = dict(DEFAULT_THRESHOLDS)
    for row in db.scalars(select(Threshold).where(Threshold.tenant_id == tenant_id)).all():
        t[row.metric] = (row.excellent_below, row.good_below, row.fair_below)
    return t


def effective_health(src: Source, collector: Collector | None, thresholds: dict, now: datetime,
                     stale_after_s: float) -> tuple[str, str | None, str, bool]:
    """(state, trigger, reason, stale). Never presents an old healthy state as current."""
    latest = json.loads(src.latest_metrics_json or "{}")
    state, trigger, reason = classify(latest, thresholds)
    if collector is not None and collector.status == "offline":
        return "poor", "collector_offline", f"Collector {collector.id} offline — no current telemetry " \
                                            f"(last known: {state.capitalize()})", True
    if src.last_event_time is None:
        return "fair", "no_data", "No telemetry received yet", True
    age = (now - src.last_event_time).total_seconds()
    if age > stale_after_s * STALE_POOR_FACTOR:
        return "poor", "stale", f"No telemetry for {age:.0f}s (last known: {state.capitalize()})", True
    if age > stale_after_s:
        return (worse(downgrade(state), "fair"), "stale",
                f"Telemetry stale ({age:.0f}s > {stale_after_s:.0f}s); last known {state.capitalize()}: {reason}", True)
    return state, trigger, reason, False


def evaluate_source(db: Session, src: Source, collector: Collector | None, thresholds: dict, now: datetime,
                    stale_after_s: float) -> bool:
    state, trigger, reason, stale = effective_health(src, collector, thresholds, now, stale_after_s)
    changed = state != src.health_state
    if changed:
        db.add(HealthTransition(tenant_id=src.tenant_id, entity_type="source", entity_id=src.source_id,
                                prev_state=src.health_state, new_state=state, trigger_metric=trigger,
                                reason=reason[:300], at=now))
        src.health_changed_at = now
    src.health_state, src.health_trigger, src.health_reason, src.stale = state, trigger, reason[:300], stale
    return changed


def summarise(sources: list[Source]) -> dict:
    dist = {s: 0 for s in HEALTH_ORDER}
    worst, worst_src = "excellent", None
    for s in sources:
        dist[s.health_state] += 1
        if HEALTH_ORDER.index(s.health_state) > HEALTH_ORDER.index(worst):
            worst, worst_src = s.health_state, s
    reason = (f"Worst source {worst_src.source_id}: {worst_src.health_reason}" if worst_src
              else "All sources healthy" if sources else "No sources reporting yet")
    return {"state": worst if sources else "fair", "distribution": dist, "reason": reason,
            "stale_sources": sum(1 for s in sources if s.stale)}
