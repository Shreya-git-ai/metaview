"""Tests for delivery percentiles and outage loss detection in stats.py."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from egress_log import append_entry
from queue_store import QueueStore
from tools.stats import compute_stats, payload_details, print_payload_details, write_stats


def test_stats_percentiles_and_missing_sent_id_detection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QueueStore(root / "queue.db")
        event_epoch = datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()
        payload_ids = ("stats-1", "stats-2", "stats-3")
        for index, payload_id in enumerate(payload_ids):
            event_time = f"2026-11-20T00:00:0{index}Z"
            payload = {"payload_id": payload_id, "event_time_utc": event_time, "category": "infra"}
            store.enqueue(payload)
            store.mark_acked(payload_id, acked_at=event_epoch + index + (1, 3, 5)[index])
            append_entry("sent", payload, path=root / "egress.jsonl")
        received_path = root / "received.jsonl"
        received_path.write_text(
            json.dumps({"payload_id": "stats-1"}) + "\n"
            + json.dumps({"payload_id": "stats-2"}) + "\n"
            + json.dumps({"payload_id": "stats-2"}) + "\n",
            encoding="utf-8",
        )
        (root / "offline_windows.jsonl").write_text(
            json.dumps({"event": "force_offline_started", "ts_utc": "2026-11-20T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )

        stats = compute_stats(root)
        assert stats["delivery"]["p50_seconds"] == 3.0
        assert stats["delivery"]["p95_seconds"] == 4.8
        assert stats["outage"]["missing_ids"] == ["stats-3"]
        assert stats["outage"]["missing_count"] == 1
        assert stats["outage"]["duplicate_count"] == 1
        assert stats["outage"]["pass"] is False
        output = write_stats(root, stats)
        assert output.exists()
        assert json.loads(output.read_text(encoding="utf-8"))["delivery"]["p95_seconds"] == 4.8


def test_missing_receiver_log_reports_na_with_reconciliation_details(capsys) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QueueStore(root / "queue.db")
        payload = {"payload_id": "unverified-1", "event_time_utc": "2026-11-20T00:00:00Z"}
        store.enqueue(payload)
        append_entry("sent", payload, path=root / "egress.jsonl")
        stats = compute_stats(root)
        assert stats["outage"]["status"] == "N/A (no outage)"
        assert stats["outage"]["pass"] is None
        assert stats["outage"]["missing_ids"] == ["unverified-1"]
        from tools.stats import print_stats
        print_stats(stats)
        output = capsys.readouterr().out
        assert "Outage check details:" in output
        assert "IDs sent but not received (1): ['unverified-1']" in output
        assert "no FORCE_OFFLINE_FLAG window was recorded" in output


def test_payload_details_are_slowest_first_and_mark_retried() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QueueStore(root / "queue.db")
        event_epoch = datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()
        for payload_id, seconds in (("fast", 1), ("slow", 8), ("middle", 4)):
            event_time = "2026-11-20T00:00:00Z"
            payload = {"payload_id": payload_id, "event_time_utc": event_time}
            store.enqueue(payload)
            store.mark_acked(payload_id, acked_at=event_epoch + seconds)
        append_entry("retried", {"payload_id": "middle"}, path=root / "egress.jsonl")
        details = payload_details(root)
        assert [row["payload_id"] for row in details] == ["slow", "middle", "fast"]
        assert [row["retried"] for row in details] == [False, True, False]


def test_delivery_percentiles_split_metrics_and_incident_events_and_verbose_output(capsys) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QueueStore(root / "queue.db")
        event_epoch = datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()
        rows = (
            ("m-fast", None, 1),
            ("m-slow", None, 3),
            ("e-fast", {"type": "cpu_anomaly"}, 5),
            ("e-slow", {"type": "db_unavailable"}, 7),
        )
        for payload_id, event, latency in rows:
            payload = {"payload_id": payload_id, "event_time_utc": "2026-11-20T00:00:00Z"}
            if event is not None:
                payload["event"] = event
            store.enqueue(payload)
            store.mark_acked(payload_id, acked_at=event_epoch + latency)
        append_entry("retried", {"payload_id": "e-fast"}, path=root / "egress.jsonl")

        stats = compute_stats(root)
        timing = stats["delivery"]["by_payload_type"]
        assert timing["metrics"]["p50_seconds"] == 2.0
        assert timing["metrics"]["p95_seconds"] == 2.9
        assert timing["incident_events"]["p50_seconds"] == 6.0
        assert timing["incident_events"]["p95_seconds"] == 6.9
        print_payload_details(root)
        output = capsys.readouterr().out
        assert output.index("e-slow") < output.index("e-fast") < output.index("m-slow") < output.index("m-fast")
        assert "e-fast | cpu_anomaly |" in output and "| yes" in output
        assert "m-fast | metrics |" in output and "| no" in output


def test_outage_mismatch_is_na_when_no_offline_window_was_recorded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QueueStore(root / "queue.db")
        payload = {"payload_id": "normal-1", "event_time_utc": "2026-11-20T00:00:00Z"}
        store.enqueue(payload)
        append_entry("sent", payload, path=root / "egress.jsonl")
        (root / "received.jsonl").write_text("", encoding="utf-8")
        outage = compute_stats(root)["outage"]
        assert outage["status"] == "N/A (no outage)"
        assert outage["missing_ids"] == ["normal-1"]
