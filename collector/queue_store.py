"""Transactional SQLite outbox for reliable local telemetry delivery."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import config


class QueueStore:
    """Persist payloads and collector offsets in a local SQLite database."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        collector_id: str | None = None,
        busy_timeout_ms: int = 10_000,
    ) -> None:
        """Initialize a queue store; each operation opens its own connection."""
        self.db_path = Path(db_path) if db_path is not None else Path(__file__).resolve().parent / "data" / "queue.db"
        self.collector_id = collector_id or config.COLLECTOR_ID
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open a thread-independent connection configured for concurrent use."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        return connection

    def _initialize(self) -> None:
        """Create the outbox and persisted counters when needed."""
        with self._initialize_lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS outbox (
                        payload_id TEXT PRIMARY KEY,
                        event_time_utc TEXT NOT NULL,
                        body TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('pending', 'acked', 'rejected')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        sent_at REAL,
                        acked_at REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_outbox_pending_order
                    ON outbox(status, event_time_utc, payload_id);
                    CREATE TABLE IF NOT EXISTS counters (
                        key TEXT PRIMARY KEY,
                        value INTEGER NOT NULL
                    );
                    INSERT OR IGNORE INTO counters(key, value) VALUES ('payload_sequence', 0);
                    INSERT OR IGNORE INTO counters(key, value) VALUES ('stream_offset', 0);
                    """
                )
            finally:
                connection.close()

    def _next_payload_id(self, connection: sqlite3.Connection) -> str:
        """Increment and return the sequence-backed payload identifier."""
        connection.execute("UPDATE counters SET value = value + 1 WHERE key = 'payload_sequence'")
        sequence = connection.execute("SELECT value FROM counters WHERE key = 'payload_sequence'").fetchone()[0]
        return f"{self.collector_id}-{sequence:06d}"

    def reserve_payload_id(self) -> str:
        """Atomically reserve and persist the next payload ID."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            payload_id = self._next_payload_id(connection)
            connection.commit()
            return payload_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _insert_payload(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
        payload_id: str,
        created_at: float,
    ) -> bool:
        """Insert one pending row unless that payload id already exists."""
        existing = connection.execute("SELECT 1 FROM outbox WHERE payload_id = ?", (payload_id,)).fetchone()
        if existing is not None:
            return False
        body_object = dict(payload)
        body_object["payload_id"] = payload_id
        body = json.dumps(body_object, sort_keys=True, separators=(",", ":"))
        event_time = body_object.get("event_time_utc")
        if not isinstance(event_time, str) or not event_time:
            raise ValueError("payload must include a non-empty event_time_utc string")
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO outbox
                (payload_id, event_time_utc, body, status, attempts, created_at)
            VALUES (?, ?, ?, 'pending', 0, ?)
            """,
            (payload_id, event_time, body, created_at),
        )
        return cursor.rowcount == 1

    def enqueue(self, payload: dict[str, Any]) -> str:
        """Insert a payload using its ID or assign the next persisted ID."""
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            supplied_id = payload.get("payload_id")
            payload_id = supplied_id if isinstance(supplied_id, str) and supplied_id else self._next_payload_id(connection)
            self._insert_payload(connection, payload, payload_id, time.time())
            connection.commit()
            return payload_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def enqueue_with_offset(self, payload: dict[str, Any], new_offset: int) -> str:
        """Atomically assign an ID, insert a payload, and persist stream offset."""
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        if isinstance(new_offset, bool) or not isinstance(new_offset, int) or new_offset < 0:
            raise ValueError("new_offset must be a non-negative integer")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            payload_id = self._next_payload_id(connection)
            self._insert_payload(connection, payload, payload_id, time.time())
            connection.execute("UPDATE counters SET value = ? WHERE key = 'stream_offset'", (new_offset,))
            connection.commit()
            return payload_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def enqueue_many_with_offset(self, payloads: list[dict[str, Any]], new_offset: int) -> int:
        """Atomically insert already-IDed payloads and update the stream offset."""
        if not isinstance(payloads, list) or any(not isinstance(payload, dict) for payload in payloads):
            raise TypeError("payloads must be a list of dictionaries")
        if isinstance(new_offset, bool) or not isinstance(new_offset, int) or new_offset < 0:
            raise ValueError("new_offset must be a non-negative integer")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted = 0
            for payload in payloads:
                payload_id = payload.get("payload_id")
                if not isinstance(payload_id, str) or not payload_id:
                    raise ValueError("every payload must contain a non-empty payload_id")
                if self._insert_payload(connection, payload, payload_id, time.time()):
                    inserted += 1
            connection.execute("UPDATE counters SET value = ? WHERE key = 'stream_offset'", (new_offset,))
            connection.commit()
            return inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_offset(self) -> int:
        """Return the persisted stream byte offset."""
        connection = self._connect()
        try:
            row = connection.execute("SELECT value FROM counters WHERE key = 'stream_offset'").fetchone()
            return int(row[0])
        finally:
            connection.close()

    def set_offset(self, offset: int) -> None:
        """Persist the stream byte offset independently of enqueueing."""
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE counters SET value = ? WHERE key = 'stream_offset'", (offset,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def next_batch(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return pending rows ordered by event time, then stable payload ID."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT payload_id, event_time_utc, body, status, attempts, created_at, sent_at, acked_at
                FROM outbox WHERE status = 'pending'
                ORDER BY event_time_utc ASC, payload_id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) | {"body": json.loads(row["body"])} for row in rows]
        finally:
            connection.close()

    def mark_acked(self, payload_id: str, acked_at: float | None = None) -> None:
        """Mark a row acknowledged and record its acknowledgement time."""
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE outbox SET status = 'acked', acked_at = ? WHERE payload_id = ?",
                (time.time() if acked_at is None else acked_at, payload_id),
            )
        finally:
            connection.close()

    def mark_attempt(self, payload_id: str, sent_at: float | None = None) -> None:
        """Increment attempts and set the most recent send time."""
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE outbox SET attempts = attempts + 1, sent_at = ? WHERE payload_id = ?",
                (time.time() if sent_at is None else sent_at, payload_id),
            )
        finally:
            connection.close()

    def mark_rejected(self, payload_id: str) -> None:
        """Mark a permanently rejected row without deleting it."""
        connection = self._connect()
        try:
            connection.execute("UPDATE outbox SET status = 'rejected' WHERE payload_id = ?", (payload_id,))
        finally:
            connection.close()

    def pending_count(self) -> int:
        """Return the number of rows waiting to be sent."""
        connection = self._connect()
        try:
            return int(connection.execute("SELECT COUNT(*) FROM outbox WHERE status = 'pending'").fetchone()[0])
        finally:
            connection.close()

    def stats(self) -> dict[str, int]:
        """Return row counts grouped into pending, acked, rejected, and total."""
        connection = self._connect()
        try:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM outbox GROUP BY status").fetchall()
            counts = {row["status"]: int(row["count"]) for row in rows}
            result = {status: counts.get(status, 0) for status in ("pending", "acked", "rejected")}
            result["total"] = sum(result.values())
            return result
        finally:
            connection.close()


_DEFAULT_STORE = QueueStore()


def enqueue(payload: dict[str, Any]) -> str:
    """Insert a payload into the default queue store."""
    return _DEFAULT_STORE.enqueue(payload)


def enqueue_with_offset(payload: dict[str, Any], new_offset: int) -> str:
    """Atomically insert a payload and byte offset into the default store."""
    return _DEFAULT_STORE.enqueue_with_offset(payload, new_offset)


def reserve_payload_id() -> str:
    """Reserve an ID from the default queue store."""
    return _DEFAULT_STORE.reserve_payload_id()


def enqueue_many_with_offset(payloads: list[dict[str, Any]], new_offset: int) -> int:
    """Atomically enqueue a batch and stream offset in the default store."""
    return _DEFAULT_STORE.enqueue_many_with_offset(payloads, new_offset)


def get_offset() -> int:
    """Read the default store's persisted stream offset."""
    return _DEFAULT_STORE.get_offset()


def set_offset(offset: int) -> None:
    """Set the default store's persisted stream offset."""
    _DEFAULT_STORE.set_offset(offset)


def next_batch(limit: int = 100) -> list[dict[str, Any]]:
    """Read pending rows from the default queue store."""
    return _DEFAULT_STORE.next_batch(limit)


def mark_acked(payload_id: str, acked_at: float | None = None) -> None:
    """Mark a default-store queue row acknowledged."""
    _DEFAULT_STORE.mark_acked(payload_id, acked_at)


def mark_attempt(payload_id: str, sent_at: float | None = None) -> None:
    """Record a send attempt in the default store."""
    _DEFAULT_STORE.mark_attempt(payload_id, sent_at)


def mark_rejected(payload_id: str) -> None:
    """Mark a default-store queue row rejected."""
    _DEFAULT_STORE.mark_rejected(payload_id)


def pending_count() -> int:
    """Count pending rows in the default store."""
    return _DEFAULT_STORE.pending_count()


def stats() -> dict[str, int]:
    """Return status counts for the default store."""
    return _DEFAULT_STORE.stats()
