"""SaaS: collector auth, schema validation, tenant isolation, idempotency, ordering, health, AIOps."""
from datetime import datetime, timedelta, timezone

from saas_backend.database.db import utcnow
from saas_backend.models.models import Collector, Source
from saas_backend.services.maintenance import run_maintenance

from conftest import login, payload, register, send


def test_collector_authentication(client):
    assert client.post("/api/v1/collectors/register", json={
        "tenant_id": "tenant-alpha", "collector_id": "col-x", "enrollment_token": "enr_wrong_SYNTHETIC",
        "version": "1.0.0"}).status_code == 401
    # enrollment token of beta cannot enroll into alpha
    from conftest import ENROLL
    assert client.post("/api/v1/collectors/register", json={
        "tenant_id": "tenant-alpha", "collector_id": "col-x", "enrollment_token": ENROLL["tenant-beta"],
        "version": "1.0.0"}).status_code == 401
    assert send(client, "col_bogus_token", [payload()]).status_code == 401
    tok = register(client)
    assert client.post("/api/v1/collectors/auth", headers={"Authorization": f"Bearer {tok}"}).json()["authenticated"]


def test_invalid_metadata_rejected(client):
    tok = register(client)
    bad = payload()
    bad["raw_log"] = "password=SYNTH"
    bad2 = payload()
    bad2["metrics"]["username"] = "alice"
    r = send(client, tok, [bad, bad2, payload()]).json()
    assert r["accepted"] == 1 and r["rejected"] == 2
    assert "extra_forbidden" in r["results"][0]["reason"]
    assert client.post("/api/v1/ingest", content='{"events":[],"extra":1}',
                       headers={"Authorization": f"Bearer {tok}"}).status_code == 400


def test_collector_cannot_write_other_tenant(client):
    tok = register(client)
    r = send(client, tok, [payload(tenant="tenant-beta", collector="col-alpha-01")]).json()
    assert r["rejected"] == 1 and "identity" in r["results"][0]["reason"]
    r = send(client, tok, [payload(collector="col-other")]).json()
    assert r["rejected"] == 1


def test_duplicates_not_processed_twice_and_order_preserved(client):
    tok = register(client)
    now = datetime.now(timezone.utc)
    late = payload(t=now - timedelta(seconds=30), cpu_pct=11.0)
    fresh = payload(t=now, cpu_pct=22.0)
    assert send(client, tok, [fresh]).json()["accepted"] == 1
    r = send(client, tok, [late, fresh]).json()           # out-of-order + duplicate
    assert r["accepted"] == 1 and r["duplicates"] == 1
    assert send(client, tok, [late]).json()["duplicates"] == 1
    h = login(client)
    src = client.get("/api/v1/sources", headers=h).json()[0]
    assert src["metrics"]["cpu_pct"] == 22.0               # older event did not overwrite latest
    recv = client.get("/api/v1/privacy/received?limit=10", headers=h).json()
    assert len(recv) == 2
    assert {r["event_time"] for r in recv} == {late["event_time"], fresh["event_time"]}   # event time preserved


