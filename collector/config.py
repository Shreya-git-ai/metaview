"""Environment-backed configuration for the local telemetry collector."""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    """Read an integer setting, falling back cleanly for invalid values."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    """Read a float setting, falling back cleanly for invalid values."""
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


INGEST_URL: str = os.getenv("INGEST_URL", "http://127.0.0.1:8000/ingest")
HEARTBEAT_URL: str = os.getenv("HEARTBEAT_URL", "http://127.0.0.1:8000/heartbeat")
API_KEY: str = os.getenv("API_KEY", "dev-key-change-me")
TENANT_ID: str = os.getenv("TENANT_ID", "city-hospital")
COLLECTOR_ID: str = os.getenv("COLLECTOR_ID", "col-01")
COLLECTOR_VERSION: str = os.getenv("COLLECTOR_VERSION", "0.1.0")
SOURCE_SALT: str = os.getenv("SOURCE_SALT", "change-me")
SOURCE_TZ: str = os.getenv("SOURCE_TZ", "Asia/Kolkata")
STREAM_PATH: str = os.getenv("STREAM_PATH", "data/stream.jsonl")
SEND_TIMEOUT_S: float = _float_env("SEND_TIMEOUT_S", 3.0)
SENDER_IDLE_S: float = _float_env("SENDER_IDLE_S", 0.2)
HEARTBEAT_INTERVAL_S: float = _float_env("HEARTBEAT_INTERVAL_S", 10.0)
HEARTBEAT_S: float = _float_env("HEARTBEAT_S", HEARTBEAT_INTERVAL_S)
BACKOFF_MAX_S: float = _float_env("BACKOFF_MAX_S", 10.0)
BACKOFF_BASE_S: float = _float_env("BACKOFF_BASE_S", 1.0)
BACKOFF_JITTER: float = _float_env("BACKOFF_JITTER", 0.2)
POLL_INTERVAL_S: float = _float_env("POLL_INTERVAL_S", 0.5)
VERIFY_TLS: bool = os.getenv("VERIFY_TLS", "true").strip().lower() not in {"0", "false", "no", "off"}
CA_CERT: str | None = os.getenv("CA_CERT") or None
PROBE_INTERVAL_S: float = _float_env("PROBE_INTERVAL_S", 5.0)
PROBE_MODE: str = os.getenv("PROBE_MODE", "mock").strip().lower()
PROBE_TIMEOUT_S: float = _float_env("PROBE_TIMEOUT_S", 3.0)
BANKING_HEALTH_URL: str = os.getenv("BANKING_HEALTH_URL", "http://banking-app:8000/health")
PAYMENT_HEALTH_URL: str = os.getenv("PAYMENT_HEALTH_URL", "http://payment-service:8000/health")
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT: int = _int_env("POSTGRES_PORT", 5432)
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "postgres")
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "collector_readonly")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_SSLMODE: str = os.getenv("POSTGRES_SSLMODE", "prefer")
FAILED_LOGIN_THRESHOLD: int = _int_env("FAILED_LOGIN_THRESHOLD", 5)
ANOMALY_Z: float = _float_env("ANOMALY_Z", 3.0)
BASELINE_MIN_SAMPLES: int = _int_env("BASELINE_MIN_SAMPLES", 20)
FORCE_OFFLINE_FLAG: str = os.getenv("FORCE_OFFLINE_FLAG", "data/FORCE_OFFLINE_FLAG")
