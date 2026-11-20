# Local telemetry collector

## Probes

`probes.py` appends numeric service telemetry to `data/stream.jsonl`. Set `PROBE_MODE=mock` (default) for simulated healthy data, or `PROBE_MODE=real` to query the banking app, payment service, and PostgreSQL. `PROBE_INTERVAL_S` controls the sampling period (default 5 seconds); `PROBE_TIMEOUT_S` sets HTTP and database connection timeouts.

Mock scenarios append records for the detectors:

```powershell
..\venv\Scripts\python.exe probes.py scenario db_down
..\venv\Scripts\python.exe probes.py scenario db_up
..\venv\Scripts\python.exe probes.py scenario app_down
..\venv\Scripts\python.exe probes.py scenario conn_flood
..\venv\Scripts\python.exe probes.py scenario error_spike
```

For real mode, configure these environment variables before starting `probes.py`:

- `BANKING_HEALTH_URL`, `PAYMENT_HEALTH_URL`: service health endpoints. Each should return JSON containing `status`, `req_count`, `err_count`, and `avg_resp_ms`.
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE`: PostgreSQL read-only probe connection settings.
- `PROBE_TIMEOUT_S`: timeout for probe connections; `PROBE_INTERVAL_S`: interval between probe batches.

## Four-terminal local demo

Open four PowerShell terminals in `collector/` and run:

1. Mock service telemetry: `$env:PROBE_MODE='mock'; ..\venv\Scripts\python.exe probes.py`
2. Dummy ingest: `..\venv\Scripts\python.exe tools\dummy_ingest.py`
3. Collector: `$env:INGEST_URL='http://127.0.0.1:8000/ingest'; $env:HEARTBEAT_URL='http://127.0.0.1:8000/heartbeat'; ..\venv\Scripts\python.exe collector.py`
4. Egress panel: `..\venv\Scripts\python.exe egress_panel.py`

Open the panel at [http://127.0.0.1:5000](http://127.0.0.1:5000). To inject a scenario, stop the periodic probes in terminal 1, run for example `..\venv\Scripts\python.exe probes.py scenario db_down`, then restart periodic probes.

To simulate an ingest outage, create `data\FORCE_OFFLINE_FLAG` while the collector runs, then remove it to resume delivery. This is the default `FORCE_OFFLINE_FLAG` path; the environment variable can override it.

Use `127.0.0.1`, not `localhost`, for local ingest and panel URLs. On Windows, `localhost` may try IPv6 first and add about 4.5 seconds per request before falling back. The collector defaults to `http://127.0.0.1:8000/ingest` and `http://127.0.0.1:8000/heartbeat`; set `INGEST_URL` and `HEARTBEAT_URL` to override them.

`tools\run_demo.ps1` contains the same four-terminal commands in its comments.
