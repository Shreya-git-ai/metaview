"""Append demo telemetry records to a local JSONL stream."""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import STREAM_PATH

HOSTS: tuple[str, ...] = ("srv-app-01", "srv-db-01", "router-01")


def _timestamp(moment: datetime | None = None) -> str:
    """Format a UTC timestamp using the required Z suffix."""
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_record(host: str, rng: random.Random, ts: str | None = None, **metrics: float) -> dict[str, Any]:
    """Build one telemetry record with realistic metric defaults."""
    values: dict[str, float] = {
        "cpu_pct": round(rng.uniform(30, 45), 1),
        "mem_pct": round(rng.uniform(48, 53), 1),
        "latency_ms": round(rng.uniform(20, 30), 1),
        "packet_loss_pct": round(max(0.0, rng.gauss(0, 0.08)), 2),
    }
    values.update(metrics)
    return {"ts": ts or _timestamp(), "host": host, "metrics": values}


def append_record(record: dict[str, Any], stream_path: str = STREAM_PATH) -> None:
    """Append one JSON record to the configured stream."""
    path = Path(stream_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")


def inject(scenario: str, rng: random.Random, stream_path: str = STREAM_PATH) -> None:
    """Append one of the documented demo failure scenarios."""
    now = datetime.now(timezone.utc)
    if scenario == "secret":
        record = make_record("srv-app-01", rng, log_line="auth ok user=rahul password=Abc@123 api_key=sk-live-9f2c81 token=eyJhbGciOi.abc.def")
        append_record(record, stream_path)
    elif scenario == "cpu":
        for _ in range(5):
            append_record(make_record("srv-app-01", rng, cpu_pct=round(rng.uniform(85, 95), 1), latency_ms=round(rng.uniform(120, 180), 1)), stream_path)
    elif scenario == "fails":
        for index in range(8):
            record = make_record("srv-app-01", rng, ts=_timestamp(now + timedelta(seconds=index)), log_line="FAILED login user=admin ip=10.0.0.7")
            append_record(record, stream_path)
    elif scenario == "latency":
        append_record(make_record("router-01", rng, latency_ms=round(rng.uniform(150, 300), 1), packet_loss_pct=round(rng.uniform(3, 8), 1)), stream_path)
    elif scenario == "duplicate":
        path = Path(stream_path)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if lines:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(lines[-1] + "\n")
    elif scenario == "late":
        append_record(make_record("srv-app-01", rng, ts=_timestamp(now - timedelta(minutes=2))), stream_path)


def run_forever(rng: random.Random, stream_path: str = STREAM_PATH) -> None:
    """Append one record per host per second until interrupted."""
    while True:
        for host in HOSTS:
            append_record(make_record(host, rng), stream_path)
        time.sleep(1)


def main() -> None:
    """Parse CLI arguments and run the generator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None, help="random seed for repeatable telemetry")
    parser.add_argument("command", nargs="?", choices=("inject",), default=None)
    parser.add_argument("scenario", nargs="?", choices=("secret", "cpu", "fails", "latency", "duplicate", "late"))
    args = parser.parse_args()
    rng = random.Random(args.seed)
    if args.command == "inject":
        if args.scenario is None:
            parser.error("inject requires a scenario")
        inject(args.scenario, rng)
    else:
        run_forever(rng)


if __name__ == "__main__":
    main()
