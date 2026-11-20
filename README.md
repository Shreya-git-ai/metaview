# NEXORA — Secure AIOps Observability (CICADA 2026 MVD)

A privacy-first, two-tier observability platform. A **Local Collector** runs inside the customer environment, collects
raw telemetry, filters/redacts/aggregates/analyses it locally and sends **only allow-listed metadata** over
**authenticated TLS** to a **Central SaaS**, which provides tenant-isolated fleet analytics, health classification,
explainable anomaly detection, incident correlation and recommendations.

```
RAW TELEMETRY → FILTER → REDACT → AGGREGATE → LOCAL ANALYSIS → POLICY VALIDATION → EGRESS AUDIT (exact bytes + SHA-256)
   → SQLite OUTBOX → HTTPS (TLS, CA-verified) + collector token → SaaS INGEST (re-validate, tenant-bind, idempotent)
   → HEALTH (stale-aware) → ANOMALY DETECTION → CORRELATION → PROBABLE CAUSE (hypothesis) → RECOMMENDATIONS → DASHBOARD
```

## Quick start (Windows, PowerShell)

Prerequisites: Python 3.11+ (tested 3.13), Node 20+ (tested 24). Frontend: React 18, Vite 6, Tailwind 4, Recharts, Lucide, Framer Motion.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install fastapi "uvicorn[standard]" sqlalchemy cryptography pytest PyJWT httpx pydantic numpy psutil
.venv\Scripts\python scripts\setup_dev.py          # local CA + TLS cert + synthetic secrets in config\*.env
cd frontend; npm install; cd ..
powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1     # starts all 4 processes + opens the browser
```

Or start the five **separate processes** manually (each in its own terminal, from the repo root):

| Process | Command | Address |
|---|---|---|
| SaaS backend (TLS only) | `.venv\Scripts\python -m saas_backend.main --env config/saas.env` | https://localhost:8443 |
| Collector — Customer Alpha | `.venv\Scripts\python -m local_collector.collector --env config/col-alpha-01.env` | console API http://127.0.0.1:9101 |
| Collector — Customer Beta | `.venv\Scripts\python -m local_collector.collector --env config/col-beta-01.env` | console API http://127.0.0.1:9102 |
| SaaS dashboard (Tier 2 UI) | `cd frontend; npm run dev` | http://localhost:5173 |
| Local Collector Console (Tier 1 UI, separate app) | `cd frontend; npm run dev:local` | http://localhost:5174 |

The two UIs are separate apps with separate dev proxies: **5173 can only reach the SaaS API** (`/api`, TLS verified
against the dev CA); **5174 can only reach the collectors' loopback consoles**. The SaaS UI therefore cannot fetch raw
telemetry even by accident. Frontend settings: `frontend/.env.example` (`VITE_API_BASE`, `VITE_LOCAL_CONSOLE_URL`,
`NEXORA_SAAS_URL`, `NEXORA_CA_CERT`).

Log in at http://localhost:5173 with a synthetic user — `alpha.admin`, `beta.admin` or `platform.operator` —
password = `SAAS_DEMO_PASSWORD` in `config/saas.env` (default `Demo#2026`). Wait ~60 s after start before triggering
anomaly scenarios: the baselines need 20 points (3 s windows) to warm up.

## TLS (local development)

* `scripts/setup_dev.py` (→ `scripts/gen_certs.py`) creates `certificates/ca.pem` (local dev root CA) and
  `certificates/server.pem` (SAN `localhost`, `127.0.0.1`) signed by it (ECDSA P-256).
* The SaaS backend **refuses to start without a certificate**; collectors **refuse any `http://` SaaS URL** and verify
  the server certificate against `ca.pem` (TLS ≥ 1.2). There is no `verify=False` anywhere.
* The Vite dev proxy also verifies the SaaS certificate against `ca.pem`, so the browser talks to Vite on
  `localhost:5173` and never needs to trust the dev CA. To call the API directly from a browser or other tools,
  trust the CA (current user only): `certutil -user -addstore Root certificates\ca.pem` (remove it later with
  `certutil -user -delstore Root "NEXORA Dev Local CA"`).
