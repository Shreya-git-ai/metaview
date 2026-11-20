"""New enterprise endpoints: incidents workflow, source detail, fleet/heartbeats, privacy summary, platform health,
search, events — and tenant isolation on every one of them."""
import json

from conftest import login, payload, register, send
from test_saas_api import _baseline


def _hb(tok, client, **extra):
    hb = {"schema_version": "1.0", "tenant_id": "tenant-alpha", "collector_id": "col-alpha-01",
          "sent_at": payload()["event_time"], "version": "1.0.0", "uptime_s": 5, "agent_cpu_pct": 1.0,
          "agent_mem_mb": 10.0, "queue_pending": 0, "queue_retrying": 0, "queue_delivered": 3, "queue_failed": 0,
          "policy_blocked": 1, "config_hash": "0123456789ab", "secrets_redacted_total": 7, "raw_records_total": 90}
    hb.update(extra)
    return client.post("/api/v1/collectors/heartbeat", content=json.dumps(hb), headers={"Authorization": f"Bearer {tok}"})


def test_incident_workflow_filters_reopen_and_detection_metrics(client):
    tok = register(client)
    t = _baseline(client, tok)
    send(client, tok, [payload(t=t, cpu_pct=95.0, mem_pct=92.0, app_response_ms=4000.0)])
    h = login(client)
    inc = client.get("/api/v1/aiops/incidents?severity=critical", headers=h).json()[0]
    assert client.get("/api/v1/aiops/incidents?severity=warning", headers=h).json() == []
    assert client.get("/api/v1/aiops/incidents?q=performance", headers=h).json()[0]["id"] == inc["id"]
    d = client.get(f"/api/v1/aiops/incidents/{inc['id']}", headers=h).json()
    m = d["metrics_at_detection"][0]["metrics"]
    assert m["cpu_pct"]["value"] == 95.0 and m["cpu_pct"]["anomalous"] is True
    assert client.post(f"/api/v1/aiops/incidents/{inc['id']}/reopen", headers=h).status_code == 409   # not resolved
    client.post(f"/api/v1/aiops/incidents/{inc['id']}/resolve", headers=h)
    assert client.post(f"/api/v1/aiops/incidents/{inc['id']}/reopen", headers=h).json()["status"] == "open"
    stats = client.get("/api/v1/aiops/incidents/stats", headers=h).json()
    assert stats["open"] == 1 and stats["critical_active"] == 1
    assert client.post(f"/api/v1/aiops/incidents/{inc['id']}/reopen", headers=login(client, "beta.admin")).status_code == 404


def test_source_detail_events_and_search_are_tenant_scoped(client):
    tok = register(client)
    send(client, tok, [payload(cpu_pct=40.0), payload(cpu_pct=95.0)])
    h, hb = login(client), login(client, "beta.admin")
    d = client.get("/api/v1/sources/src-aaaaaaaaaaaa", headers=h).json()
    assert d["health_state"] == "poor" and d["transitions"] and "cpu_pct" in d["thresholds"]
    assert client.get("/api/v1/sources/src-aaaaaaaaaaaa", headers=hb).status_code == 404
    ev = client.get("/api/v1/events/recent", headers=h).json()
    assert any(e["type"] == "health_transition" for e in ev)
    assert client.get("/api/v1/events/recent", headers=hb).json() == [] or all(
        "src-aaaaaaaaaaaa" not in e["text"] for e in client.get("/api/v1/events/recent", headers=hb).json())
    assert client.get("/api/v1/search?q=src-aaaa", headers=h).json()["sources"]
    assert client.get("/api/v1/search?q=src-aaaa", headers=hb).json()["sources"] == []


def test_fleet_heartbeat_timeline_and_degraded_state(client):
    tok = register(client)
    _hb(tok, client)
    h = login(client)
    f = client.get("/api/v1/fleet", headers=h).json()
    assert f["summary"]["total"] == 1 and f["collectors"][0]["fleet_status"] == "online"
    assert f["collectors"][0]["region"] == "eu-west-1"
    assert f["collectors"][0]["local_processing"]["secrets_redacted_total"] == 7
    _hb(tok, client, queue_retrying=4)
    f = client.get("/api/v1/fleet", headers=h).json()
    assert f["collectors"][0]["fleet_status"] == "degraded" and "retrying" in f["collectors"][0]["health_reasons"][0]
    tl = client.get("/api/v1/collectors/col-alpha-01/heartbeats", headers=h).json()
    assert len(tl["heartbeats"]) == 2 and tl["heartbeats"][0]["transport_ms"] is not None
    assert client.get("/api/v1/collectors/col-alpha-01/heartbeats", headers=login(client, "beta.admin")).status_code == 404
    assert client.get("/api/v1/collectors/col-alpha-01/events", headers=h).json()["audit"]


def test_privacy_summary_counts_only(client):
    tok = register(client)
    p = payload()
    p["processing"] = {"raw_records": 9, "fields_dropped": 12, "secrets_redacted": 3, "identifiers_pseudonymised": 4,
                       "rules_triggered": ["R-PASSWORD", "R-USER"]}
    bad = payload()
    bad["processing"] = {"raw_records": 1, "fields_dropped": 0, "secrets_redacted": 0, "identifiers_pseudonymised": 0,
                         "rules_triggered": ["password=SYNTH"]}       # not an enumerated rule id
    r = send(client, tok, [p, payload(), bad]).json()
    assert r["accepted"] == 2 and r["rejected"] == 1
    h = login(client)
    s = client.get("/api/v1/privacy/summary", headers=h).json()
    assert s["processing"]["secrets_redacted"] == 3 and s["rules_triggered"] == {"R-PASSWORD": 1, "R-USER": 1}
    assert s["processing"]["events_with_secrets_removed"] == 1
    red = client.get("/api/v1/privacy/received?redacted_only=true", headers=h).json()
    assert [e["event_id"] for e in red] == [p["event_id"]]
    assert client.get("/api/v1/privacy/summary", headers=login(client, "beta.admin")).json()["received_total"] == 0


def test_platform_health_security_network(client):
    tok = register(client)
    send(client, tok, [payload(latency_ms=300.0, auth_failures=9)])
    h = login(client)
    client.get("/api/v1/overview", headers=h)
    p = client.get("/api/v1/platform/health", headers=h).json()
    assert p["availability"]["status"] == "up" and p["api"]["requests"] > 0 and p["database"]["ping_ms"] >= 0
    assert p["database"]["row_counts"] is None                                  # cross-tenant sizes: operator only
    assert client.get("/api/v1/platform/health", headers=login(client, "platform.operator")).json()["database"]["row_counts"]
    sec = client.get("/api/v1/security/overview", headers=h).json()
    assert sec["failed_logins_total"] == 9 and sec["by_source"][0]["failed_logins"] == 9
    net = client.get("/api/v1/network/overview", headers=h).json()
    assert net["sources"][0]["connectivity"] == "degraded" and "latency" in net["sources"][0]["degradation"][0]
