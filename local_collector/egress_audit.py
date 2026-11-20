"""Egress proof: an append-only record of the EXACT bytes prepared for transmission."""
from __future__ import annotations

import json

from shared.contract import iso, utc_now

from .local_db import LocalDB
from .policy_engine import PolicyDecision


class EgressAudit:
    def __init__(self, db: LocalDB, destination: str) -> None:
        self.db = db
        self.destination = destination

    def record(self, payload: dict, decision: PolicyDecision, window_id: str | None, kind: str = "event") -> None:
        status = ("queued" if kind == "event" else "sending") if decision.allowed else "blocked"
        self.db.execute(
            "INSERT INTO egress_audit(event_id, kind, window_id, tenant_id, collector_id, source_id, category, "
            "destination, created_at, payload_json, sha256, policy_allowed, policy_reasons, policy_checks, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (payload.get("event_id"), kind, window_id, payload.get("tenant_id"), payload.get("collector_id"),
             payload.get("source_id"), payload.get("category"), self.destination, iso(utc_now()),
             decision.canonical, decision.sha256, int(decision.allowed), json.dumps(decision.reasons),
             json.dumps(decision.checks), status))

    def mark(self, event_ids: list[str], status: str, http_status: int | None = None,
             saas_result: str | None = None, error: str | None = None, transmitted: bool = False) -> None:
        if not event_ids:
            return
        now = iso(utc_now())
        self.db.executemany(
            "UPDATE egress_audit SET status=?, attempts=attempts+1, last_attempt_at=?, http_status=COALESCE(?, http_status), "
            "saas_result=COALESCE(?, saas_result), last_error=?, transmitted_at=CASE WHEN ? THEN ? ELSE transmitted_at END "
            "WHERE event_id=? AND kind='event'",
            [(status, now, http_status, saas_result, error, int(transmitted), now, eid) for eid in event_ids])

    def mark_heartbeat_last(self, status: str, http_status: int | None, error: str | None) -> None:
        self.db.execute(
            "UPDATE egress_audit SET status=?, http_status=?, last_error=?, attempts=1, last_attempt_at=? "
            "WHERE id=(SELECT MAX(id) FROM egress_audit WHERE kind='heartbeat')",
            (status, http_status, error, iso(utc_now())))

    def recent(self, limit: int = 50, include_heartbeats: bool = False, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM egress_audit WHERE 1=1"
        params: list = []
        if not include_heartbeats:
            sql += " AND kind='event'"
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [_decode(r) for r in self.db.query(sql, params)]

    def by_event(self, event_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM egress_audit WHERE event_id=? AND kind='event'", (event_id,))
        return _decode(r) if r else None

    def counts(self) -> dict:
        rows = self.db.query("SELECT status, COUNT(*) n FROM egress_audit WHERE kind='event' GROUP BY status")
        return {r["status"]: r["n"] for r in rows}


def _decode(r: dict) -> dict:
    r = dict(r)
    r["payload"] = json.loads(r.pop("payload_json"))
    r["payload_bytes"] = len(json.dumps(r["payload"], sort_keys=True, separators=(",", ":")))
    r["policy_reasons"] = json.loads(r["policy_reasons"] or "[]")
    r["policy_checks"] = json.loads(r["policy_checks"] or "{}")
    r["policy_allowed"] = bool(r["policy_allowed"])
    r["saas_result"] = r.get("saas_result")
    return r
