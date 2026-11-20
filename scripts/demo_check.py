"""Live acceptance check against the RUNNING stack (SaaS + both collectors must be up).

    python scripts/demo_check.py

Scenarios exercised through the collectors' local consoles (they change the real pipeline):
  * beta : SaaS connectivity failure (40 s) → buffered → retried → reconcile: 0 lost / 0 duplicates stored
  * alpha: collector offline (45 s)        → SaaS watchdog marks offline (< 60 s) → back online
  * beta : duplicate replay                → all detected as duplicates
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared.envfile import load_env_file  # noqa: E402

load_env_file(ROOT / "config/saas.env")
SAAS = "https://localhost:8443/api/v1"
LOCAL = {"alpha": "http://127.0.0.1:9101/local", "beta": "http://127.0.0.1:9102/local"}
c = httpx.Client(verify=str(ROOT / "certificates/ca.pem"), timeout=10)
lc = httpx.Client(timeout=10)


def tok(user):
    return {"Authorization": "Bearer " + c.post(f"{SAAS}/auth/login", json={
        "username": user, "password": os.environ["SAAS_DEMO_PASSWORD"]}).json()["token"]}


def col_status(h):
    return c.get(f"{SAAS}/collectors", headers=h).json()[0]


ha, hb = tok("alpha.admin"), tok("beta.admin")
print("1) trigger saas_outage on beta (40s) and collector_offline on alpha (45s)")
lc.post(f"{LOCAL['beta']}/scenarios/saas_outage", json={"duration_s": 40})
t_off = time.time()
lc.post(f"{LOCAL['alpha']}/scenarios/collector_offline", json={"duration_s": 45})
offline_seen = None
for _ in range(45):
    time.sleep(1)
    q = lc.get(f"{LOCAL['beta']}/status").json()["queue"]
    a = col_status(ha)
    if a["status"] == "offline" and offline_seen is None:
        offline_seen = time.time() - t_off
        print(f"   alpha marked OFFLINE by SaaS after {offline_seen:.1f}s ({a['status_reason']})")
print(f"   beta buffered during outage: {q}")
print("2) waiting for recovery and retry …")
for _ in range(60):
    time.sleep(1)
    q = lc.get(f"{LOCAL['beta']}/status").json()["queue"]
    if q["retrying"] == 0 and q["pending"] == 0 and col_status(ha)["status"] == "online":
        break
print(f"   beta queue after reconnect: {q}; alpha status: {col_status(ha)['status']}")
rec = lc.post(f"{LOCAL['beta']}/reconcile").json()
print(f"3) reconcile beta: checked={rec['checked']} present={rec['present']} missing={rec['missing']} "
      f"duplicates_stored={rec['duplicates_stored']}")
rep = lc.post(f"{LOCAL['beta']}/scenarios/duplicate_replay").json()["result"]
print(f"4) duplicate replay: sent={rep.get('sent')} accepted={rep.get('accepted')} duplicates={rep.get('duplicates')}")
perf = c.get(f"{SAAS}/performance", headers=hb).json()
print("5) beta performance:", {k: perf["collection_to_ingest_s"][k] for k in ("p95", "buffered_events", "buffered_max_delay_s")},
      "delivery:", {k: v for k, v in perf["delivery"].items() if k != "reconciliations"})
pa = c.get(f"{SAAS}/performance", headers=ha).json()
print("   alpha offline detection (s):", pa["offline_detection_s"]["last"], "| breach→alert p95:", pa["breach_to_alert_s"]["p95"])
ok = rec["missing"] == 0 and rep.get("duplicates") == rep.get("sent") and offline_seen is not None and offline_seen < 60
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
