"""Quick CLI check of the running stack (uses TLS + the local CA).

    python scripts/api_check.py [alpha.admin|beta.admin|platform.operator]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared.envfile import load_env_file  # noqa: E402

load_env_file(ROOT / "config/saas.env")
BASE = f"https://localhost:{os.environ.get('SAAS_PORT', '8443')}/api/v1"
user = sys.argv[1] if len(sys.argv) > 1 else "alpha.admin"
c = httpx.Client(verify=str(ROOT / "certificates/ca.pem"), timeout=10)
tok = c.post(f"{BASE}/auth/login", json={"username": user, "password": os.environ["SAAS_DEMO_PASSWORD"]}).json()["token"]
h = {"Authorization": f"Bearer {tok}"}
ov = c.get(f"{BASE}/overview", headers=h).json()
print("tenant:", ov["tenant"]["name"], "| health:", ov["health"]["state"], ov["health"]["distribution"])
print("kpis:", json.dumps(ov["kpis"]))
print("freshness:", ov["freshness"])
for s in ov["sources"]:
    m = s["metrics"]
    print(f"  {s['source_id']} {s['role']:4s} {s['health_state']:9s} cpu={m.get('cpu_pct')} mem={m.get('mem_pct')} "
          f"lat={m.get('latency_ms')} age={s['data_age_s']}s stale={s['stale']}")
for i in ov["incidents"]:
    print("  INCIDENT", i["id"], i["status"], i["severity"], i["title"], "|", i["probable_cause"])
for a in ov["recent_anomalies"][:5]:
    print("  ANOMALY", a["severity"], a["reason"])
print("collectors:", [(x["id"], x["status"], x["heartbeat_age_s"], x["queue"]) for x in c.get(f"{BASE}/collectors", headers=h).json()])
perf = c.get(f"{BASE}/performance", headers=h).json()
print("perf ingest:", perf["collection_to_ingest_s"]["p95"], "breach->alert:", perf["breach_to_alert_s"]["p95"],
      "offline:", perf["offline_detection_s"]["last"], "delivery:", {k: v for k, v in perf["delivery"].items() if k != "reconciliations"})
