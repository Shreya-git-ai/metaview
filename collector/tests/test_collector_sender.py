"""Integration tests for byte-offset collection and background delivery."""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import requests
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collector as collector_module
import sender as sender_module
from collector import TelemetryCollector, read_events
from generator import HOSTS, append_record, inject, make_record
from queue_store import QueueStore
from sender import Sender
from tools.dummy_ingest import create_app


class _Response:
    """Small requests-compatible response wrapper for Flask test client calls."""

    def __init__(self, response: Any) -> None:
        self.status_code = response.status_code
        self._response = response

    def json(self) -> Any:
        return self._response.get_json()


def _mock_server(app: Any):
    """Return a requests.post substitute backed by an isolated Flask client."""
    client = app.test_client()

    def post(url: str, **kwargs: Any) -> _Response:
        path = "/heartbeat" if url.endswith("/heartbeat") else "/ingest"
        response = client.post(path, json=kwargs.get("json"), headers=kwargs.get("headers", {}))
        return _Response(response)

    return post


def _patch_session(monkeypatch: pytest.MonkeyPatch, post: Any) -> None:
    """Give Sender a requests.Session-shaped object backed by the test handler."""
    class _Session:
        def post(self, url: str, **kwargs: Any) -> Any:
            return post(url, **kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr(sender_module.requests, "Session", _Session)


def _paths(directory: str) -> tuple[Path, Path, Path, Path]:
    root = Path(directory)
    return root / "stream.jsonl", root / "queue.db", root / "egress.jsonl", root / "redaction_audit.jsonl"


def _record(index: int, ts_base: datetime | None = None) -> dict[str, Any]:
    base = ts_base or datetime(2026, 11, 20, tzinfo=timezone.utc)
    ts = (base + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
    import random

    return make_record(HOSTS[0], random.Random(index), ts=ts, cpu_pct=35.0, mem_pct=50.0, latency_ms=25.0, packet_loss_pct=0.0)


def _drain(sender: Sender, store: QueueStore) -> None:
    """Synchronously send each currently pending item once, retrying once if needed."""
    while store.pending_count():
        row = store.next_batch(1)[0]
        outcome, _ = sender.send_one(row)
        if outcome == "retry":
            row = store.next_batch(1)[0]
            outcome, _ = sender.send_one(row)
            assert outcome != "retry"


def test_read_events_respects_offsets_malformed_lines_and_partial_tail() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "stream.jsonl"
        first = b'{"ts":"2026-11-20T00:00:00Z","host":"one"}\n'
        malformed = b"{bad json}\n"
        partial = b'{"ts":"2026-11-20T00:00:01Z","host":"two"}'
        path.write_bytes(first + malformed + partial)
        skipped: list[int] = []
        records = list(read_events(path, 0, skipped.append))
        assert len(records) == 1
        assert records[0][1] == len(first)
        assert skipped == [len(first) + len(malformed)]
        with path.open("ab") as stream:
            stream.write(b"\n")
        completed = list(read_events(path, skipped[-1]))
        assert completed == [({"ts": "2026-11-20T00:00:01Z", "host": "two"}, len(first + malformed + partial + b"\n"))]


def test_end_to_end_normal_records_and_secret_are_acked_without_raw_leaks(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        stream, db, egress, audit = _paths(directory)
        for index in range(40):
            append_record(_record(index), str(stream))
        inject("secret", __import__("random").Random(10), str(stream))
        store = QueueStore(db, collector_id="e2e")
        collector = TelemetryCollector(stream, store, egress_path=egress, audit_path=audit)
        assert collector.poll_once() == 41
        assert store.stats()["pending"] >= 42
        app = create_app("dev-key-change-me", Path(directory) / "received.jsonl")
        _patch_session(monkeypatch, _mock_server(app))
        sender = Sender(store, threading.Event(), offline_flag=Path(directory) / "offline", egress_path=egress)
        _drain(sender, store)
        assert store.stats()["pending"] == 0
        assert store.stats()["acked"] == store.stats()["total"]
        bodies = [row["body"] for row in store.next_batch(100)]
        assert bodies == []
        connection = __import__("sqlite3").connect(db)
        try:
            queued_json = "\n".join(row[0] for row in connection.execute("SELECT body FROM outbox"))
        finally:
            connection.close()
        egress_text = egress.read_text(encoding="utf-8")
        for secret in ("Abc@123", "sk-live-9f2c81", "eyJhbGciOi.abc.def", "rahul"):
            assert secret not in queued_json
            assert secret not in egress_text
        assert any(payload.get("event", {}).get("type") == "secret_redacted" for payload in app.extensions["received_payloads"])
        assert "Abc@123" in audit.read_text(encoding="utf-8")


def test_restart_resumes_offset_without_duplicate_or_lost_records() -> None:
    with tempfile.TemporaryDirectory() as directory:
        stream, db, egress, audit = _paths(directory)
        for index in range(20):
            append_record(_record(index), str(stream))
        store = QueueStore(db, collector_id="restart")
        first = TelemetryCollector(stream, store, egress_path=egress, audit_path=audit)
        assert first.poll_once() == 20
        first_ids = {row["payload_id"] for row in store.next_batch(100)}
        for index in range(20, 40):
            append_record(_record(index), str(stream))
        restarted_store = QueueStore(db, collector_id="restart")
        restarted = TelemetryCollector(stream, restarted_store, egress_path=egress, audit_path=audit)
        assert restarted.poll_once() == 20
        all_ids = [row["payload_id"] for row in restarted_store.next_batch(100)]
        assert len(all_ids) == 40
        assert len(set(all_ids)) == 40
        assert first_ids.issubset(set(all_ids))
        assert restarted_store.get_offset() == stream.stat().st_size


def test_offline_queue_drains_in_event_order_and_heartbeat_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        stream, db, egress, audit = _paths(directory)
        offline = Path(directory) / "FORCE_OFFLINE_FLAG"
        offline.touch()
        for index in range(20):
            append_record(_record(index), str(stream))
        store = QueueStore(db, collector_id="offline")
        collector = TelemetryCollector(stream, store, egress_path=egress, audit_path=audit)
        collector.poll_once()
        app = create_app("dev-key-change-me", Path(directory) / "received.jsonl")
        requests_seen: list[str] = []

        def tracked_post(url: str, **kwargs: Any) -> _Response:
            requests_seen.append(url)
            return _mock_server(app)(url, **kwargs)

        _patch_session(monkeypatch, tracked_post)
        stop_event = threading.Event()
        sender = Sender(store, stop_event, offline_flag=offline, egress_path=egress, heartbeat_s=0.01)
        thread = threading.Thread(target=sender.run)
        thread.start()
        time.sleep(0.15)
        assert requests_seen == []
        assert store.pending_count() == 20
        assert (Path(directory) / "offline_windows.jsonl").exists()
        offline.unlink()
        deadline = time.time() + 5
        while store.pending_count() and time.time() < deadline:
            time.sleep(0.02)
        stop_event.set()
        thread.join(timeout=2)
        assert store.stats()["acked"] == 20
        ids = [payload["payload_id"] for payload in app.extensions["received_payloads"]]
        assert len(ids) == 20
        assert len(set(ids)) == 20
        times = [payload["event_time_utc"] for payload in app.extensions["received_payloads"]]
        assert times == sorted(times)
        assert app.extensions["duplicate_count"] == 0


def test_timeout_retry_reuses_id_and_duplicate_reply_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        _, db, egress, _ = _paths(directory)
        store = QueueStore(db, collector_id="retry")
        store.enqueue({"payload_id": "retry-000001", "event_time_utc": "2026-11-20T10:00:00Z", "category": "infra"})
        app = create_app("dev-key-change-me", Path(directory) / "received.jsonl")
        real_post = _mock_server(app)
        sent_ids: list[str] = []
        first = True

        def timeout_after_accept(url: str, **kwargs: Any) -> _Response:
            nonlocal first
            payload_id = kwargs["json"]["payload_id"]
            sent_ids.append(payload_id)
            response = real_post(url, **kwargs)
            if first:
                first = False
                raise requests.Timeout("simulated timeout after server accepted")
            return response

        _patch_session(monkeypatch, timeout_after_accept)
        sender = Sender(store, threading.Event(), offline_flag=Path(directory) / "offline", egress_path=egress)
        row = store.next_batch(1)[0]
        assert sender.send_one(row)[0] == "retry"
        assert sender.send_one(store.next_batch(1)[0])[0] == "acked"
        assert sent_ids == ["retry-000001", "retry-000001"]
        assert app.extensions["accepted_count"] == 1
        assert app.extensions["duplicate_count"] == 1


def test_final_safety_failure_is_blocked_and_never_enqueued(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        stream, db, egress, audit = _paths(directory)
        append_record(_record(0), str(stream))
        original_apply = collector_module.apply_allowlist

        def inject_secret(candidate: dict[str, Any]):
            clean, dropped = original_apply(candidate)
            clean["alert_context"] = "password=never-egress"
            return clean, dropped

        monkeypatch.setattr(collector_module, "apply_allowlist", inject_secret)
        store = QueueStore(db)
        collector = TelemetryCollector(stream, store, egress_path=egress, audit_path=audit)
        collector.poll_once()
        assert store.pending_count() == 0
        text = egress.read_text(encoding="utf-8")
        assert '"status":"blocked"' in text
        assert "never-egress" not in text
        assert store.get_offset() == stream.stat().st_size


def test_malformed_complete_line_advances_count_and_offset() -> None:
    with tempfile.TemporaryDirectory() as directory:
        stream, db, egress, audit = _paths(directory)
        stream.write_bytes(b"not json\n" + json.dumps(_record(0)).encode() + b"\n")
        collector = TelemetryCollector(stream, QueueStore(db), egress_path=egress, audit_path=audit)
        assert collector.poll_once() == 1
        assert collector.malformed_count == 1
        assert collector.store.get_offset() == stream.stat().st_size


def test_100_pending_payloads_drain_against_dummy_ingest_under_10_seconds() -> None:
    """A real local HTTP server verifies the sustained batch-drain path."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QueueStore(root / "queue.db", collector_id="speed")
        for index in range(100):
            store.enqueue({
                "payload_id": f"speed-{index:06d}",
                "event_time_utc": f"2026-11-20T10:{index // 60:02d}:{index % 60:02d}Z",
                "category": "infra",
            })
        app = create_app(received_path=root / "received.jsonl")
        server = make_server("127.0.0.1", 0, app, threaded=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        old_level = logging.getLogger("werkzeug").level
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        stop_event = threading.Event()
        sender = Sender(
            store,
            stop_event,
            ingest_url=f"http://127.0.0.1:{server.server_port}/ingest",
            offline_flag=root / "offline",
            egress_path=root / "egress.jsonl",
            heartbeat_s=3600,
        )
        worker = threading.Thread(target=sender.run)
        started = time.perf_counter()
        worker.start()
        try:
            deadline = started + 10
            while app.extensions["accepted_count"] < 100 and time.perf_counter() < deadline:
                time.sleep(0.05)
            while store.pending_count() and time.perf_counter() < deadline:
                time.sleep(0.05)
            elapsed = time.perf_counter() - started
            stop_event.set()
            worker.join(timeout=2)
            assert store.stats()["acked"] == 100
            assert app.extensions["accepted_count"] == 100
            assert elapsed < 10, f"100 payloads took {elapsed:.3f}s"
        finally:
            stop_event.set()
            worker.join(timeout=2)
            server.shutdown()
            server_thread.join(timeout=2)
            logging.getLogger("werkzeug").setLevel(old_level)
