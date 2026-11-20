"""SaaS backend settings (environment variables, see config/saas.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


@dataclass
class Settings:
    db_path: Path
    jwt_secret: str
    enrollment_tokens: dict[str, str]          # tenant_id -> enrollment token (synthetic)
    demo_password: str
    tls_cert: Path | None = None
    tls_key: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8443
    heartbeat_timeout_s: float = 30.0
    stale_after_s: float = 15.0
    maintenance_interval_s: float = 5.0
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173"])
    run_background: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        tokens = {}
        for item in e.get("SAAS_ENROLLMENT_TOKENS", "").split(","):
            if ":" in item:
                t, _, tok = item.partition(":")
                tokens[t.strip()] = tok.strip()
        return cls(
            db_path=_path(e.get("SAAS_DB_PATH", "data/saas/saas.db")),
            jwt_secret=e["SAAS_JWT_SECRET"],
            enrollment_tokens=tokens,
            demo_password=e.get("SAAS_DEMO_PASSWORD", "Demo#2026"),
            tls_cert=_path(e["SAAS_TLS_CERT"]) if e.get("SAAS_TLS_CERT") else None,
            tls_key=_path(e["SAAS_TLS_KEY"]) if e.get("SAAS_TLS_KEY") else None,
            host=e.get("SAAS_HOST", "127.0.0.1"),
            port=int(e.get("SAAS_PORT", "8443")),
            heartbeat_timeout_s=float(e.get("SAAS_HEARTBEAT_TIMEOUT_S", "30")),
            stale_after_s=float(e.get("SAAS_STALE_AFTER_S", "15")),
            cors_origins=[o.strip() for o in e.get("SAAS_CORS_ORIGINS", "http://localhost:5173").split(",")],
        )


TENANTS = [
    {"id": "tenant-alpha", "name": "Customer Alpha", "region": "eu-west-1"},
    {"id": "tenant-beta", "name": "Customer Beta", "region": "ap-south-1"},
]
DEMO_USERS = [
    {"username": "alpha.admin", "role": "tenant_admin", "tenant_id": "tenant-alpha", "display": "Alpha Admin"},
    {"username": "beta.admin", "role": "tenant_admin", "tenant_id": "tenant-beta", "display": "Beta Admin"},
    {"username": "platform.operator", "role": "platform_operator", "tenant_id": None, "display": "Platform Operator"},
]
