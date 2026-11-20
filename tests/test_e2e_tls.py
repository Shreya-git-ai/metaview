"""End-to-end over a REAL TLS socket: collector → TLS → SaaS. Covers TLS, buffering, retry, duplicates, egress proof."""
import json
import sqlite3
import time

import pytest

from local_collector.config import PlaintextRefused
from scripts.gen_certs import generate
from shared.contract import canonical_json, sha256_hex


def _run_windows(col, n=2, ticks=3):
    for _ in range(n):
        for _ in range(ticks):
            col.tick()
        col.close_window()


def test_plaintext_is_refused(collector_factory):
    with pytest.raises(PlaintextRefused):
        collector_factory("http://localhost:8443")


def test_untrusted_certificate_is_rejected(tls_saas, collector_factory, tmp_path):
    other_ca = generate(tmp_path / "other", force=True, ca_cn="Some Other CA")["ca_cert"]
    col = collector_factory(tls_saas["url"], ca=other_ca)
    _run_windows(col, 1)
    res = col.flush_outbox()
    assert res["sent"] == 0 and "CERTIFICATE_VERIFY_FAILED" in res["error"]
    assert col.queue.counts()["retrying"] > 0     # data kept locally, not sent in plaintext


def test_e2e_secret_stays_local_and_egress_proof_matches(tls_saas, collector_factory):
    """THE key acceptance test: raw log with a password → outgoing payload has no password → SaaS receives only
    approved metadata → received bytes hash-match the egress audit."""
    col = collector_factory(tls_saas["url"])
    host = col.generator.hostnames[1]
    col.scenarios.trigger("secret_injection", host, 30)
    time.sleep(5.2)
    _run_windows(col, 2, 4)
    col.send_heartbeat()
    res = col.flush_outbox()
    assert res["sent"] > 0 and res["rejected"] == 0, res
    secrets = col.generator.injected_secrets
    assert any(s.startswith("SYNTH!") for s in secrets)          # synthetic passwords were in the raw logs
    raw = "".join(r["body"] for r in col.db.query("SELECT body FROM raw_records"))
    assert any(s in raw for s in secrets)

    saas_db = sqlite3.connect(tls_saas["settings"].db_path)
    rows = saas_db.execute("SELECT event_id, payload_json, payload_sha256 FROM received_events").fetchall()
    assert rows
    # 1. no secret anywhere in the SaaS database file (all tables, all bytes)
    saas_db.close()
    blob = open(tls_saas["settings"].db_path, "rb").read()
    wal = tls_saas["settings"].db_path.with_name("saas.db-wal")
    if wal.exists():
        blob += wal.read_bytes()
    for s in secrets:
        assert s.encode() not in blob, f"secret leaked to SaaS storage: {s[:6]}…"
    assert b"internal" not in blob                               # hostnames never left the collector
    # 2. received == approved egress payload, byte-for-byte (SHA-256)
    for event_id, payload_json, sha in rows:
        audit = col.audit.by_event(event_id)
        assert audit is not None and audit["status"] == "delivered"
        assert sha == audit["sha256"] == sha256_hex(payload_json)
        assert json.loads(payload_json) == audit["payload"]
        assert set(json.loads(payload_json)) <= {"schema_version", "event_id", "tenant_id", "collector_id", "source_id",
                                                 "source_role", "category", "window_start", "event_time", "metrics",
                                                 "health_state", "severity", "context_code", "context_params",
                                                 "processing"}
    # privacy-safe processing evidence reached SaaS as counts + rule ids only
    procs = [json.loads(r[1]).get("processing") for r in rows]
    assert any(p and p["secrets_redacted"] > 0 and "R-PASSWORD" in p["rules_triggered"] for p in procs)
    # 3. collector-side verification via the API
    v = col.transport.received(rows[0][0])
    assert v["payload_sha256"] == col.audit.by_event(rows[0][0])["sha256"]
    # 4. the simulated leaky plugin payload never reached SaaS
    blocked = [b["event_id"] for b in col.audit.recent(status="blocked")]
    assert blocked and not set(blocked) & {r[0] for r in rows}


def test_outage_buffering_retry_and_no_duplicates(tls_saas, collector_factory):
    col = collector_factory(tls_saas["url"])
    _run_windows(col, 1)
    assert col.flush_outbox()["sent"] > 0
    # --- outage (simulated partition at the collector egress)
    col.scenarios.trigger("saas_outage", None, 60)
    _run_windows(col, 3)
    res = col.flush_outbox()
    assert res["sent"] == 0 and "partition" in res["error"]
    buffered = col.queue.counts()["retrying"]
    assert buffered >= 3
    event_times = [r["event_time"] for r in col.db.query("SELECT event_time FROM outbox WHERE status='retrying'")]
    # --- reconnect
    col.scenarios.trigger("normal")
    col.next_send_at = 0
    col.db.execute("UPDATE outbox SET next_attempt_at=0")       # skip the backoff wait in the test
    res = col.flush_outbox()
    assert res["accepted"] == buffered and res["duplicates"] == 0
    assert col.queue.counts()["retrying"] == 0
    # original event timestamps preserved at SaaS
    saas = sqlite3.connect(tls_saas["settings"].db_path)
    stored_times = {r[0] for r in saas.execute("SELECT event_time FROM received_events")}
    assert len(stored_times) >= len(set(event_times))
    # --- replay already-delivered payloads: idempotent
    rep = col.replay_duplicates(10)
    assert rep["accepted"] == 0 and rep["duplicates"] == rep["sent"] > 0
    total = saas.execute("SELECT COUNT(*) FROM received_events").fetchone()[0]
    assert total == col.queue.counts()["delivered"]              # zero lost, zero duplicates stored
    rec = col.transport.reconcile(col.queue.delivered_ids())
    assert rec["missing"] == 0 and rec["present"] == rec["checked"] == total


def test_backoff_is_exponential():
    from local_collector.local_queue import backoff_delay
    d = [backoff_delay(a) for a in range(1, 8)]
    assert d[0] < 1.5 and 1.9 < d[1] < 2.5 and 3.9 < d[2] < 4.5 and all(x <= 16.5 for x in d)


def test_canonical_json_is_deterministic():
    a = {"b": 1, "a": {"y": 2.5, "x": True}}
    assert canonical_json(a) == canonical_json(json.loads(canonical_json(a))) == '{"a":{"x":true,"y":2.5},"b":1}'
