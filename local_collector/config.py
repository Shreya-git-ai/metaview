"""Local collector configuration (environment variables, see config/<collector>.env)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"


class PlaintextRefused(ValueError):
    pass


def _path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


@dataclass
class CollectorConfig:
    tenant_id: str
    collector_id: str
    saas_url: str
    ca_cert: Path
    enrollment_token: str
    data_dir: Path
    console_port: int
    hosts: list[tuple[str, str]]          # (hostname, role) – hostnames never leave the collector
    source_salt: str                      # pseudonymisation key – never leaves the collector
    sample_interval_s: float = 1.0
    window_s: float = 3.0
    heartbeat_s: float = 5.0
    version: str = VERSION
    include_local_host: bool = False      # add the real machine via psutil
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "CollectorConfig":
        e = os.environ
        hosts = []
        for item in e["COLLECTOR_HOSTS"].split(","):
            name, _, role = item.strip().partition(":")
            hosts.append((name, role or "host"))
        cfg = cls(
            tenant_id=e["COLLECTOR_TENANT_ID"],
            collector_id=e["COLLECTOR_ID"],
            saas_url=e["COLLECTOR_SAAS_URL"].rstrip("/"),
            ca_cert=_path(e["COLLECTOR_CA_CERT"]),
            enrollment_token=e["COLLECTOR_ENROLLMENT_TOKEN"],
            data_dir=_path(e["COLLECTOR_DATA_DIR"]),
            console_port=int(e.get("COLLECTOR_CONSOLE_PORT", "9101")),
            hosts=hosts,
            source_salt=e["COLLECTOR_SOURCE_SALT"],
            sample_interval_s=float(e.get("COLLECTOR_SAMPLE_INTERVAL_S", "1")),
            window_s=float(e.get("COLLECTOR_WINDOW_S", "3")),
            heartbeat_s=float(e.get("COLLECTOR_HEARTBEAT_S", "5")),
            include_local_host=e.get("COLLECTOR_INCLUDE_LOCAL_HOST", "false").lower() == "true",
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.saas_url.startswith("https://"):
            raise PlaintextRefused(
                f"COLLECTOR_SAAS_URL must use https:// — plaintext transport is refused ({self.saas_url!r})")
        if not Path(self.ca_cert).exists():
            raise FileNotFoundError(f"CA certificate not found: {self.ca_cert} (run scripts/setup_dev.py)")

    @property
    def config_hash(self) -> str:
        """Hash of non-secret configuration (reported in heartbeat as configuration status)."""
        s = "|".join([self.tenant_id, self.collector_id, self.saas_url, self.version,
                      ",".join(f"{h}:{r}" for h, r in self.hosts), str(self.sample_interval_s), str(self.window_s)])
        return hashlib.sha256(s.encode()).hexdigest()[:12]