def test_tenant_isolation_enforced_in_backend(client):
    ta, tb = register(client), register(client, "tenant-beta", "col-beta-01")
    send(client, ta, [payload()])
    pb = payload(tenant="tenant-beta", collector="col-beta-01", source="src-bbbbbbbbbbbb")
    send(client, tb, [pb])
    ha, hb = login(client, "alpha.admin"), login(client, "beta.admin")
    # each sees only their own data
    assert [s["source_id"] for s in client.get("/api/v1/sources", headers=ha).json()] == ["src-aaaaaaaaaaaa"]
    assert [s["source_id"] for s in client.get("/api/v1/sources", headers=hb).json()] == ["src-bbbbbbbbbbbb"]
    # spoofing the tenant header is denied and audited
    assert client.get("/api/v1/overview", headers={**ha, "X-Tenant-Id": "tenant-beta"}).status_code == 403
    assert client.get("/api/v1/tenants/tenant-beta/metrics", headers=ha).status_code == 404
    assert client.get(f"/api/v1/privacy/received/{pb['event_id']}", headers=ha).status_code == 404
    assert [t["id"] for t in client.get("/api/v1/tenants", headers=ha).json()] == ["tenant-alpha"]
    denied = client.get("/api/v1/audit?action=cross_tenant_denied", headers=ha).json()
    assert len(denied) >= 3
    # a collector cannot read another collector's records either
    assert client.get(f"/api/v1/collectors/me/received/{pb['event_id']}",
                      headers={"Authorization": f"Bearer {ta}"}).status_code == 404
    # platform operator may switch tenant explicitly
    ho = login(client, "platform.operator")
    assert client.get("/api/v1/sources", headers={**ho, "X-Tenant-Id": "tenant-beta"}).json()[0]["source_id"] == "src-bbbbbbbbbbbb"
    assert client.get("/api/v1/sources", headers=ha | {"Authorization": "Bearer forged.jwt.token"}).status_code == 401


def test_health_transitions_and_thresholds(client):
    tok = register(client)
    send(client, tok, [payload(cpu_pct=40.0)])
    send(client, tok, [payload(cpu_pct=95.0)])
    h = login(client)
    tr = client.get("/api/v1/health/transitions", headers=h).json()
    assert tr[0]["new_state"] == "poor" and tr[0]["prev_state"] == "excellent" and tr[0]["trigger_metric"] == "cpu_pct"
    assert "cpu_pct" in tr[0]["reason"] and tr[0]["at"].endswith("Z")
    # configurable thresholds change the classification
    assert client.put("/api/v1/settings/thresholds", headers=h, json={
        "metric": "cpu_pct", "excellent_below": 96, "good_below": 97, "fair_below": 98}).status_code == 200
    assert client.put("/api/v1/settings/thresholds", headers=h, json={
        "metric": "cpu_pct", "excellent_below": 90, "good_below": 80, "fair_below": 98}).status_code == 400
    send(client, tok, [payload(cpu_pct=95.0)])
    assert client.get("/api/v1/sources", headers=h).json()[0]["health_state"] == "excellent"


