"""Tests for the local egress proof panel's API endpoints."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from egress_log import append_entry, write_redaction_audit
from egress_panel import create_app
from queue_store import QueueStore


def test_counters_reflect_prepared_egress_and_queue() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = {"payload_id": "col-000001", "event_time_utc": "2026-11-20T10:20:15Z", "category": "infra"}
        store = QueueStore(root / "queue.db")
        store.enqueue({"payload_id": "pending-id", "event_time_utc": "2026-11-20T10:20:15Z"})
        for status in ("queued", "sent", "acked", "retried", "blocked", "rejected"):
            append_entry(status, payload, path=root / "egress.jsonl")
        received_path = root / "received.jsonl"
        received_path.write_text(json.dumps(payload) + "\n" + json.dumps(payload) + "\n", encoding="utf-8")
        app = create_app(root)
        result = app.test_client().get("/api/counters").get_json()
        assert result["queued"] == result["sent"] == result["acked"] == 1
        assert result["retried"] == result["blocked"] == result["rejected"] == 1
        assert result["duplicates"] == 1
        assert result["queue_depth"] == 1
        assert result["online_state"] == "online"


def test_proof_pairs_local_raw_line_with_sent_payload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = {"payload_id": "proof-1", "event_time_utc": "2026-11-20T10:20:15Z", "event": {"type": "secret_redacted"}}
        write_redaction_audit("proof-1", "password=local-secret", "password=[REDACTED]", path=root / "redaction_audit.jsonl")
        append_entry("sent", payload, path=root / "egress.jsonl")
        result = create_app(root).test_client().get("/api/proof").get_json()
        proof = result["proofs"][0]
        assert proof["raw_line_label"] == "LOCAL ONLY, NEVER SENT"
        assert proof["raw_line"] == "password=local-secret"
        assert proof["sent_payload"] == payload


def test_tampered_chain_is_reported_broken() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        append_entry("queued", {"payload_id": "tamper-1"}, path=root / "egress.jsonl")
        path = root / "egress.jsonl"
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        entries[0]["payload"]["payload_id"] = "tampered"
        path.write_text(json.dumps(entries[0]) + "\n", encoding="utf-8")
        assert create_app(root).test_client().get("/api/chain").get_json() == {"valid": False}


def test_raw_secret_is_not_returned_by_payload_endpoint() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = {"payload_id": "proof-2", "event_time_utc": "2026-11-20T10:20:15Z", "alert_context": "secret redacted"}
        append_entry("acked", payload, path=root / "egress.jsonl")
        write_redaction_audit("proof-2", "password=Abc@123", "password=[REDACTED]", path=root / "redaction_audit.jsonl")
        response = create_app(root).test_client().get("/api/payloads")
        assert b"Abc@123" not in response.data
        assert b"password=" not in response.data
