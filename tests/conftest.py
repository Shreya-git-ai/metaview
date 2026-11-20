from __future__ import annotations

import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from saas_backend.config import Settings  # noqa: E402
from saas_backend.main import create_app  # noqa: E402
from scripts.gen_certs import generate  # noqa: E402
from shared.contract import iso  # noqa: E402

ENROLL = {"tenant-alpha": "enr_test_alpha_SYNTHETIC_0001", "tenant-beta": "enr_test_beta_SYNTHETIC_0002"}
PASSWORD = "Test#Pass-SYNTHETIC"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=tmp_path / "saas.db", jwt_secret="test-secret-SYNTHETIC-0123456789abcdef",
                    enrollment_tokens=ENROLL, demo_password=PASSWORD, run_background=False,
                    heartbeat_timeout_s=30, stale_after_s=15)


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def register(client, tenant="tenant-alpha", collector="col-alpha-01") -> str:
    r = client.post("/api/v1/collectors/register", json={"tenant_id": tenant, "collector_id": collector,
                                                          "enrollment_token": ENROLL[tenant], "version": "1.0.0"})
    assert r.status_code == 200, r.text
    return r.json()["collector_token"]


def login(client, user="alpha.admin") -> dict:
    r = client.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def payload(tenant="tenant-alpha", collector="col-alpha-01", source="src-aaaaaaaaaaaa", t: datetime | None = None,
            **metrics) -> dict:
    t = t or datetime.now(timezone.utc)
    m = {"cpu_pct": 40.0, "mem_pct": 55.0, "disk_pct": 50.0, "latency_ms": 20.0, "packet_loss_pct": 0.05,
         "app_response_ms": 150.0, "app_up": True, "auth_failures": 0}
    m.update(metrics)
    return {"schema_version": "1.0", "event_id": str(uuid.uuid4()), "tenant_id": tenant, "collector_id": collector,
            "source_id": source, "source_role": "app", "category": "metrics", "window_start": iso(t - timedelta(seconds=3)),
            "event_time": iso(t), "metrics": m, "health_state": "excellent", "severity": "info"}


def send(client, token, events):
    import json
    body = '{"events":[' + ",".join(json.dumps(e, sort_keys=True, separators=(",", ":")) for e in events) + "]}"
    return client.post("/api/v1/ingest", content=body, headers={"Authorization": f"Bearer {token}",
                                                               "Content-Type": "application/json"})


# ---------------------------------------------------------------- real TLS server
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TLSServer:
    def __init__(self, app, certs: dict, port: int):
        import uvicorn
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error",
                                                    ssl_certfile=certs["server_cert"], ssl_keyfile=certs["server_key"]))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def certs(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("certs"), force=True)


@pytest.fixture
def tls_saas(settings, certs):
    port = free_port()
    srv = TLSServer(create_app(settings), certs, port).start()
    yield {"url": f"https://localhost:{port}", "port": port, "settings": settings}
    srv.stop()


@pytest.fixture
def collector_factory(tmp_path, certs):
    from local_collector.collector import Collector
    from local_collector.config import CollectorConfig

    def make(url, tenant="tenant-alpha", cid="col-alpha-01", ca=None):
        cfg = CollectorConfig(tenant_id=tenant, collector_id=cid, saas_url=url, ca_cert=Path(ca or certs["ca_cert"]),
                              enrollment_token=ENROLL[tenant], data_dir=tmp_path / cid, console_port=0,
                              hosts=[("web-01.test.internal", "web"), ("app-01.test.internal", "app")],
                              source_salt="salt-SYNTHETIC", window_s=1.0)
        cfg.validate()
        return Collector(cfg, seed=42)
    return make
