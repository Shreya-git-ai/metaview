"""Local SQLite store (inside the customer environment).

Tables:
  raw_records    – raw telemetry & raw log lines (may contain synthetic secrets). LOCAL ONLY, pruned.
  redaction_log  – which rules fired on which raw record + the redacted text (never secret values).
  egress_audit   – every outgoing payload decision: exact bytes, SHA-256, policy result, status.
  outbox         – approved metadata waiting for / after delivery (the reliable local queue).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS raw_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, window_id TEXT, host TEXT NOT NULL,
  kind TEXT NOT NULL, stream TEXT, body TEXT NOT NULL, has_secret INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_raw_secret ON raw_records(has_secret, id);
CREATE TABLE IF NOT EXISTS redaction_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, raw_id INTEGER NOT NULL, ts TEXT NOT NULL, window_id TEXT,
  host TEXT NOT NULL, stream TEXT, findings TEXT NOT NULL, secret_count INTEGER NOT NULL,
  redacted_text TEXT, dropped_fields TEXT);
CREATE TABLE IF NOT EXISTS egress_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, kind TEXT NOT NULL, window_id TEXT,
  tenant_id TEXT, collector_id TEXT, source_id TEXT, category TEXT, destination TEXT NOT NULL,
  created_at TEXT NOT NULL, payload_json TEXT NOT NULL, sha256 TEXT NOT NULL,
  policy_allowed INTEGER NOT NULL, policy_reasons TEXT, policy_checks TEXT, status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, transmitted_at TEXT, http_status INTEGER,
  saas_result TEXT, last_error TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_event ON egress_audit(event_id);
CREATE INDEX IF NOT EXISTS ix_audit_window ON egress_audit(window_id, source_id);
CREATE TABLE IF NOT EXISTS outbox (
  event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, sha256 TEXT NOT NULL, event_time TEXT NOT NULL,
  created_at REAL NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL, last_error TEXT, delivered_at TEXT, saas_result TEXT);
CREATE INDEX IF NOT EXISTS ix_outbox_due ON outbox(status, next_attempt_at);
"""

RAW_KEEP = 4000
REDACTION_KEEP = 4000
HEARTBEAT_AUDIT_KEEP = 200


class LocalDB:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        with self.lock:
            self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq) -> None:
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, seq)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------- raw + redaction
    def add_raw(self, rec: dict, window_id: str, has_secret: bool) -> int:
        cur = self.execute(
            "INSERT INTO raw_records(ts, window_id, host, kind, stream, body, has_secret) VALUES (?,?,?,?,?,?,?)",
            (rec["ts"], window_id, rec["hostname"], rec["kind"], rec.get("stream"), json.dumps(rec), int(has_secret)))
        return int(cur.lastrowid)

    def add_redaction(self, raw_id: int, rec: dict, window_id: str, details: dict) -> None:
        self.execute(
            "INSERT INTO redaction_log(raw_id, ts, window_id, host, stream, findings, secret_count, redacted_text, "
            "dropped_fields) VALUES (?,?,?,?,?,?,?,?,?)",
            (raw_id, rec["ts"], window_id, rec["hostname"], rec.get("stream"), json.dumps(details["findings"]),
             details.get("secret_count", 0), details.get("redacted"), json.dumps(details.get("dropped", []))))

    def prune(self) -> None:
        with self.lock:
            # keep secret-bearing records a little longer so the proof panel can show them
            self.conn.execute("DELETE FROM raw_records WHERE has_secret=0 AND id < "
                              "(SELECT COALESCE(MAX(id),0) - ? FROM raw_records)", (RAW_KEEP,))
            self.conn.execute("DELETE FROM raw_records WHERE has_secret=1 AND id < "
                              "(SELECT COALESCE(MAX(id),0) - ? FROM raw_records)", (RAW_KEEP * 10,))
            self.conn.execute("DELETE FROM redaction_log WHERE secret_count=0 AND id < "
                              "(SELECT COALESCE(MAX(id),0) - ? FROM redaction_log)", (REDACTION_KEEP,))
            self.conn.execute("DELETE FROM egress_audit WHERE kind='heartbeat' AND id NOT IN "
                              "(SELECT id FROM egress_audit WHERE kind='heartbeat' ORDER BY id DESC LIMIT ?)",
                              (HEARTBEAT_AUDIT_KEEP,))
