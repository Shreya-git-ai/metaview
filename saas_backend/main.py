"""NEXORA central SaaS backend.

    python -m saas_backend.main --env config/saas.env

Serves HTTPS only. Starting without a TLS certificate/key is refused (no plaintext fallback).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from saas_backend.api import (aiops_routes, collector_routes, dashboard_routes, insights_routes,  # noqa: E402
                              privacy_routes)
from saas_backend.config import Settings  # noqa: E402
from saas_backend.database.db import Base, make_engine, make_session_factory, utcnow  # noqa: E402
from saas_backend.services.maintenance import run_maintenance  # noqa: E402
from saas_backend.services.seed import seed  # noqa: E402
from shared.envfile import load_env_file  # noqa: E402

log = logging.getLogger("saas")


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.db_path)
    SessionLocal = make_session_factory(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, settings)

    def maintenance_once() -> dict:
        t0 = time.perf_counter()
        with SessionLocal() as db:
            res = run_maintenance(db, utcnow(), settings.heartbeat_timeout_s, settings.stale_after_s)
        app.state.maintenance_last_run = utcnow()
        app.state.maintenance_last_ms = round((time.perf_counter() - t0) * 1000, 1)
        return res

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.run_background:
            async def loop():
                while True:
                    try:
                        await asyncio.to_thread(maintenance_once)
                    except Exception:
                        log.exception("maintenance loop error")
                    await asyncio.sleep(settings.maintenance_interval_s)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="NEXORA SaaS API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal
    app.state.maintenance_once = maintenance_once
    app.state.started_at = utcnow()
    app.state.api_stats = deque(maxlen=20000)
    app.state.api_total = 0
    app.state.maintenance_last_run = None
    app.state.maintenance_last_ms = None

    @app.middleware("http")
    async def measure(request, call_next):
        """Records API latency/status for the platform-health page (no payloads, no identities)."""
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            parts = path.split("/")
            group = parts[3] if len(parts) > 3 and path.startswith("/api/v1") else "other"
            app.state.api_total += 1
            app.state.api_stats.append({"ts": time.time(), "group": group, "path": path, "status": status,
                                        "ms": (time.perf_counter() - t0) * 1000})
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])
    for r in (collector_routes.router, dashboard_routes.router, aiops_routes.router, privacy_routes.router,
              insights_routes.router):
        app.include_router(r)

    @app.get("/api/v1/status")
    def status():
        return {"service": "nexora-saas", "tls": True, "time": utcnow().isoformat() + "Z"}

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="NEXORA SaaS backend")
    ap.add_argument("--env", default="config/saas.env")
    args = ap.parse_args()
    load_env_file(ROOT / args.env if not Path(args.env).is_absolute() else args.env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not (settings.tls_cert and settings.tls_key and settings.tls_cert.exists() and settings.tls_key.exists()):
        raise SystemExit("TLS certificate/key missing — refusing to start in plaintext. Run: python scripts/setup_dev.py")
    import uvicorn
    log.info("NEXORA SaaS listening on https://%s:%d", settings.host, settings.port)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, ssl_certfile=str(settings.tls_cert),
                ssl_keyfile=str(settings.tls_key), log_level="warning")


if __name__ == "__main__":
    main()
