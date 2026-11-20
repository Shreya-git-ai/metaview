"""Tests that generated JSONL records follow the input stream schema."""

from __future__ import annotations

import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator import HOSTS, append_record, inject, make_record


ALLOWED_KEYS = {"ts", "host", "metrics", "log_line"}
SCENARIOS = ("secret", "cpu", "fails", "latency", "duplicate", "late")


def _assert_record(record: dict[str, object]) -> None:
    assert set(record).issubset(ALLOWED_KEYS)
    assert isinstance(record["ts"], str) and record["ts"].endswith("Z")
    assert isinstance(record["host"], str)
    metrics = record["metrics"]
    assert isinstance(metrics, dict)
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in metrics.values())
    if "log_line" in record:
        assert isinstance(record["log_line"], str)


def test_normal_record_schema() -> None:
    for host in HOSTS:
        _assert_record(make_record(host, random.Random(1)))


def test_all_injected_scenario_records_schema() -> None:
    with tempfile.TemporaryDirectory() as directory:
        stream = Path(directory) / "stream.jsonl"
        append_record(make_record("srv-app-01", random.Random(7)), str(stream))
        for scenario in SCENARIOS:
            inject(scenario, random.Random(2), str(stream))
        records = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 18
        for record in records:
            _assert_record(record)
        assert all("log_line" not in record["metrics"] for record in records)
        assert records[1]["log_line"].startswith("auth ok")
        assert all(records[index]["log_line"].startswith("FAILED login") for index in range(7, 15))
