"""Live demonstration scenarios. Each one changes what the telemetry generator / collector
pipeline actually does — nothing here touches the dashboard directly."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

RAMP_S = 5.0

CATALOG = {
    "normal": {"label": "Normal operation", "scope": "all", "duration": 0,
               "description": "Clear every active scenario; telemetry returns to baseline behaviour."},
    "cpu_spike": {"label": "CPU spike", "scope": "host", "duration": 90,
                  "description": "Drives CPU on one host to ~95%."},
    "memory_spike": {"label": "Memory spike", "scope": "host", "duration": 90,
                     "description": "Drives memory on one host to ~92%."},
    "latency": {"label": "Network latency increase", "scope": "all", "duration": 90,
                "description": "Raises network latency to ~300 ms on every host of this collector."},
    "packet_loss": {"label": "Packet loss", "scope": "all", "duration": 90,
                    "description": "Raises packet loss to ~6% (and latency moderately) on every host."},
    "failed_logins": {"label": "Repeated failed logins", "scope": "host", "duration": 60,
                      "description": "Generates bursts of failed SSH logins (synthetic accounts and IPs) in the raw auth log."},
    "secret_injection": {"label": "Sensitive log injection", "scope": "host", "duration": 15,
                         "description": "Injects synthetic passwords, API keys, bearer tokens and AWS-style keys into raw logs, "
                                        "and simulates a misconfigured plugin trying to attach a raw log line to the payload."},
    "correlated_incident": {"label": "Multiple related events", "scope": "host", "duration": 90,
                            "description": "CPU, memory and application response time degrade together on one host."},
    "collector_offline": {"label": "Collector offline", "scope": "collector", "duration": 75,
                          "description": "SIMULATED process hang: the collector stops sampling, sending and heart-beating."},
    "saas_outage": {"label": "SaaS connectivity failure", "scope": "collector", "duration": 60,
                    "description": "SIMULATED network partition at the collector egress: collection continues, approved "
                                   "metadata is buffered in SQLite and retried with exponential backoff."},
    "duplicate_replay": {"label": "Duplicate replay", "scope": "collector", "duration": 0,
                         "description": "Re-sends the last 20 already-delivered payloads byte-for-byte to prove idempotent ingestion."},
}


@dataclass
class Active:
    name: str
    target: str | None
    started: float
    until: float


class ScenarioManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, Active] = {}
        self.history: list[dict] = []

    def trigger(self, name: str, target: str | None = None, duration: float | None = None) -> Active | None:
        if name not in CATALOG:
            raise KeyError(name)
        now = time.time()
        with self._lock:
            self.history.append({"name": name, "target": target, "at": now})
            self.history = self.history[-50:]
            if name == "normal":
                self._active.clear()
                return None
            dur = duration if duration is not None else CATALOG[name]["duration"]
            act = Active(name, target, now, now + dur)
            self._active[name] = act
            return act

    def _prune(self) -> None:
        now = time.time()
        for k in [k for k, a in self._active.items() if a.until <= now]:
            del self._active[k]

    def active(self) -> list[Active]:
        with self._lock:
            self._prune()
            return list(self._active.values())

    def get(self, name: str) -> Active | None:
        with self._lock:
            self._prune()
            return self._active.get(name)

    def intensity(self, name: str, host: str) -> float:
        """0..1 ramped intensity of a scenario for a given host."""
        a = self.get(name)
        if not a or (a.target and a.target != host):
            return 0.0
        now = time.time()
        up = min(1.0, (now - a.started) / RAMP_S)
        down = min(1.0, max(0.0, (a.until - now) / RAMP_S))
        return max(0.0, min(up, down))
