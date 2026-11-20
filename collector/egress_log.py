"""Append-only, hash-chained egress and local redaction audit logs."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
EGRESS_PATH = DATA_DIR / "egress.jsonl"
REDACTION_AUDIT_PATH = DATA_DIR / "redaction_audit.jsonl"
_APPEND_LOCK = threading.Lock()
_ZERO_HASH = "0" * 64
_STATUSES = {"queued", "sent", "retried", "acked", "blocked", "rejected"}


def _canonical_json(value: Any) -> str:
    """Serialize a value deterministically for integrity hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _last_state(path: Path) -> tuple[int, str]:
    """Read the last valid entry's sequence and hash, or the initial state."""
    if not path.exists():
        return 0, _ZERO_HASH
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return 0, _ZERO_HASH
        entry = json.loads(lines[-1])
        return int(entry["seq"]), str(entry["entry_hash"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 0, _ZERO_HASH


def append_entry(
    status: str,
    payload: dict[str, Any],
    dropped_fields: list[dict[str, str]] | None = None,
    redactions: list[str] | None = None,
    *,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a payload status entry and extend the hash chain."""
    if status not in _STATUSES:
        raise ValueError(f"unsupported egress status: {status}")
    log_path = Path(path) if path is not None else EGRESS_PATH
    payload_copy = dict(payload)
    payload_sha256 = hashlib.sha256(_canonical_json(payload_copy).encode("utf-8")).hexdigest()
    with _APPEND_LOCK:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        seq, prev_hash = _last_state(log_path)
        entry: dict[str, Any] = {
            "seq": seq + 1,
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "status": status,
            "payload": payload_copy,
            "dropped_fields": dropped_fields or [],
            "redactions": redactions or [],
            "payload_sha256": payload_sha256,
            "prev_hash": prev_hash,
            "entry_hash": hashlib.sha256(f"{prev_hash}{payload_sha256}{status}".encode("utf-8")).hexdigest(),
        }
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        return entry


def log_status(
    status: str,
    payload: dict[str, Any],
    dropped_fields: list[dict[str, str]] | None = None,
    redactions: list[str] | None = None,
    *,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a status update for a payload as a new chain entry."""
    return append_entry(status, payload, dropped_fields, redactions, path=path)


def write_redaction_audit(
    payload_id: str,
    raw_line: str,
    redacted_line: str,
    *,
    path: str | Path | None = None,
) -> None:
    """Append raw and redacted log text to the local-only audit file."""
    audit_path = Path(path) if path is not None else REDACTION_AUDIT_PATH
    record = {"payload_id": payload_id, "raw_line": raw_line, "redacted_line": redacted_line}
    with _APPEND_LOCK:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def verify_chain(path: str | Path | None = None) -> bool:
    """Verify every entry's sequence, payload digest, previous hash, and entry hash."""
    log_path = Path(path) if path is not None else EGRESS_PATH
    if not log_path.exists():
        return True
    prev_hash = _ZERO_HASH
    expected_seq = 1
    try:
        with log_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    return False
                entry = json.loads(line)
                if entry.get("seq") != expected_seq or entry.get("prev_hash") != prev_hash:
                    return False
                status = entry.get("status")
                if status not in _STATUSES:
                    return False
                payload_sha256 = hashlib.sha256(_canonical_json(entry["payload"]).encode("utf-8")).hexdigest()
                if entry.get("payload_sha256") != payload_sha256:
                    return False
                expected_hash = hashlib.sha256(f"{prev_hash}{payload_sha256}{status}".encode("utf-8")).hexdigest()
                if entry.get("entry_hash") != expected_hash:
                    return False
                prev_hash = expected_hash
                expected_seq += 1
        return True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
