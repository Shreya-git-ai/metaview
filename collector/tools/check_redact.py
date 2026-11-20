"""Quick manual check for redact.py (stage 1). Run from the collector/ folder."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redact import redact_text  # noqa: E402

STREAM = ROOT / "data" / "stream.jsonl"
SECRETS = ["Abc@123", "sk-live-9f2c81", "eyJhbGciOi.abc.def"]
SAFE_LINES = [
    "GET /health 200 12ms",
    "cpu 3.1 sigma above local baseline (mean 35, now 82)",
]

failed = False

# 1. Secret line from the stream must come out clean
log_line = None
for raw in reversed(STREAM.read_text().splitlines()):
    rec = json.loads(raw)
    if "api_key" in rec.get("log_line", ""):
        log_line = rec["log_line"]
        break

if log_line is None:
    print("FAIL: no secret line found. Run: python generator.py inject secret")
    sys.exit(1)

redacted, rules = redact_text(log_line)
print("redacted :", redacted)
print("rules    :", rules)
for s in SECRETS:
    if s in redacted:
        print(f"FAIL: a secret is still present in the output ({s[:4]}...)")
        failed = True

# 2. Normal lines must NOT change (false-positive check)
for line in SAFE_LINES:
    out, _ = redact_text(line)
    if out != line:
        print(f"FAIL: clean line was changed -> {out}")
        failed = True

# 3. Idempotency
once, _ = redact_text(log_line)
twice, _ = redact_text(once)
if once != twice:
    print("FAIL: redaction is not idempotent")
    failed = True

print("RESULT:", "FAIL" if failed else "ALL CHECKS PASSED")
sys.exit(1 if failed else 0)
