"""Summarize collector delivery, outage replay, chain integrity, and leak checks."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from egress_log import verify_chain
import config

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


def _payload_event_type(body: str | dict[str, Any]) -> str:
    """Return an incident event type, or metrics for a metrics payload."""
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return "metrics"
    event = payload.get("event") if isinstance(payload, dict) else None
    event_type = event.get("type") if isinstance(event, dict) else None
    return event_type if isinstance(event_type, str) and event_type else "metrics"


def compute_stats(data_dir: str | Path) -> dict[str, Any]:
    """Compute all stage 6 statistics from local queue and JSONL artifacts."""
    root = Path(data_dir)
    egress_path = root / "egress.jsonl"
    received_path = root / "received.jsonl"
    outbox = _load_outbox(root / "queue.db")
    egress = _read_jsonl(egress_path)
    received = _read_jsonl(received_path)

    acked_rows = [row for row in outbox if row["status"] == "acked" and row.get("acked_at") is not None]
    acked_count = sum(1 for row in outbox if row["status"] == "acked")
    durations: list[float] = []
    grouped_durations: dict[str, list[float]] = {"metrics": [], "incident_events": []}
    for row in acked_rows:
        try:
            duration = float(row["acked_at"]) - _timestamp_epoch(row["event_time_utc"])
            durations.append(duration)
            group = "incident_events" if _payload_event_type(row["body"]) != "metrics" else "metrics"
            grouped_durations[group].append(duration)
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
    receiver_audit_empty = not received
    outage_comparison_performed = not (receiver_audit_empty and acked_count > 0)
    missing_ids = sent_ids - received_ids if outage_comparison_performed else set()
    unsent_received_ids = received_ids - sent_ids if outage_comparison_performed else set()
    duplicate_count = len(received_list) - len(received_ids) if outage_comparison_performed else 0
    outage_pass = not missing_ids and not unsent_received_ids and duplicate_count == 0
    receiver_log_available = received_path.exists()
    offline_flag_path = Path(config.FORCE_OFFLINE_FLAG)
    if not offline_flag_path.is_absolute():
        offline_flag_path = ROOT / offline_flag_path
    offline_window_logs = {root / "offline_windows.jsonl", offline_flag_path.parent / "offline_windows.jsonl"}
    offline_window_occurred = offline_flag_path.exists() or any(
        record.get("event") == "force_offline_started"
        for offline_window_log in offline_window_logs
        for record in _read_jsonl(offline_window_log)
    )

    egress_text = egress_path.read_text(encoding="utf-8") if egress_path.exists() else ""
    outbox_text = "\n".join(str(row["body"]) for row in outbox)
    leak_locations = [
        {"secret_label": label, "locations": [name for name, content in (("egress.jsonl", egress_text), ("outbox", outbox_text)) if secret in content]}
        for label, secret in KNOWN_SECRETS
        if secret in egress_text or secret in outbox_text
    ]
    statuses = {name: sum(1 for row in outbox if row["status"] == name) for name in ("acked", "rejected")}
    if not outage_comparison_performed:
        outage_status = "N/A (receiver log empty)"
        outage_reason = "no receiver audit rows are available despite acknowledged payloads"
    elif not receiver_log_available and not offline_window_occurred:
        outage_status = "N/A (no outage)"
        outage_reason = "no FORCE_OFFLINE_FLAG window was recorded"
    elif not receiver_log_available:
        outage_status = "N/A (received log unavailable)"
        outage_reason = "data/received.jsonl is absent; receiver IDs cannot be compared"
    elif not offline_window_occurred:
        outage_status = "N/A (no outage)"
        outage_reason = "no FORCE_OFFLINE_FLAG window was recorded"
    else:
        outage_status = "PASS" if outage_pass else "FAIL"
        outage_reason = None
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
            "by_payload_type": {
                name: {
                    "sample_count": len(values),
                    "p50_seconds": _percentile(values, 0.50),
                    "p95_seconds": _percentile(values, 0.95),
                }
                for name, values in grouped_durations.items()
            },
        },
        "outage": {
            "sent_count": len(sent_ids),
            "received_count": len(received_ids),
            "missing_count": len(missing_ids),
            "missing_ids": sorted(missing_ids),
            "unsent_received_count": len(unsent_received_ids),
            "unsent_received_ids": sorted(unsent_received_ids),
            "duplicate_count": duplicate_count,
            "receiver_log_available": receiver_log_available,
            "status": outage_status,
            "reason": outage_reason,
            "pass": outage_pass if outage_comparison_performed and receiver_log_available and offline_window_occurred else None,
            "receiver_audit_empty": receiver_audit_empty,
            "comparison_performed": outage_comparison_performed,
            "force_offline_flag_present": offline_flag_path.exists(),
            "force_offline_window_occurred": offline_window_occurred,
            "offline_window_history_available": any(path.exists() for path in offline_window_logs),
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
        ("Outage check", stats["outage"]["status"]),
        ("Hash chain", "PASS" if stats["chain_valid"] else "FAIL"),
        ("Secret leak check", "PASS" if stats["secret_leak"]["pass"] else "FAIL"),
    ]
    width = max(len(label) for label, _ in values)
    print(f"{'Metric':<{width}} | Value")
    print(f"{'-' * width}-+-{'-' * 32}")
    for label, value in values:
        print(f"{label:<{width}} | {value}")
    for group, label in (("metrics", "Metrics"), ("incident_events", "Incident events")):
        timing = stats["delivery"]["by_payload_type"][group]
        print(f"Delivery {label} p50/p95 (s): {timing['p50_seconds']} / {timing['p95_seconds']} (n={timing['sample_count']})")
    outage = stats["outage"]
    if stats["acked"] > 0 and outage["receiver_audit_empty"]:
        print("WARN: receiver audit empty")
    print("Outage check details:")
    print(f"  Sent IDs: {outage['sent_count']}")
    print(f"  Received IDs: {outage['received_count']}")
    if outage["comparison_performed"]:
        print(f"  IDs sent but not received ({outage['missing_count']}): {outage['missing_ids']}")
        print(f"  IDs received but not sent ({outage['unsent_received_count']}): {outage['unsent_received_ids']}")
    else:
        print("  ID comparison skipped: receiver audit empty")
    print(f"  Duplicates: {outage['duplicate_count']}")
    if outage["reason"]:
        print(f"  Reason: {outage['reason']}")
    print(f"  FORCE_OFFLINE_FLAG currently present: {outage['force_offline_flag_present']}")
    print(f"  FORCE_OFFLINE_FLAG window occurred: {outage['force_offline_window_occurred']}")
    print(f"  Offline-window history available: {outage['offline_window_history_available']}")


def payload_details(data_dir: str | Path) -> list[dict[str, Any]]:
    """Build delivery latency detail rows sorted slowest first."""
    root = Path(data_dir)
    outbox = _load_outbox(root / "queue.db")
    egress = _read_jsonl(root / "egress.jsonl")
    retried_ids = {
        entry.get("payload", {}).get("payload_id")
        for entry in egress
        if entry.get("status") == "retried" and isinstance(entry.get("payload"), dict)
    }
    details: list[dict[str, Any]] = []
    for row in outbox:
        try:
            latency = (
                float(row["acked_at"]) - _timestamp_epoch(row["event_time_utc"])
                if row.get("acked_at") is not None
                else None
            )
        except (TypeError, ValueError, OverflowError):
            latency = None
        details.append({
            "payload_id": row["payload_id"],
            "event_type": _payload_event_type(row["body"]),
            "event_time_utc": row["event_time_utc"],
            "acked_at": row.get("acked_at"),
            "latency_s": latency,
            "retried": row["payload_id"] in retried_ids,
        })
    details.sort(key=lambda item: (item["latency_s"] is None, -(item["latency_s"] or 0.0)))
    return details


def print_payload_details(data_dir: str | Path) -> None:
    """Print the optional per-payload latency report."""
    details = payload_details(data_dir)
    print("\nPer-payload delivery latency (slowest first):")
    print("payload_id | event_type | event_time_utc | acked_at | latency_s | retried")
    for row in details:
        acked_at = "" if row["acked_at"] is None else f"{float(row['acked_at']):.3f}"
        latency = "" if row["latency_s"] is None else f"{row['latency_s']:.3f}"
        print(f"{row['payload_id']} | {row['event_type']} | {row['event_time_utc']} | {acked_at} | {latency} | {'yes' if row['retried'] else 'no'}")


def main(argv: list[str] | None = None) -> None:
    """Compute stats from collector/data and write stats.json."""
    parser = argparse.ArgumentParser(description="Report collector delivery and egress statistics")
    parser.add_argument("--verbose", action="store_true", help="print per-payload delivery latency details")
    args = parser.parse_args(argv)
    data_dir = ROOT / "data"
    stats = compute_stats(data_dir)
    output = write_stats(data_dir, stats)
    print_stats(stats)
    if args.verbose:
        print_payload_details(data_dir)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
