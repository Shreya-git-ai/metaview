"""End-to-end stage 3 check using JSONL records from the real generator."""

from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

COLLECTOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COLLECTOR_DIR))

from allowlist import apply_allowlist, build_payload
from generator import append_record, inject, make_record
from local_detect import LocalDetector
from redact import redact_text


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Read generated JSONL records from disk."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _report(name: str, passed: bool, detail: str = "") -> bool:
    """Print a single check result and return its result value."""
    print(f"{'PASS' if passed else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    return passed


def main() -> int:
    """Generate, process, and validate stage 3 telemetry scenarios."""
    results: list[bool] = []
    rng = random.Random(20261120)
    detector = LocalDetector(baseline_min_samples=20, anomaly_z=3.0, failed_login_threshold=5)

    with tempfile.TemporaryDirectory(prefix="check_detect_") as temp_dir:
        stream = Path(temp_dir) / "stream.jsonl"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for index in range(30):
            record = make_record(
                "srv-app-01",
                rng,
                ts=(now + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
                cpu_pct=35.0 + rng.uniform(-0.3, 0.3),
            )
            append_record(record, str(stream))
        inject("cpu", rng, str(stream))

        generated = _read_records(stream)
        cpu_events: list[dict[str, Any]] = []
        for record in generated:
            cpu_events.extend(
                detector.process(
                    record["host"],
                    record.get("metrics"),
                    record.get("log_line"),
                    record["ts"],
                )
            )
        cpu_anomalies = [event for event in cpu_events if event["type"] == "cpu_anomaly"]
        results.append(
            _report(
                "CPU scenario emits anomaly with sigma context",
                bool(cpu_anomalies) and "sigma" in cpu_anomalies[0]["alert_context"],
                cpu_anomalies[0]["alert_context"] if cpu_anomalies else "no cpu_anomaly emitted",
            )
        )

        fails_start = len(generated)
        inject("fails", rng, str(stream))
        all_records = _read_records(stream)
        failure_records = all_records[fails_start:]
        failure_events: list[dict[str, Any]] = []
        for record in failure_records:
            failure_events.extend(
                detector.process(
                    record["host"],
                    record.get("metrics"),
                    record.get("log_line"),
                    record["ts"],
                )
            )
        auth_events = [event for event in failure_events if event["type"] == "auth_failures"]
        serialized_auth = json.dumps(auth_events, sort_keys=True)
        no_raw_auth_data = all(secret not in serialized_auth for secret in ("admin", "10.0.0.7", "FAILED", "user="))
        results.append(
            _report(
                "Eight failure lines emit exactly one safe auth event",
                len(auth_events) == 1 and no_raw_auth_data,
                f"events={len(auth_events)}, event={serialized_auth}",
            )
        )

        generated_events = cpu_events + failure_events
        payloads_ok = True
        payload_failure = ""
        for index, event in enumerate(generated_events, start=1):
            source_record = generated[-1] if event in cpu_events else failure_records[-1]
            candidate = build_payload(
                source_record["host"],
                source_record["ts"],
                event={key: event[key] for key in ("type", "severity", "count")},
                category=event["category"],
                payload_id=f"check-{index:06d}",
                alert_context=event["alert_context"],
            )
            clean, dropped = apply_allowlist(candidate)
            serialized = json.dumps(clean, sort_keys=True)
            redacted, safety_rules = redact_text(serialized)
            if dropped or redacted != serialized:
                payloads_ok = False
                payload_failure = (
                    f"payload={index}, dropped={dropped}, safety_changed={redacted != serialized}, "
                    f"rules={safety_rules}"
                )
                break
        results.append(
            _report(
                "All generated events pass allow-list and final redaction check",
                payloads_ok,
                payload_failure or f"validated {len(generated_events)} event payload(s)",
            )
        )

        late_start = len(all_records)
        inject("late", rng, str(stream))
        late_record = _read_records(stream)[late_start]
        baseline_before = list(detector._baselines[("srv-app-01", "cpu_pct")])
        late_events = detector.process(
            late_record["host"], late_record.get("metrics"), late_record.get("log_line"), late_record["ts"]
        )
        baseline_after_late = list(detector._baselines[("srv-app-01", "cpu_pct")])
        inject("duplicate", rng, str(stream))
        duplicate_records = _read_records(stream)
        duplicate_record = duplicate_records[-1]
        duplicate_events = detector.process(
            duplicate_record["host"],
            duplicate_record.get("metrics"),
            duplicate_record.get("log_line"),
            duplicate_record["ts"],
        )
        still_detects_cpu = any(
            event["type"] == "cpu_anomaly"
            for event in detector.process(
                "srv-app-01", {"cpu_pct": 92.0}, None, (now + timedelta(seconds=100)).isoformat().replace("+00:00", "Z")
            )
        )
        late_duplicate_ok = (
            late_record["ts"].endswith("Z")
            and late_events == []
            and baseline_before != []
            and baseline_after_late != []
            and duplicate_record == late_record
            and isinstance(duplicate_events, list)
            and still_detects_cpu
        )
        results.append(
            _report(
                "Late and duplicate records are safe and late data preserves baseline detection",
                late_duplicate_ok,
            )
        )

        failure_times = [datetime.fromisoformat(record["ts"].replace("Z", "+00:00")) for record in failure_records]
        span = (max(failure_times) - min(failure_times)).total_seconds() if failure_times else float("inf")
        results.append(
            _report(
                "All eight failure timestamps fit within 60 seconds",
                len(failure_times) == 8 and span <= 60,
                f"span={span:g}s",
            )
        )

    passed = all(results)
    print(f"RESULT: {'PASS' if passed else 'FAIL'} ({sum(results)}/{len(results)} checks passed)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
