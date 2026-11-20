"""Collector-side privacy: redaction, allow-listing, schema validation, raw-data exclusion, egress audit."""
import json

from local_collector.policy_engine import PolicyEngine
from local_collector.processor import filter_metric_record, source_id_for
from local_collector.redaction import Redactor, scan_for_secrets
from shared.contract import canonical_json, sha256_hex

from conftest import payload

R = Redactor("k-SYNTHETIC")


def test_secret_redaction_removes_every_secret_type():
    line = ("user=user042 password=SYNTH!pw123 api_key=sk_live_SYNTHabcdef123456 "
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SYNTHsig "
            "AKIASYNTH1234567890 postgres://svc:SYNTH-Url-xyz@db:5432/x token=SYNTHtok999")
    red, findings, secrets = R.redact(line)
    for s in ["SYNTH!pw123", "sk_live_SYNTHabcdef123456", "eyJhbGciOiJIUzI1NiJ9", "AKIASYNTH1234567890",
              "SYNTH-Url-xyz", "SYNTHtok999", "user042"]:
        assert s not in red, s
    ids = {f["rule_id"] for f in findings}
    assert {"R-PASSWORD", "R-APIKEY", "R-BEARER", "R-AWSKEY", "R-URLCRED", "R-KV-SECRET", "R-USER"} <= ids
    assert "SYNTH!pw123" in secrets
    assert scan_for_secrets(red) == []


def test_identifiers_are_pseudonymised_consistently():
    a, _, _ = R.redact("client=10.1.2.3 user=alice@customer.example")
    b, _, _ = R.redact("client=10.1.2.3")
    assert "10.1.2.3" not in a and "alice@customer.example" not in a
    assert a.split()[0] == b.split()[0]   # same IP -> same pseudonym


def test_filter_drops_non_allow_listed_raw_fields():
    kept, dropped = filter_metric_record({"kind": "metric", "ts": "x", "hostname": "h", "ip": "10.0.0.1", "cpu_pct": 1.0,
                                          "top_processes": [{"cmdline": "--db-password=SYNTH"}], "kernel": "k"})
    assert kept == {"cpu_pct": 1.0}
    assert set(dropped) == {"hostname", "ip", "top_processes", "kernel"}


def test_source_id_is_pseudonymous_and_stable():
    a = source_id_for("db-01.alpha.internal", "salt")
    assert a == source_id_for("db-01.alpha.internal", "salt") and "db-01" not in a
    assert a != source_id_for("db-01.alpha.internal", "other-salt")


def test_policy_allows_valid_payload():
    d = PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(payload())
    assert d.allowed and d.sha256 == sha256_hex(d.canonical)


def test_policy_rejects_unknown_fields_and_raw_logs():
    p = payload()
    p["raw_log"] = "password=SYNTH-leak"
    d = PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(p, {"SYNTH-leak"})
    assert not d.allowed
    assert d.checks == {"schema": "fail", "identity": "pass", "secret_scan": "fail", "canary": "fail"}


def test_policy_rejects_unknown_metric_and_bad_types():
    p = payload()
    p["metrics"]["hostname"] = "db-01"
    assert not PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(p).allowed
    p = payload(cpu_pct="95")              # string instead of number (strict)
    assert not PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(p).allowed
    p = payload(cpu_pct=150.0)             # out of bounds
    assert not PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(p).allowed


def test_policy_rejects_free_text_context_and_identity_mismatch():
    p = payload()
    p["context_code"] = "password=SYNTH"   # not an enumerated code
    assert not PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(p).allowed
    p = payload(tenant="tenant-beta")
    d = PolicyEngine("tenant-alpha", "col-alpha-01").evaluate(p)
    assert not d.allowed and d.checks["identity"] == "fail"


def test_pipeline_secret_injection_never_reaches_outbox(collector_factory):
    col = collector_factory("https://localhost:1")   # SaaS not needed for this test
    host = col.generator.hostnames[1]
    col.scenarios.trigger("secret_injection", host, 30)
    col.scenarios.trigger("failed_logins", host, 30)
    import time
    time.sleep(5.2)                                  # let the scenario ramp up
    for _ in range(4):
        col.tick()
    decisions = col.close_window()
    secrets = col.generator.injected_secrets
    assert secrets, "scenario should inject synthetic secrets"
    outbox = "".join(r["payload_json"] for r in col.db.query("SELECT payload_json FROM outbox"))
    assert not any(s in outbox for s in secrets)
    # raw store DOES hold them locally (proves the test is meaningful)
    raw = "".join(r["body"] for r in col.db.query("SELECT body FROM raw_records"))
    assert any(s in raw for s in secrets)
    # the simulated misconfigured plugin payload was blocked and audited
    assert any(not d.allowed for d in decisions)
    blocked = col.audit.recent(status="blocked")
    assert blocked and any("raw_log" in r for b in blocked for r in b["policy_reasons"])
    # egress audit = exact bytes queued
    for row in col.db.query("SELECT event_id, payload_json, sha256 FROM outbox"):
        a = col.audit.by_event(row["event_id"])
        assert a["sha256"] == row["sha256"] == sha256_hex(row["payload_json"])
        assert canonical_json(json.loads(row["payload_json"])) == row["payload_json"]
