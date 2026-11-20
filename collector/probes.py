"""Mock or real service probes that append safe numeric telemetry records."""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config
from generator import append_record

try:
    import psutil
except ImportError:  # optional in mock mode
    psutil = None  # type: ignore[assignment]


def _timestamp(moment: datetime | None = None) -> str:
    """Return a UTC ISO-8601 timestamp with the required Z suffix."""
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: Any, default: float = 0.0) -> float:
    """Accept finite JSON numbers only and otherwise use a safe numeric default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if number == number and abs(number) != float("inf") else default


def _host_metrics() -> dict[str, float]:
    """Read local CPU, memory, and disk percentages without exposing host details."""
    if psutil is None:
        return {"cpu_pct": 0.0, "mem_pct": 0.0, "disk_pct": 0.0}
    try:
        return {
            "cpu_pct": float(psutil.cpu_percent(interval=None)),
            "mem_pct": float(psutil.virtual_memory().percent),
            "disk_pct": float(psutil.disk_usage("/").percent),
        }
    except Exception:
        return {"cpu_pct": 0.0, "mem_pct": 0.0, "disk_pct": 0.0}


def make_mock_records(moment: datetime | None = None) -> list[dict[str, Any]]:
    """Create one healthy mock record for each supported service host."""
    ts = _timestamp(moment)
    host = _host_metrics()
    return [
        {
            "ts": ts,
            "host": "banking-app",
            "metrics": {**host, "up": 1, "req_count": 20, "err_count": 0, "resp_ms": 42.0},
        },
        {
            "ts": ts,
            "host": "payment-service",
            "metrics": {**host, "up": 1, "req_count": 18, "err_count": 0, "resp_ms": 37.0},
        },
        {
            "ts": ts,
            "host": "postgres",
            "metrics": {**host, "db_up": 1, "db_conn_active": 12, "db_conn_max": 100, "db_size_mb": 256.0, "db_conn_errors": 0},
        },
    ]


def _service_probe(url: str, host_name: str) -> dict[str, Any]:
    """Fetch one app health endpoint and emit only its approved numeric fields."""
    import requests

    host = _host_metrics()
    fallback = {**host, "up": 0, "req_count": 0, "err_count": 0, "resp_ms": 0.0}
    try:
        response = requests.get(url, timeout=config.PROBE_TIMEOUT_S)
        if response.status_code < 200 or response.status_code >= 300:
            return {"ts": _timestamp(), "host": host_name, "metrics": fallback}
        data = response.json()
        if not isinstance(data, dict):
            return {"ts": _timestamp(), "host": host_name, "metrics": fallback}
        status = data.get("status")
        up = int(isinstance(status, str) and status.strip().lower() in {"ok", "healthy", "up"})
        return {
            "ts": _timestamp(),
            "host": host_name,
            "metrics": {
                **host,
                "up": up,
                "req_count": max(0, int(_number(data.get("req_count")))),
                "err_count": max(0, int(_number(data.get("err_count")))),
                "resp_ms": max(0.0, _number(data.get("avg_resp_ms"))),
            },
        }
    except Exception:
        return {"ts": _timestamp(), "host": host_name, "metrics": fallback}


def _postgres_probe() -> dict[str, Any]:
    """Read PostgreSQL activity and size using a configured read-only account."""
    host = _host_metrics()
    fallback = {**host, "db_up": 0, "db_conn_active": 0, "db_conn_max": 0, "db_size_mb": 0.0, "db_conn_errors": 1}
    try:
        import psycopg2

        connection = psycopg2.connect(
            host=config.POSTGRES_HOST,
            port=config.POSTGRES_PORT,
            dbname=config.POSTGRES_DB,
            user=config.POSTGRES_USER,
            password=config.POSTGRES_PASSWORD,
            sslmode=config.POSTGRES_SSLMODE,
            connect_timeout=max(1, int(config.PROBE_TIMEOUT_S)),
            options="-c default_transaction_read_only=on",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity")
                active = int(cursor.fetchone()[0])
                cursor.execute("SHOW max_connections")
                maximum = int(cursor.fetchone()[0])
                cursor.execute("SELECT pg_database_size(current_database())")
                size_bytes = int(cursor.fetchone()[0])
            return {
                "ts": _timestamp(),
                "host": "postgres",
                "metrics": {
                    **host,
                    "db_up": 1,
                    "db_conn_active": max(0, active),
                    "db_conn_max": max(0, maximum),
                    "db_size_mb": max(0.0, size_bytes / (1024.0 * 1024.0)),
                    "db_conn_errors": 0,
                },
            }
        finally:
            connection.close()
    except Exception:
        return {"ts": _timestamp(), "host": "postgres", "metrics": fallback}


def probe_real_records() -> list[dict[str, Any]]:
    """Probe application HTTP health endpoints and PostgreSQL read-only stats."""
    return [
        _service_probe(config.BANKING_HEALTH_URL, "banking-app"),
        _service_probe(config.PAYMENT_HEALTH_URL, "payment-service"),
        _postgres_probe(),
    ]


def append_records(records: list[dict[str, Any]], stream_path: str | Path = config.STREAM_PATH) -> None:
    """Append generated records as JSONL to the configured local stream."""
    for record in records:
        append_record(record, str(stream_path))


def inject_scenario(scenario: str, stream_path: str | Path = config.STREAM_PATH) -> list[dict[str, Any]]:
    """Append the requested mock failure scenario and return its records."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    host = _host_metrics()
    if scenario == "db_down":
        records = [
            {"ts": _timestamp(now + timedelta(seconds=index)), "host": "postgres", "metrics": {**host, "db_up": 0, "db_conn_active": 0, "db_conn_max": 100, "db_size_mb": 256.0, "db_conn_errors": 1}}
            for index in range(2)
        ]
    elif scenario == "db_up":
        records = [{"ts": _timestamp(now), "host": "postgres", "metrics": {**host, "db_up": 1, "db_conn_active": 12, "db_conn_max": 100, "db_size_mb": 256.0, "db_conn_errors": 0}}]
    elif scenario == "app_down":
        records = [
            {"ts": _timestamp(now + timedelta(seconds=index)), "host": "banking-app", "metrics": {**host, "up": 0, "req_count": 0, "err_count": 0, "resp_ms": 0.0}}
            for index in range(2)
        ]
    elif scenario == "conn_flood":
        records = [{"ts": _timestamp(now), "host": "postgres", "metrics": {**host, "db_up": 1, "db_conn_active": 98, "db_conn_max": 100, "db_size_mb": 256.0, "db_conn_errors": 0}}]
    elif scenario == "error_spike":
        records = [{"ts": _timestamp(now), "host": "banking-app", "metrics": {**host, "up": 1, "req_count": 100, "err_count": 30, "resp_ms": 110.0}}]
    else:
        raise ValueError(f"unknown probe scenario: {scenario}")
    append_records(records, stream_path)
    print(f"Appended {len(records)} {scenario} probe record(s)")
    return records


def run_forever(stream_path: str | Path = config.STREAM_PATH) -> None:
    """Append one set of service probes every configured interval."""
    while True:
        records = probe_real_records() if config.PROBE_MODE == "real" else make_mock_records()
        append_records(records, stream_path)
        time.sleep(config.PROBE_INTERVAL_S)


def main() -> None:
    """Run periodic probes or append a single mock failure scenario."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("scenario",))
    parser.add_argument("scenario", nargs="?", choices=("db_down", "db_up", "app_down", "conn_flood", "error_spike"))
    args = parser.parse_args()
    if args.command == "scenario":
        if args.scenario is None:
            parser.error("scenario requires a scenario name")
        inject_scenario(args.scenario)
    else:
        run_forever()


if __name__ == "__main__":
    main()