* Regenerate everything: `python scripts/setup_dev.py --force` (then delete `data/` so collectors re-enroll).

## SaaS UI modules (http://localhost:5173)

| Module | Route | Highlights (all backed by real API data) |
|---|---|---|
| Command Center | `/` | KPI cards (animated, clickable), health distribution filter, source inventory, 6 live charts with threshold lines + anomaly markers, global time range, incidents, recommendations, recent events |
| Infrastructure | `/infrastructure`, `/infrastructure/:id` | inventory with filters, metric comparison chart, per-source detail: charts, anomalies, incidents, health history |
| Network | `/network` | latency / loss / bandwidth stats (avg, p95, max), degradation reasons, network anomalies & incidents |
| Security Centre | `/security` | failed-login trends & per-source counts, locally summarised bursts, security anomalies/incidents — no raw auth data |
| AIOps Intelligence | `/aiops` | anomaly detection by category, event correlation, root-cause assistance (hypotheses), recommendations, baselines, method |
| Incidents | `/incidents` | stats + MTTR, filters/search, detail drawer (signals, metrics at detection, timeline), acknowledge / resolve / reopen, simulated remediation |
| Collector Fleet | `/collectors` | online/degraded/offline, search/filters, drawer: identity, connection, buffer (as reported), processing counters, heartbeat timeline, history |
| Privacy & Egress | `/privacy` | processed/approved/blocked/redacted counts, local-vs-approved explainer, redaction activity by rule, egress record drawer (9-step audit), violations, transfer audit, allow-list |
| Tenants & Isolation | `/tenants` | tenant overview, live cross-tenant probe (all denied), audited denials |
| Performance | `/performance` | **NEXORA platform** (API latency/errors, ingestion volume, DB health) vs **customer telemetry SLOs** |
| Settings & Audit | `/settings` | per-tenant thresholds with 4-band preview, health transitions, audit log |

Global shell: collapsible grouped sidebar (tooltips when collapsed), mobile drawer, breadcrumbs, tenant selector
(platform operator), region, global health, telemetry age, collector connectivity, notifications, Ctrl/⌘+K search,
user menu with theme toggle (dark default, light available), toasts on new incidents. Framer Motion animations respect
`prefers-reduced-motion`.

## What to demo

