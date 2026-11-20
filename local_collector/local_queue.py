"""Reliable local outbox for APPROVED metadata only (never raw data).

Statuses: pending → delivered | retrying → delivered | failed (rejected by SaaS schema/auth; not retried).
Event ids are generated once at the collector, so re-sends are idempotent at the SaaS side.
"""
from __future__ import annotations

import random
import time

from shared.contract import iso, utc_now

from .local_db import LocalDB

MAX_BACKOFF_S = 16.0


def backoff_delay(attempts: int) -> float:
    """Exponential backoff with jitter: 1, 2, 4, 8, 16, 16 … seconds."""
    return min(MAX_BACKOFF_S, 2 ** max(0, attempts - 1)) + random.uniform(0, 0.3)


class LocalQueue:
    def __init__(self, db: LocalDB) -> None:
        self.db = db

    def enqueue(self, event_id: str, canonical: str, sha256: str, event_time: str) -> None:
        now = time.time()
        self.db.execute(
            "INSERT OR IGNORE INTO outbox(event_id, payload_json, sha256, event_time, created_at, status, attempts, "
            "next_attempt_at) VALUES (?,?,?,?,?, 'pending', 0, ?)",
            (event_id, canonical, sha256, event_time, now, now))

    def due(self, limit: int = 200) -> list[dict]:
        return self.db.query(
            "SELECT * FROM outbox WHERE status IN ('pending','retrying') AND next_attempt_at <= ? "
            "ORDER BY created_at LIMIT ?", (time.time(), limit))

    def mark_delivered(self, results: dict[str, str]) -> None:
        now = iso(utc_now())
        self.db.executemany("UPDATE outbox SET status='delivered', attempts=attempts+1, delivered_at=?, saas_result=?, "
                            "last_error=NULL WHERE event_id=?",
                            [(now, res, eid) for eid, res in results.items()])

    def mark_failed(self, results: dict[str, str]) -> None:
        self.db.executemany("UPDATE outbox SET status='failed', attempts=attempts+1, last_error=? WHERE event_id=?",
                            [(err, eid) for eid, err in results.items()])

    def mark_retry(self, rows: list[dict], error: str) -> None:
        now = time.time()
        self.db.executemany(
            "UPDATE outbox SET status='retrying', attempts=attempts+1, next_attempt_at=?, last_error=? WHERE event_id=?",
            [(now + backoff_delay(r["attempts"] + 1), error, r["event_id"]) for r in rows])

    def counts(self) -> dict:
        rows = self.db.query("SELECT status, COUNT(*) n FROM outbox GROUP BY status")
        c = {"pending": 0, "retrying": 0, "delivered": 0, "failed": 0}
        c.update({r["status"]: r["n"] for r in rows})
        return c

    def recent(self, limit: int = 30, status: str | None = None) -> list[dict]:
        if status:
            return self.db.query("SELECT event_id, sha256, event_time, status, attempts, last_error, delivered_at, "
                                 "saas_result FROM outbox WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        return self.db.query("SELECT event_id, sha256, event_time, status, attempts, last_error, delivered_at, "
                             "saas_result FROM outbox ORDER BY created_at DESC LIMIT ?", (limit,))

    def delivered_ids(self, limit: int = 5000) -> list[str]:
        return [r["event_id"] for r in self.db.query(
            "SELECT event_id FROM outbox WHERE status='delivered' ORDER BY created_at DESC LIMIT ?", (limit,))]

    def last_delivered_payloads(self, n: int) -> list[dict]:
        return self.db.query("SELECT event_id, payload_json, sha256 FROM outbox WHERE status='delivered' "
                             "ORDER BY created_at DESC LIMIT ?", (n,))