def test_stale_data_and_offline_detection(client):
    tok = register(client)
    hb = {"schema_version": "1.0", "tenant_id": "tenant-alpha", "collector_id": "col-alpha-01",
          "sent_at": payload()["event_time"], "version": "1.0.0", "uptime_s": 1, "agent_cpu_pct": 1.0,
          "agent_mem_mb": 10.0, "queue_pending": 0, "queue_retrying": 0, "queue_delivered": 0, "queue_failed": 0,
          "policy_blocked": 0, "config_hash": "0123456789ab"}
    import json
    assert client.post("/api/v1/collectors/heartbeat", content=json.dumps(hb),
                       headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    send(client, tok, [payload()])
    SL = client.app.state.SessionLocal
    with SL() as db:   # simulate time passing: data 20 s old, heartbeat 40 s old
        s = db.get(Source, ("tenant-alpha", "src-aaaaaaaaaaaa"))
        s.last_event_time = utcnow() - timedelta(seconds=20)
        db.commit()
        run_maintenance(db, utcnow(), 30, 15)
        s = db.get(Source, ("tenant-alpha", "src-aaaaaaaaaaaa"))
        assert s.stale and s.health_state == "fair" and "stale" in s.health_reason.lower()
        c = db.get(Collector, "col-alpha-01")
        c.last_heartbeat_at = utcnow() - timedelta(seconds=40)
        db.commit()
        run_maintenance(db, utcnow(), 30, 15)
        assert db.get(Collector, "col-alpha-01").status == "offline"
        assert db.get(Source, ("tenant-alpha", "src-aaaaaaaaaaaa")).health_state == "poor"
    h = login(client)
    inc = client.get("/api/v1/aiops/incidents", headers=h).json()
    assert any(i["rule_id"] == "R-COL-01" for i in inc)
    perf = client.get("/api/v1/performance", headers=h).json()
    assert perf["offline_detection_s"]["last"] >= 30
    # heartbeat resumes -> online, collector incident resolved
    client.post("/api/v1/collectors/heartbeat", content=json.dumps(hb), headers={"Authorization": f"Bearer {tok}"})
    assert client.get("/api/v1/collectors", headers=h).json()[0]["status"] == "online"
    assert not any(i["rule_id"] == "R-COL-01" for i in client.get("/api/v1/aiops/incidents", headers=h).json())


def _baseline(client, tok, n=30):
    import random
    rnd = random.Random(1)
    t0 = datetime.now(timezone.utc) - timedelta(seconds=3 * (n + 5))
    evs = [payload(t=t0 + timedelta(seconds=3 * i), cpu_pct=45 + rnd.uniform(-4, 4), mem_pct=60 + rnd.uniform(-2, 2),
                   app_response_ms=150 + rnd.uniform(-20, 20), latency_ms=20 + rnd.uniform(-3, 3)) for i in range(n)]
    assert send(client, tok, evs).json()["accepted"] == n
    return t0 + timedelta(seconds=3 * n)


def test_anomaly_detection_against_baseline(client):
    tok = register(client)
    t = _baseline(client, tok)
    send(client, tok, [payload(t=t, cpu_pct=46.0)])                   # normal → nothing
    h = login(client)
    assert client.get("/api/v1/aiops/anomalies", headers=h).json() == []
    send(client, tok, [payload(t=t + timedelta(seconds=3), cpu_pct=95.0)])
    a = client.get("/api/v1/aiops/anomalies", headers=h).json()
    assert len(a) == 1 and a[0]["metric"] == "cpu_pct"
    assert a[0]["expected_high"] < 95 < a[0]["observed"] + 0.01
    assert a[0]["robust_z"] >= 4 and a[0]["severity"] == "critical"
    assert "rolling median" in a[0]["reason"] and "robust z" in a[0]["reason"]
    assert "robust z-score" in a[0]["method"].lower()


def test_correlation_groups_related_signals_into_one_incident(client):
    tok = register(client)
    t = _baseline(client, tok)
    send(client, tok, [payload(t=t, cpu_pct=95.0, mem_pct=92.0, app_response_ms=4000.0)])
    h = login(client)
    incs = client.get("/api/v1/aiops/incidents", headers=h).json()
    assert len(incs) == 1
    i = client.get(f"/api/v1/aiops/incidents/{incs[0]['id']}", headers=h).json()
    assert i["rule_id"] == "R-RES-01" and len(i["anomalies"]) == 3
    assert i["cause_label"] == "hypothesis" and "not a confirmed root cause" in i["explanation"]
    codes = {r["action_code"] for r in i["recommendations"]}
    assert {"INVESTIGATE_PROCESSES", "SCALE_OUT"} <= codes
    assert all("Why:" in r["rationale"] for r in i["recommendations"])
    # safe remediation: approval -> simulated -> audited -> reversible
    rem = next(r for r in i["recommendations"] if r["requires_approval"])
    assert client.post(f"/api/v1/aiops/recommendations/{rem['id']}/execute", headers=h).json()["simulated"] is True
    assert client.post(f"/api/v1/aiops/recommendations/{rem['id']}/revert", headers=h).status_code == 200
    inv = next(r for r in i["recommendations"] if not r["requires_approval"])
    assert client.post(f"/api/v1/aiops/recommendations/{inv['id']}/execute", headers=h).status_code == 400
    acts = {a["action"] for a in client.get("/api/v1/audit", headers=h).json()}
    assert {"remediation_executed", "remediation_reverted", "incident_opened"} <= acts
    # another tenant cannot see or act on it
    hb = login(client, "beta.admin")
    assert client.get(f"/api/v1/aiops/incidents/{incs[0]['id']}", headers=hb).status_code == 404
    assert client.post(f"/api/v1/aiops/recommendations/{rem['id']}/execute", headers=hb).status_code == 404
