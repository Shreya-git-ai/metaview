"""Summarize collector delivery, outage replay, chain integrity, and leak checks."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from egress_log import verify_chain

KNOWN_SECRETS = (
    ("generator_password", "Abc@123"),
    ("generator_api_key", "sk-live-9f2c81"),
    ("generator_jwt", "eyJhbGciOi.abc.def"),
    ("generator_username", "rahul"),
    ("generator_ip", "10.0.0.7"),
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load valid JSON object lines from a local JSONL file."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _percentile(values: list[float], percentile: float) -> float | None:
    """Calculate a linearly interpolated percentile over sorted values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _timestamp_epoch(value: str) -> float:
    """Parse a UTC event timestamp into epoch seconds."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _load_outbox(db_path: Path) -> list[dict[str, Any]]:
    """Read outbox rows without mutating the collector database."""
    if not db_path.exists():
        return []
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT payload_id,event_time_utc,body,status,attempts,created_at,sent_at,acked_at FROM outbox")]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def compute_stats(data_dir: str | Path) -> dict[str, Any]:
    """Compute all stage 6 statistics from local queue and JSONL artifacts."""
    root = Path(data_dir)
    egress_path = root / "egress.jsonl"
    received_path = root / "received.jsonl"
    outbox = _load_outbox(root / "queue.db")
    egress = _read_jsonl(egress_path)
    received = _read_jsonl(received_path)

    acked_rows = [row for row in outbox if row["status"] == "acked" and row.get("acked_at") is not None]
    durations: list[float] = []
    for row in acked_rows:
        try:
            durations.append(float(row["acked_at"]) - _timestamp_epoch(row["event_time_utc"]))
        except (TypeError, ValueError, OverflowError):
            continue
    p50 = _percentile(durations, 0.50)
    p95 = _percentile(durations, 0.95)

    sent_ids = {
        entry.get("payload", {}).get("payload_id")
        for entry in egress
        if entry.get("status") == "sent" and isinstance(entry.get("payload"), dict)
    }
    sent_ids.discard(None)
    received_list = [entry.get("payload_id") for entry in received if isinstance(entry.get("payload_id"), str)]
    received_ids = set(received_list)
    missing_ids = sent_ids - received_ids
    unsent_received_ids = received_ids - sent_ids
    duplicate_count = len(received_list) - len(received_ids)
    outage_pass = not missing_ids and not unsent_received_ids and duplicate_count == 0

    egress_text = egress_path.read_text(encoding="utf-8") if egress_path.exists() else ""
    outbox_text = "\n".join(str(row["body"]) for row in outbox)
    leak_locations = [
        {"secret_label": label, "locations": [name for name, content in (("egress.jsonl", egress_text), ("outbox", outbox_text)) if secret in content]}
        for label, secret in KNOWN_SECRETS
        if secret in egress_text or secret in outbox_text
    ]
    statuses = {name: sum(1 for row in outbox if row["status"] == name) for name in ("acked", "rejected")}
    result: dict[str, Any] = {
        "payloads_created": len(outbox),
        "acked": statuses["acked"],
        "rejected": statuses["rejected"],
        "blocked": sum(1 for entry in egress if entry.get("status") == "blocked"),
        "duplicates_received": duplicate_count,
        "delivery": {
            "sample_count": len(durations),
            "p50_seconds": p50,
            "p95_seconds": p95,
            "p95_within_10_seconds": p95 is not None and p95 <= 10.0,
        },
        "outage": {
            "sent_count": len(sent_ids),
            "received_count": len(received_ids),
            "missing_count": len(missing_ids),
            "missing_ids": sorted(missing_ids),
            "unsent_received_count": len(unsent_received_ids),
            "duplicate_count": duplicate_count,
            "pass": outage_pass,
        },
        "chain_valid": verify_chain(egress_path),
        "secret_leak": {"pass": not leak_locations, "leaks": leak_locations},
    }
    return result


def write_stats(data_dir: str | Path, result: dict[str, Any] | None = None) -> Path:
    """Write a computed stats document to data/stats.json."""
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    output = root / "stats.json"
    stats = compute_stats(root) if result is None else result
    output.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def print_stats(stats: dict[str, Any]) -> None:
    """Print a compact key-value table with PASS/FAIL checks."""
    values = [
        ("Payloads created", stats["payloads_created"]),
        ("Acked", stats["acked"]),
        ("Rejected", stats["rejected"]),
        ("Blocked", stats["blocked"]),
        ("Duplicates received", stats["duplicates_received"]),
        ("Delivery p50 (s)", stats["delivery"]["p50_seconds"]),
        ("Delivery p95 (s)", stats["delivery"]["p95_seconds"]),
        ("p95 <= 10s", "PASS" if stats["delivery"]["p95_within_10_seconds"] else "FAIL"),
        ("Outage check", "PASS" if stats["outage"]["pass"] else "FAIL"),
        ("Hash chain", "PASS" if stats["chain_valid"] else "FAIL"),
        ("Secret leak check", "PASS" if stats["secret_leak"]["pass"] else "FAIL"),
    ]
    width = max(len(label) for label, _ in values)
    print(f"{'Metric':<{width}} | Value")
    print(f"{'-' * width}-+-{'-' * 32}")
    for label, value in values:
        print(f"{label:<{width}} | {value}")


def main() -> None:
    """Compute stats from collector/data and write stats.json."""
    data_dir = ROOT / "data"
    stats = compute_stats(data_dir)
    output = write_stats(data_dir, stats)
    print_stats(stats)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