The **Local Collector Console** (http://localhost:5174, linked from the SaaS sidebar) is the separate **Tier-1 app**:
process/connectivity status, raw logs with synthetic secrets highlighted next to their redacted form, allow-list
validation, approved payloads + SHA-256, blocked payloads, local buffer, the egress proof (raw → redacted → payload →
SaaS hash verification) and the scenario controls. Its scenario buttons change what the generator/collector pipeline
really does:

| Scenario | What happens (real pipeline) | Where to look |
|---|---|---|
| Normal operation | clears all scenarios | Command Center: all Excellent |
| CPU spike / Memory spike | metric driven to ~95 % / ~92 % on one host | AIOps: anomaly vs baseline (expected range, z, reason) |
| Multiple related events | CPU + memory + app response degrade together | AIOps: **one** incident, rule R-RES-01, hypothesis + recommendations |
| Network latency / Packet loss | latency ~300 ms / loss ~6 % on all hosts | network-wide incident R-NET-01/02 |
| Repeated failed logins | synthetic SSH failures in raw auth log | locally summarised counts only; R-SEC-01 incident; raw records stay local |
| Sensitive log injection | synthetic passwords/API keys/JWT/AWS keys in raw logs + a simulated misconfigured plugin trying to attach a raw log line | Local Console → Egress proof: raw (secret highlighted) → redacted → exact payload → SaaS hash match, 0 secrets in SaaS copy, blocked payload with reasons. SaaS Privacy page: redaction counts/rule ids only |
| Collector offline | **simulated** process hang: no sampling, sending or heartbeats | Collector Fleet: offline in ≤ ~35 s; sources Poor/stale; R-COL-01 incident; auto-recovers |
| SaaS connectivity failure | **simulated** network partition at the collector egress | Local Console queue “retrying”; after recovery: delivered, “Reconcile delivery” → missing 0 |
| Duplicate replay | re-sends 20 delivered payloads byte-for-byte | all reported as duplicates; Privacy → ingest audit |

Also: **Tenants & Isolation → “Attempt cross-tenant access”** fires real cross-tenant requests (all denied + audited);
**AIOps → Approve & run (simulated)** demonstrates safe, approval-gated, audited, reversible remediation;
**Settings** edits per-tenant health thresholds; **Performance** shows measured latencies.

`python scripts/demo_check.py` runs the outage / offline / duplicate scenarios against the running stack and prints
PASS/FAIL. `python scripts/api_check.py alpha.admin` prints a CLI summary.

## Architecture

```
local_collector/            Tier 1 — independent process (one per customer environment)
  telemetry_generator.py    synthetic hosts (AR noise + daily-ish cycles) + raw logs; optional real host via psutil
  scenarios.py              live demo scenarios (ramped, time-boxed)
  redaction.py              secret rules (remove) + identifier rules (HMAC pseudonymise)
  processor.py              filter → aggregate (3 s windows) → local EWMA analysis → candidate payloads
  policy_engine.py          strict schema + identity + secret scan + canary check → allow / block (+ reasons)
  egress_audit.py           append-only record of the exact bytes, SHA-256, decision, transmission status
  local_queue.py            SQLite outbox (approved metadata only), exponential backoff
  sender.py                 TLS transport, enrollment → per-collector token, no plaintext
  console_api.py            Tier-1 console API (127.0.0.1 only)
saas_backend/               Tier 2 — FastAPI over HTTPS
  api/                      collector, dashboard, aiops, privacy routes
  security/auth.py          collector tokens (SHA-256 stored), JWT users, server-side TenantContext
  services/                 ingest, health (stale-aware), maintenance (watchdog), performance, seed
  ai_engine/detector.py     rolling robust z-score anomaly detection
  correlation/engine.py     incident grouping, rule signatures, hypotheses, explainable recommendations
shared/contract.py          THE egress contract (allow-list) used by both tiers
frontend/                   React + Vite + Tailwind + Recharts + Lucide
tests/                      pytest (unit, API, real-TLS end-to-end)
```

### Egress contract (allow-list)
`schema_version, event_id, tenant_id, collector_id, source_id (HMAC pseudonym), source_role, category, window_start,
event_time, metrics{cpu_pct, mem_pct, disk_pct, net_mbps, latency_ms, packet_loss_pct, app_response_ms, app_up,
auth_failures, auth_distinct_accounts, auth_distinct_sources}, health_state, severity, context_code (enum),
context_params (numbers only)`. Pydantic `extra="forbid"`, strict types and bounds. No free text can leave.

### Egress proof
The collector serialises each payload once as canonical JSON, records those exact bytes + SHA-256, and sends the same
bytes. The SaaS hashes the canonical form of what it received and stores it. Equal hashes ⇒ the egress proof equals what
SaaS received. Verifiable in the UI (Privacy page), from the collector (`POST /local/verify/{event_id}`) and in tests.

### Tenant isolation
Tenant comes from the signed JWT (or, for collectors, from the token→collector→tenant binding) — never from the
client. Every query filters on it; cross-tenant ids return 404, spoofed `X-Tenant-Id` returns 403, both are audited.
A collector can only submit and read back events for its own tenant/collector id.

### AIOps (honest description)
* **Detection:** per (tenant, source, metric) rolling median/MAD over the last 60 non-anomalous points; robust
  z = (x − median)/max(1.4826·MAD, floor). Anomaly if z ≥ 4, or z ≥ 3 twice in a row, **and** deviation ≥ a per-metric
  minimum. Anomalous points are excluded from future baselines.
* **Correlation:** anomalies on the same source (or network signals on the same collector) within 120 s join one
  incident; ordered rule signatures (R-RES-01 CPU+memory+app response, R-NET-01 latency+loss, R-SEC-01 auth failures …)
  select the title, the **probable cause — always labelled a hypothesis** — and recommendations, each quoting its
  evidence. No ML model and no confidence score are used or claimed.
* Incidents auto-resolve after 90 s without anomalous signals; remediation actions are **simulated**.

### Health
Four states (Excellent/Good/Fair/Poor), worst metric wins, per-tenant configurable thresholds. Every transition stores
previous/new state, triggering metric, reason and UTC time. Data older than 15 s ⇒ marked stale and downgraded
(at least Fair); > 60 s or collector offline ⇒ Poor. A stale value is never shown as current.

### Reliability
Outbox statuses pending → delivered | retrying → delivered | failed. Global + per-item exponential backoff (1→16 s).
Idempotent ingestion on `(tenant_id, event_id)`; event time preserved; out-of-order events never overwrite newer
state. `POST /local/reconcile` compares the collector's delivered ids with the SaaS store (0 lost / 0 duplicates).

## API overview (all under `/api/v1`)

* **Collector (TLS + collector token):** `POST /collectors/register`, `POST /collectors/auth`, `POST /ingest`,
  `POST /collectors/heartbeat`, `GET /collectors/me/status`, `GET /collectors/me/received/{event_id}`,
  `POST /collectors/me/reconcile`
* **Dashboard (JWT):** `/auth/login`, `/overview`, `/sources`, `/metrics/latest`, `/metrics/history`, `/health`,
  `/health/transitions`, `/network`, `/security/summaries`, `/collectors[/{id}]`, `/tenants[/{id}[/metrics|/incidents]]`,
  `/settings/thresholds`, `/audit`, `/performance`
* **AIOps:** `/aiops/anomalies`, `/aiops/baselines`, `/aiops/incidents[/{id}]`, `/aiops/root-causes`,
  `/aiops/recommendations`, `/aiops/method`, `POST /aiops/incidents/{id}/acknowledge|resolve`,
  `POST /aiops/recommendations/{id}/execute|revert`
* **Privacy:** `/privacy/received[/{event_id}]`, `/privacy/ingest-audit`, `/privacy/rejections`, `/privacy/policy`
* **Local console (127.0.0.1:910x):** `/local/status|raw|redactions|filter-example|aggregates|egress|proof|policy|queue|scenarios`,
  `POST /local/scenarios/{name}`, `POST /local/verify/{event_id}`, `POST /local/reconcile`

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q
```
29 tests: secret redaction, pseudonymisation, filtering, strict allow-listing & schema validation, raw-data exclusion,
collector authentication, plaintext refusal, untrusted-certificate rejection, tenant isolation (header spoofing, path
ids, collector read-back), idempotency & out-of-order handling, health transitions & configurable thresholds,
stale-data & offline detection, anomaly detection, correlation + recommendations + safe remediation, buffering during
outage, retry after reconnection, zero lost / zero duplicate reconciliation, and the **end-to-end privacy test over a
real TLS socket** (synthetic passwords in raw logs → absent from the payload and from every byte of the SaaS database
→ received payload SHA-256 equals the egress audit), plus the enterprise endpoints (incident filters/reopen/metrics at
detection, source detail, fleet & heartbeat timeline, degraded state, privacy summary, platform health, security and
network overviews, search, events) each with tenant-isolation assertions.

## Simulated / limited (stated plainly)

* Telemetry is **synthetic** (optionally plus this machine's real CPU/mem/disk via `COLLECTOR_INCLUDE_LOCAL_HOST=true`).
  All secrets are synthetic and contain `SYNTH`.
* *Collector offline* and *SaaS connectivity failure* scenarios are **simulated inside the collector** (halt / egress
  partition). The real equivalents also work: stop the collector process, or stop the SaaS process.
* Remediation actions are **simulated** (approval + audit + revert; nothing touches infrastructure).
* Dev TLS uses a self-signed local CA; collector auth is bearer-token over TLS (mTLS would be the next step).
* SQLite for both tiers (SQLAlchemy layer → PostgreSQL by changing the engine URL). Single-region deployment;
  region is stored per tenant but no regional routing is implemented.
* Dashboard uses polling (2–10 s, paused when the tab is hidden), not WebSockets.
* Platform API metrics are measured in-process since the SaaS process started (not persisted).
* Not built: user management/RBAC administration UI, alert-preference/notification channels, billing, regional
  routing, ML models (the AIOps engine is explicitly statistical + rule-based).
