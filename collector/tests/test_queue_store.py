"""Tests for durable, concurrent SQLite queue storage."""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from queue_store import QueueStore


def _payload(event_time: str = "2026-11-20T10:00:00Z", **extra: object) -> dict[str, object]:
    return {"event_time_utc": event_time, "metrics": {"cpu_pct": 35.0}, **extra}


def test_ids_are_unique_and_continue_after_restart() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "queue.db"
        first = QueueStore(path, collector_id="test-col")
        id_one = first.enqueue(_payload())
        id_two = first.enqueue(_payload())
        reopened = QueueStore(path, collector_id="test-col")
        id_three = reopened.enqueue(_payload())
        assert [id_one, id_two, id_three] == ["test-col-000001", "test-col-000002", "test-col-000003"]


def test_offset_survives_restart_and_can_be_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "queue.db"
        store = QueueStore(path)
        store.set_offset(2468)
        assert QueueStore(path).get_offset() == 2468


def test_failed_enqueue_transaction_saves_neither_row_nor_offset() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = QueueStore(Path(directory) / "queue.db")
        try:
            store.enqueue_with_offset(_payload(unserializable=object()), 777)
        except Exception:
            pass
        else:
            raise AssertionError("non-serializable payload should fail")
        assert store.get_offset() == 0
        assert store.stats()["total"] == 0
        assert store.enqueue(_payload()).endswith("000001")


def test_duplicate_payload_id_is_ignored_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = QueueStore(Path(directory) / "queue.db")
        store.enqueue(_payload(payload_id="stable-id", marker="first"))
        store.enqueue(_payload(event_time="2026-11-21T10:00:00Z", payload_id="stable-id", marker="second"))
        rows = store.next_batch()
        assert len(rows) == 1
        assert rows[0]["body"]["marker"] == "first"
        assert rows[0]["event_time_utc"] == "2026-11-20T10:00:00Z"


def test_next_batch_orders_by_event_time_not_insertion_order() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = QueueStore(Path(directory) / "queue.db")
        store.enqueue(_payload("2026-11-20T10:03:00Z", payload_id="later"))
        store.enqueue(_payload("2026-11-20T10:01:00Z", payload_id="earlier"))
        store.enqueue(_payload("2026-11-20T10:02:00Z", payload_id="middle"))
        assert [row["payload_id"] for row in store.next_batch()] == ["earlier", "middle", "later"]


def test_acked_and_rejected_rows_are_excluded_from_next_batch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = QueueStore(Path(directory) / "queue.db")
        acked_id = store.enqueue(_payload(payload_id="acked"))
        rejected_id = store.enqueue(_payload(payload_id="rejected"))
        store.enqueue(_payload(payload_id="pending"))
        store.mark_acked(acked_id)
        store.mark_rejected(rejected_id)
        assert [row["payload_id"] for row in store.next_batch()] == ["pending"]


def test_two_threads_enqueue_without_errors_or_lost_rows() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "queue.db"
        store = QueueStore(path, collector_id="parallel", busy_timeout_ms=30_000)
        errors: list[BaseException] = []
        ids: list[str] = []
        result_lock = threading.Lock()

        def worker(prefix: str) -> None:
            try:
                local_ids = [store.enqueue(_payload(marker=f"{prefix}-{number}")) for number in range(40)]
                with result_lock:
                    ids.extend(local_ids)
            except BaseException as error:
                with result_lock:
                    errors.append(error)

        threads = [threading.Thread(target=worker, args=(f"thread-{number}",)) for number in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(ids) == 80
        assert len(set(ids)) == 80
        assert store.stats()["total"] == 80


def test_stats_count_pending_acked_rejected_and_total() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = QueueStore(Path(directory) / "queue.db")
        acked = store.enqueue(_payload(payload_id="a"))
        store.enqueue(_payload(payload_id="p1"))
        store.enqueue(_payload(payload_id="p2"))
        rejected = store.enqueue(_payload(payload_id="r"))
        store.mark_acked(acked)
        store.mark_rejected(rejected)
        assert store.pending_count() == 2
        assert store.stats() == {"pending": 2, "acked": 1, "rejected": 1, "total": 4}
