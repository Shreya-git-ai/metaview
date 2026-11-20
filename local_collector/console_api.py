"""Local Collector Console API — bound to 127.0.0.1 only.

This is the Tier-1 view: it may show raw local telemetry (with SYNTHETIC secrets) because it
never leaves the customer machine. The SaaS backend has no route to any of this data.
"""
from __future__ import annotations

import json
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.contract import schema_description

from .processor import RAW_METRIC_ALLOW
from .redaction import rules_description
from .scenarios import CATALOG
from .sender import AuthRejected, TransportUnavailable


class ScenarioRequest(BaseModel):
    target: str | None = None
    duration_s: float | None = None


def create_console_app(col) -> FastAPI:  # col: Collector
    app = FastAPI(title=f"NEXORA Local Console — {col.cfg.collector_id}", docs_url="/local/docs",
                  openapi_url="/local/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
                       allow_methods=["*"], allow_headers=["*"])

    def host_map() -> list[dict]:
        return [{"hostname_local_only": h, "role": col.processor.roles.get(h, "host"),
                 "source_id": col.processor.source_id(h)} for h in col.generator.hostnames]

    @app.get("/local/status")
    def status():
        return {
            "tier": "TIER 1 — LOCAL COLLECTOR (customer environment)",
            "collector_id": col.cfg.collector_id, "tenant_id": col.cfg.tenant_id, "version": col.cfg.version,
            "config_hash": col.cfg.config_hash, "uptime_s": int(time.time() - col.started_at),
            "saas_url": col.cfg.saas_url, "transport": "HTTPS (TLS ≥1.2, server cert verified against local CA)",
            "registered": col.transport.registered, "paused": col.paused,
            "partitioned": col.scenarios.get("saas_outage") is not None,
            "connectivity": col.connectivity, "queue": col.queue.counts(), "egress": col.audit.counts(),
            "stats": dict(col.processor.stats), "policy_blocked": col.policy_blocked,
            "window_s": col.cfg.window_s, "sample_interval_s": col.cfg.sample_interval_s,
            "hosts": host_map(),
            "active_scenarios": [{"name": a.name, "target": a.target, "remaining_s": round(a.until - time.time())}
                                 for a in col.scenarios.active()],
            "last_replay": col.last_replay,
        }

    @app.get("/local/raw")
    def raw(limit: int = 40, secrets_only: bool = False, kind: str | None = None):
        sql, params = "SELECT * FROM raw_records WHERE 1=1", []
        if secrets_only:
            sql += " AND has_secret=1"
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(min(limit, 500))
        rows = col.db.query(sql, params)
        for r in rows:
            r["body"] = json.loads(r["body"])
            r["has_secret"] = bool(r["has_secret"])
        return rows

    @app.get("/local/redactions")
    def redactions(limit: int = 40, secrets_only: bool = False):
        sql = "SELECT * FROM redaction_log" + (" WHERE secret_count>0" if secrets_only else "") + \
              " ORDER BY id DESC LIMIT ?"
        rows = col.db.query(sql, (min(limit, 500),))
        for r in rows:
            r["findings"] = json.loads(r["findings"])
            r["dropped_fields"] = json.loads(r["dropped_fields"] or "[]")
        return rows

    @app.get("/local/filter-example")
    def filter_example():
        return col.processor.last_filter_example or {}

    @app.get("/local/aggregates")
    def aggregates():
        return col.processor.last_aggregates

    @app.get("/local/egress")
    def egress(limit: int = 30, include_heartbeats: bool = False, status: str | None = None):
        return col.audit.recent(min(limit, 500), include_heartbeats, status)

    @app.get("/local/egress/{event_id}")
    def egress_one(event_id: str):
        r = col.audit.by_event(event_id)
        if not r:
            raise HTTPException(404, "unknown event id")
        return r

    @app.get("/local/proof")
    def proof():
        """Links the most recent raw record containing a synthetic secret to its redaction and to the
        exact egress payload produced for the same source and window."""
        raw_row = col.db.one("SELECT * FROM raw_records WHERE has_secret=1 ORDER BY id DESC LIMIT 1")
        if not raw_row:
            return {"available": False, "hint": "Trigger the 'Sensitive log injection' scenario."}
        raw_row["body"] = json.loads(raw_row["body"])
        red = col.db.one("SELECT * FROM redaction_log WHERE raw_id=?", (raw_row["id"],))
        if red:
            red["findings"] = json.loads(red["findings"])
        sid = col.processor.source_id(raw_row["host"])
        payload = col.db.one("SELECT event_id FROM egress_audit WHERE window_id=? AND source_id=? AND kind='event' "
                             "AND policy_allowed=1 AND category='metrics' ORDER BY id LIMIT 1",
                             (raw_row["window_id"], sid))
        blocked = col.db.query("SELECT event_id FROM egress_audit WHERE window_id=? AND policy_allowed=0 "
                               "AND kind='event'", (raw_row["window_id"],))
        recent_canaries = col.generator.injected_secrets[-10:]
        return {
            "available": True,
            "raw": raw_row, "redaction": red, "source_id": sid,
            "egress": col.audit.by_event(payload["event_id"]) if payload else None,
            "blocked": [col.audit.by_event(b["event_id"]) for b in blocked if b["event_id"]],
            "synthetic_secrets_local_only": recent_canaries,
        }

    @app.post("/local/verify/{event_id}")
    def verify(event_id: str):
        """Tier-1 asks Tier-2 what it stored for this event id and compares SHA-256 of the exact bytes."""
        local = col.audit.by_event(event_id)
        if not local:
            raise HTTPException(404, "unknown event id")
        try:
            remote = col.transport.received(event_id)
        except (TransportUnavailable, AuthRejected) as e:
            return {"event_id": event_id, "local_sha256": local["sha256"], "error": str(e)}
        canaries = col.generator.injected_secrets
        remote_json = json.dumps(remote.get("payload") or {})
        return {"event_id": event_id, "local_sha256": local["sha256"], "saas_sha256": remote.get("payload_sha256"),
                "match": remote.get("payload_sha256") == local["sha256"], "saas_http_status": remote["http_status"],
                "synthetic_secrets_found_in_saas_copy": sum(1 for c in canaries if c in remote_json)}

    @app.get("/local/policy")
    def policy():
        return {"contract": schema_description(), "redaction_rules": rules_description(),
                "raw_metric_filter_allow_list": sorted(RAW_METRIC_ALLOW),
                "policy_checks": ["schema (extra=forbid, strict types, bounds)", "identity (tenant/collector)",
                                  "secret scan over final bytes", "canary match against removed secrets"]}

    @app.get("/local/queue")
    def queue(limit: int = 30, status: str | None = None):
        return {"counts": col.queue.counts(), "items": col.queue.recent(min(limit, 200), status),
                "next_send_at": col.next_send_at, "connectivity": col.connectivity}

    @app.get("/local/scenarios")
    def scenarios():
        return {"catalog": CATALOG, "hosts": col.generator.hostnames,
                "active": [{"name": a.name, "target": a.target, "remaining_s": round(a.until - time.time())}
                           for a in col.scenarios.active()],
                "history": col.scenarios.history[-10:]}

    @app.post("/local/scenarios/{name}")
    def trigger(name: str, req: ScenarioRequest | None = None):
        if name not in CATALOG:
            raise HTTPException(404, "unknown scenario")
        req = req or ScenarioRequest()
        target = req.target
        if CATALOG[name]["scope"] == "host":
            target = target or col.generator.hostnames[min(1, len(col.generator.hostnames) - 1)]
            if target not in col.generator.hostnames:
                raise HTTPException(400, "unknown target host")
        elif CATALOG[name]["scope"] != "all":
            target = None
        if name == "duplicate_replay":
            col.scenarios.trigger(name, None, 0)
            return {"scenario": name, "result": col.replay_duplicates()}
        act = col.scenarios.trigger(name, target, req.duration_s)
        return {"scenario": name, "target": target,
                "until": act.until if act else None, "active": [a.name for a in col.scenarios.active()]}

    @app.post("/local/reconcile")
    def reconcile():
        """Post-outage delivery check: every event the collector marked delivered must exist at SaaS."""
        ids = col.queue.delivered_ids(5000)
        try:
            res = col.transport.reconcile(ids)
        except (TransportUnavailable, AuthRejected) as e:
            raise HTTPException(503, str(e))
        res["queue"] = col.queue.counts()
        return res

    return app
