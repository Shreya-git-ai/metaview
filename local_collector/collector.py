"""NEXORA Local Collector — independent process running inside the customer environment.

    python -m local_collector.collector --env config/col-alpha-01.env

Pipeline (per sample / per window):
    COLLECT → FILTER → REDACT → AGGREGATE → ANALYZE → APPLY POLICY → EGRESS AUDIT → QUEUE → SEND (TLS)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.contract import SCHEMA_VERSION, HeartbeatPayload, canonical_json, iso, sha256_hex, utc_now  # noqa: E402
from shared.envfile import load_env_file  # noqa: E402

from .config import CollectorConfig  # noqa: E402
from .egress_audit import EgressAudit  # noqa: E402
from .local_db import LocalDB  # noqa: E402
from .local_queue import LocalQueue  # noqa: E402
from .policy_engine import PolicyDecision, PolicyEngine  # noqa: E402
from .processor import Processor  # noqa: E402
from .redaction import Redactor  # noqa: E402
from .scenarios import ScenarioManager  # noqa: E402
from .sender import AuthRejected, SaaSTransport, TransportUnavailable  # noqa: E402
from .telemetry_generator import TelemetryGenerator  # noqa: E402

log = logging.getLogger("collector")


class Collector:
    def __init__(self, cfg: CollectorConfig, seed: int | None = None) -> None:
        self.cfg = cfg
        self.started_at = time.time()
        self.db = LocalDB(Path(cfg.data_dir) / "local.db")
        self.scenarios = ScenarioManager()
        self.generator = TelemetryGenerator(cfg.hosts, self.scenarios, seed=seed,
                                            include_local_host=cfg.include_local_host)
        roles = dict(cfg.hosts)
        self.redactor = Redactor(cfg.source_salt)
        self.processor = Processor(cfg.tenant_id, cfg.collector_id, cfg.source_salt, roles, self.redactor,
                                   cfg.window_s)
        self.policy = PolicyEngine(cfg.tenant_id, cfg.collector_id)
        self.audit = EgressAudit(self.db, destination=f"{cfg.saas_url}/api/v1/ingest")
        self.queue = LocalQueue(self.db)
        self.transport = SaaSTransport(cfg)
        self.stop = threading.Event()
        self.window_start = utc_now()
        self.window_id = uuid.uuid4().hex[:12]
        self.connectivity = {"state": "unknown", "last_ok_at": None, "last_error": None,
                             "last_heartbeat_ok_at": None, "consecutive_failures": 0}
        self.next_send_at = 0.0
        self.last_replay: dict | None = None
        self.policy_blocked = 0
        self.payloads_approved = 0

    # ================================================================ pipeline
    @property
    def paused(self) -> bool:
        return self.scenarios.get("collector_offline") is not None

    def tick(self) -> None:
        """One sampling tick: collect, filter, redact, store locally."""
        if self.paused:
            return
        for rec in self.generator.sample():
            details = self.processor.ingest_raw(rec)
            has_secret = bool(details.get("secret_count")) or any(
                f["kind"] == "secret" for f in details.get("findings", []))
            raw_id = self.db.add_raw(rec, self.window_id, has_secret)
            if rec["kind"] == "log" or has_secret:
                self.db.add_redaction(raw_id, rec, self.window_id, details)

    def close_window(self) -> list[PolicyDecision]:
        """Aggregate + analyse the window, apply policy, write egress audit, enqueue approved payloads."""
        if self.paused:
            return []
        end = utc_now()
        candidates, canaries = self.processor.build_payloads(self.window_start, end, self.window_id)
        if self.scenarios.get("secret_injection") and candidates:
            # SIMULATED misconfigured enrichment plugin: tries to attach a raw log line to a payload.
            bad = dict(candidates[0])
            bad["event_id"] = str(uuid.uuid4())
            bad["raw_log"] = self._latest_secret_line() or "password=SYNTH-unknown"
            candidates.append(bad)
        decisions = []
        for p in candidates:
            d = self.policy.evaluate(p, canaries)
            self.audit.record(p, d, self.window_id)
            if d.allowed:
                self.payloads_approved += 1
                self.queue.enqueue(p["event_id"], d.canonical, d.sha256, p["event_time"])
            else:
                self.policy_blocked += 1
                log.warning("policy BLOCKED payload %s: %s", p.get("event_id"), "; ".join(d.reasons))
            decisions.append(d)
        self.window_start = end
        self.window_id = uuid.uuid4().hex[:12]
        self.db.prune()
        return decisions

    def _latest_secret_line(self) -> str | None:
        r = self.db.one("SELECT body FROM raw_records WHERE has_secret=1 AND kind='log' ORDER BY id DESC LIMIT 1")
        if not r:
            return None
        import json
        return json.loads(r["body"]).get("line")

    # ================================================================ sending
    def flush_outbox(self) -> dict | None:
        """Send due approved payloads. Returns a summary or None if nothing was attempted."""
        if self.paused or time.time() < self.next_send_at:
            return None
        self.transport.partitioned = self.scenarios.get("saas_outage") is not None
        rows = self.queue.due(limit=200)
        if not rows:
            return None
        ids = [r["event_id"] for r in rows]
        try:
            status, data = self.transport.ingest([r["payload_json"] for r in rows])
        except (TransportUnavailable, AuthRejected) as e:
            self._send_failed(rows, str(e))
            return {"sent": 0, "error": str(e)}
        if status != 200:
            self._send_failed(rows, f"HTTP {status}: {data}")
            return {"sent": 0, "error": f"HTTP {status}"}
        results = {r["event_id"]: r for r in data.get("results", [])}
        delivered, failed = {}, {}
        for eid in ids:
            res = results.get(eid, {"status": "missing"})
            if res["status"] in ("accepted", "duplicate"):
                delivered[eid] = res["status"]
            else:
                failed[eid] = res.get("reason", res["status"])
        self.queue.mark_delivered(delivered)
        self.queue.mark_failed(failed)
        for st in ("accepted", "duplicate"):
            eids = [e for e, s in delivered.items() if s == st]
            self.audit.mark(eids, "delivered" if st == "accepted" else "delivered_duplicate_ack",
                            http_status=status, saas_result=st, transmitted=True)
        for eid, reason in failed.items():
            self.audit.mark([eid], "rejected_by_saas", http_status=status, saas_result=reason, transmitted=True)
        self._ok()
        return {"sent": len(ids), "accepted": data.get("accepted"), "duplicates": data.get("duplicates"),
                "rejected": data.get("rejected")}

    def _send_failed(self, rows: list[dict], error: str) -> None:
        self.queue.mark_retry(rows, error)
        self.audit.mark([r["event_id"] for r in rows], "retrying", error=error)
        c = self.connectivity
        c["consecutive_failures"] += 1
        c["state"] = "unreachable"
        c["last_error"] = error
        self.next_send_at = time.time() + min(16.0, 2 ** (c["consecutive_failures"] - 1))
        log.warning("send failed (%d rows buffered, retry in %.0fs): %s", len(rows),
                    self.next_send_at - time.time(), error)

    def _ok(self) -> None:
        c = self.connectivity
        if c["state"] != "connected":
            log.info("SaaS connectivity: connected")
        c.update(state="connected", last_ok_at=iso(utc_now()), last_error=None, consecutive_failures=0)
        self.next_send_at = 0.0

    def send_heartbeat(self) -> None:
        if self.paused:
            return
        self.transport.partitioned = self.scenarios.get("saas_outage") is not None
        q = self.queue.counts()
        st = self.processor.stats
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            cpu, mem = proc.cpu_percent(interval=None), proc.memory_info().rss / 1e6
        except Exception:  # pragma: no cover
            cpu, mem = 0.0, 0.0
        hb = {"schema_version": SCHEMA_VERSION, "tenant_id": self.cfg.tenant_id, "collector_id": self.cfg.collector_id,
              "sent_at": iso(utc_now()), "version": self.cfg.version, "uptime_s": int(time.time() - self.started_at),
              "agent_cpu_pct": float(round(cpu, 2)), "agent_mem_mb": float(round(mem, 1)),
              "queue_pending": q["pending"], "queue_retrying": q["retrying"], "queue_delivered": q["delivered"],
              "queue_failed": q["failed"], "policy_blocked": self.policy_blocked, "config_hash": self.cfg.config_hash,
              "raw_records_total": int(st.get("raw_records", 0)), "fields_dropped_total": int(st.get("fields_dropped", 0)),
              "secrets_redacted_total": int(st.get("secrets_redacted", 0)),
              "identifiers_pseudonymised_total": int(st.get("identifiers_pseudonymised", 0)),
              "payloads_approved_total": self.payloads_approved}
        if self.connectivity.get("last_ok_at"):
            hb["last_successful_send_at"] = self.connectivity["last_ok_at"]
        HeartbeatPayload.model_validate(hb)
        canonical = canonical_json(hb)
        decision = PolicyDecision(allowed=True, canonical=canonical, sha256=sha256_hex(canonical),
                                  checks={"schema": "pass"})
        self.audit.record({**hb, "event_id": None, "category": "heartbeat"}, decision, None, kind="heartbeat")
        try:
            code = self.transport.heartbeat(canonical)
            self.audit.mark_heartbeat_last("delivered" if code == 200 else "rejected_by_saas", code, None)
            if code == 200:
                self.connectivity["last_heartbeat_ok_at"] = iso(utc_now())
                self._ok()
        except (TransportUnavailable, AuthRejected) as e:
            self.audit.mark_heartbeat_last("failed", None, str(e))
            self.connectivity.update(state="unreachable", last_error=str(e))

    def replay_duplicates(self, n: int = 20) -> dict:
        """Re-send already delivered payloads byte-for-byte (idempotency demonstration)."""
        rows = self.queue.last_delivered_payloads(n)
        if not rows:
            self.last_replay = {"sent": 0, "note": "nothing delivered yet"}
            return self.last_replay
        try:
            status, data = self.transport.ingest([r["payload_json"] for r in rows])
            self.last_replay = {"at": iso(utc_now()), "sent": len(rows), "http_status": status,
                                "accepted": data.get("accepted"), "duplicates": data.get("duplicates"),
                                "rejected": data.get("rejected")}
        except (TransportUnavailable, AuthRejected) as e:
            self.last_replay = {"at": iso(utc_now()), "sent": 0, "error": str(e)}
        return self.last_replay

    # ================================================================ threads
    def _loop(self, interval: float, fn, name: str) -> None:
        while not self.stop.is_set():
            t0 = time.time()
            try:
                fn()
            except Exception:  # keep the collector alive whatever happens
                log.exception("%s loop error", name)
            self.stop.wait(max(0.05, interval - (time.time() - t0)))

    def _pipeline_step(self) -> None:
        self.tick()
        if (utc_now() - self.window_start).total_seconds() >= self.cfg.window_s:
            self.close_window()

    def start_background(self) -> list[threading.Thread]:
        threads = [
            threading.Thread(target=self._loop, args=(self.cfg.sample_interval_s, self._pipeline_step, "pipeline"),
                             daemon=True, name="pipeline"),
            threading.Thread(target=self._loop, args=(0.5, self.flush_outbox, "sender"), daemon=True, name="sender"),
            threading.Thread(target=self._loop, args=(self.cfg.heartbeat_s, self.send_heartbeat, "heartbeat"),
                             daemon=True, name="heartbeat"),
        ]
        for t in threads:
            t.start()
        return threads


def main() -> None:
    ap = argparse.ArgumentParser(description="NEXORA local collector")
    ap.add_argument("--env", help="env file, e.g. config/col-alpha-01.env")
    args = ap.parse_args()
    if args.env:
        load_env_file(ROOT / args.env if not Path(args.env).is_absolute() else args.env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = CollectorConfig.from_env()
    col = Collector(cfg)
    log.info("collector %s (tenant %s) starting — SaaS %s, console http://127.0.0.1:%d",
             cfg.collector_id, cfg.tenant_id, cfg.saas_url, cfg.console_port)
    col.start_background()

    import uvicorn

    from .console_api import create_console_app
    # The local console binds to loopback only: raw data never leaves this machine via the console.
    uvicorn.run(create_console_app(col), host="127.0.0.1", port=cfg.console_port, log_level="warning")
    col.stop.set()


if __name__ == "__main__":
    main()
