"""Tests for mock customer environment probes and collector integration."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sender as sender_module
from collector import TelemetryCollector
from probes import append_records, inject_scenario, make_mock_records
from queue_store import QueueStore
from sender import Sender
from tools.dummy_ingest import create_app


class _Response:
    """Requests-shaped response that wraps a Flask test-client result."""

    def __init__(self, response: Any) -> None:
        self.status_code = response.status_code
        self._response = response

    def json(self) -> Any:
        return self._response.get_json()


def test_mock_probe_records_match_generator_stream_shape() -> None:
    records = make_mock_records()
    assert [record["host"] for record in records] == ["banking-app", "payment-service", "postgres"]
    for record in records:
        assert record["ts"].endswith("Z")
        assert set(record).issubset({"ts", "host", "metrics", "log_line"})
        assert all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in record["metrics"].values())


def test_mock_db_down_probe_produces_acked_db_event(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stream = root / "stream.jsonl"
        db = root / "queue.db"
        egress = root / "egress.jsonl"
        audit = root / "redaction_audit.jsonl"
        records = inject_scenario("db_down", stream)
        assert len(records) == 2

        store = QueueStore(db, collector_id="probe-test")
        collector = TelemetryCollector(stream, store, egress_path=egress, audit_path=audit)
        assert collector.poll_once() == 2
        pending = store.next_batch(100)
        assert any(row["body"].get("event", {}).get("type") == "db_unavailable" for row in pending)

        app = create_app("dev-key-change-me", root / "received.jsonl")
        client = app.test_client()

        def post(url: str, **kwargs: Any) -> _Response:
            path = "/heartbeat" if url.endswith("/heartbeat") else "/ingest"
            return _Response(client.post(path, json=kwargs.get("json"), headers=kwargs.get("headers", {})))

        monkeypatch.setattr(sender_module.requests, "post", post)
        sender = Sender(store, threading.Event(), offline_flag=root / "offline", egress_path=egress)
        while store.pending_count():
            outcome, _ = sender.send_one(store.next_batch(1)[0])
            assert outcome == "acked"

        accepted_event = next(
            payload
            for payload in app.extensions["received_payloads"]
            if payload.get("event", {}).get("type") == "db_unavailable"
        )
        assert accepted_event["affected_services"] == ["postgres", "banking-app", "payment-service"]
        assert store.stats()["acked"] == store.stats()["total"] == len(records) + 1


def test_probe_scenario_stream_records_contain_only_numeric_metrics() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "stream.jsonl"
        for scenario in ("db_down", "db_up", "app_down", "conn_flood", "error_spike"):
            inject_scenario(scenario, path)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 7
        assert all(isinstance(record.get("metrics"), dict) for record in records)
        assert all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for record in records
            for value in record["metrics"].values()
        )
