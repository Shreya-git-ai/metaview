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
from tools.stats import compute_stats, write_stats


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
