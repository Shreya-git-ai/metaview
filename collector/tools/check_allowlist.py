"""Manual check for allowlist.py (stage 2). Run from the collector/ folder."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from allowlist import apply_allowlist  # noqa: E402
from redact import redact_text  # noqa: E402

candidate = {
    "payload_id": "col-01-000001",
    "tenant_id": "city-hospital",
    "collector_id": "col-01",
    "collector_version": "0.1.0",
    "source_id": "src-1a2b3c4d",
    "event_time_utc": "2026-11-20T10:20:15Z",
    "source_tz": "Asia/Kolkata",
    "category": "infra",
    "metrics": {"cpu_pct": 34.0, "mem_pct": 51.0, "latency_ms": 22.0, "packet_loss_pct": 0.0},
    "event": {"type": "cpu_anomaly", "severity": "high", "count": 1},
    "health_state": "Fair",
    "alert_context": "cpu 3.1 sigma above local baseline (mean 35, now 82)",
    # fields below must be dropped
    "hostname": "srv-app-01",
    "log_line": "auth ok user=rahul password=Abc@123",
}

failed = False
clean, dropped = apply_allowlist(candidate)
print("dropped:", dropped)

dropped_names = {d["field"] for d in dropped}
for bad in ("hostname", "log_line"):
    if bad in clean or bad not in dropped_names:
        print(f"FAIL: unknown field '{bad}' was not dropped and reported")
        failed = True

blob = json.dumps(clean)
for leak in ("srv-app-01", "Abc@123", "rahul"):
    if leak in blob:
        print(f"FAIL: leak found in payload: {leak}")
        failed = True

# Critical: clean payload must survive the final safety check unchanged
redacted, rules = redact_text(blob)
if redacted != blob:
    print("FAIL: redact_text changes a CLEAN payload (false positive). rules:", rules)
    failed = True

# alert_context truncation
long_ctx = dict(candidate, alert_context="x " * 200)
clean2, _ = apply_allowlist(long_ctx)
if len(clean2.get("alert_context", "")) > 120:
    print("FAIL: alert_context longer than 120 chars")
    failed = True

print("RESULT:", "FAIL" if failed else "ALL CHECKS PASSED")
sys.exit(1 if failed else 0)