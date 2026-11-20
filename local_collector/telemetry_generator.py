"""Synthetic telemetry generator.

Produces RAW records exactly as an agent on a customer host might see them — including fields
that must never leave the environment (hostnames, internal IPs, process command lines, raw log
lines with user names, and — in the injection scenario — synthetic secrets).

All values are synthetic. All "secrets" contain the marker SYNTH so they are obviously fake.
"""
from __future__ import annotations

import math
import random
import string
import time
from dataclasses import dataclass, field

from shared.contract import iso, utc_now

from .scenarios import ScenarioManager

ROLE_BASE = {
    #        cpu  mem  disk  net_mbps latency loss  app_ms
    "web": (38, 55, 48, 220, 18, 0.05, 140),
    "app": (45, 62, 52, 140, 22, 0.05, 180),
    "db": (40, 68, 66, 90, 14, 0.03, 90),
    "api": (42, 58, 44, 180, 20, 0.05, 160),
    "network": (20, 40, 30, 600, 10, 0.05, 50),
    "host": (35, 55, 50, 100, 20, 0.05, 150),
}
ACCOUNTS = ["root", "admin", "oracle", "postgres", "ubuntu", "test", "user042", "svc-backup", "deploy", "git"]


def _rand_token(rng: random.Random, n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


@dataclass
class SimHost:
    hostname: str
    role: str
    rng: random.Random
    ip: str = ""
    phase: float = 0.0
    noise: dict = field(default_factory=dict)
    disk: float = 0.0

    def __post_init__(self) -> None:
        self.ip = f"10.{self.rng.randint(0, 40)}.{self.rng.randint(0, 255)}.{self.rng.randint(2, 254)}"
        self.phase = self.rng.uniform(0, 2 * math.pi)
        self.noise = {k: 0.0 for k in ("cpu", "mem", "net", "lat", "loss", "app")}
        self.disk = ROLE_BASE.get(self.role, ROLE_BASE["host"])[2]

    def _ar(self, key: str, sigma: float, phi: float = 0.8) -> float:
        self.noise[key] = phi * self.noise[key] + self.rng.gauss(0, sigma)
        return self.noise[key]


def _blend(normal: float, target: float, k: float) -> float:
    return normal + k * (target - normal)


class TelemetryGenerator:
    def __init__(self, hosts: list[tuple[str, str]], scenarios: ScenarioManager, seed: int | None = None,
                 include_local_host: bool = False) -> None:
        self.rng = random.Random(seed)
        self.scenarios = scenarios
        self.hosts = [SimHost(h, r, random.Random(self.rng.random())) for h, r in hosts]
        self.include_local_host = include_local_host
        self.injected_secrets: list[str] = []   # local-only record of synthetic canaries (for tests/demo)
        self._t0 = time.time()

    @property
    def hostnames(self) -> list[str]:
        names = [h.hostname for h in self.hosts]
        if self.include_local_host:
            names.append(_local_hostname())
        return names

    # ------------------------------------------------------------------ sampling
    def sample(self) -> list[dict]:
        """One sampling tick → list of raw records (metric records + raw log lines)."""
        now = utc_now()
        ts = iso(now)
        t = time.time() - self._t0
        out: list[dict] = []
        for h in self.hosts:
            out.append(self._metric_record(h, t, ts))
            out.extend(self._log_records(h, ts))
        if self.include_local_host:
            rec = _real_host_record(ts)
            if rec:
                out.append(rec)
        return out

    def _metric_record(self, h: SimHost, t: float, ts: str) -> dict:
        cpu_b, mem_b, _, net_b, lat_b, loss_b, app_b = ROLE_BASE.get(h.role, ROLE_BASE["host"])
        sc = self.scenarios
        cpu = cpu_b + 6 * math.sin(2 * math.pi * t / 300 + h.phase) + h._ar("cpu", 2.0)
        mem = mem_b + 2.5 * math.sin(2 * math.pi * t / 600 + h.phase) + h._ar("mem", 0.6, 0.9)
        h.disk = min(99.0, h.disk + h.rng.uniform(0, 0.0015))
        net = net_b * (1 + 0.2 * math.sin(2 * math.pi * t / 240 + h.phase)) + h._ar("net", net_b * 0.04)
        lat = lat_b + abs(h._ar("lat", 2.5, 0.6))
        loss = max(0.0, loss_b + h._ar("loss", 0.03, 0.5))
        app = app_b + 0.9 * max(0.0, cpu - cpu_b) + abs(h._ar("app", 12, 0.6))

        k = sc.intensity("cpu_spike", h.hostname)
        cpu = _blend(cpu, 95 + h.rng.uniform(-1.5, 1.5), k)
        k = sc.intensity("memory_spike", h.hostname)
        mem = _blend(mem, 92 + h.rng.uniform(-1, 1), k)
        k = sc.intensity("correlated_incident", h.hostname)
        if k:
            cpu = _blend(cpu, 95 + h.rng.uniform(-1.5, 1.5), k)
            mem = _blend(mem, 91 + h.rng.uniform(-1, 1), k)
            app = _blend(app, 4000 + h.rng.uniform(-400, 400), k)
        k = sc.intensity("latency", h.hostname)
        lat = _blend(lat, 300 + h.rng.uniform(-40, 40), k)
        k = sc.intensity("packet_loss", h.hostname)
        if k:
            loss = _blend(loss, 6 + h.rng.uniform(-1.5, 1.5), k)
            lat = _blend(lat, 90 + h.rng.uniform(-15, 15), k)
            app = _blend(app, app * 1.6, k)

        secret_k = sc.intensity("secret_injection", h.hostname)
        cmd = f"/opt/{h.role}/bin/{h.role}-server --port 8080 --threads 32"
        if secret_k:
            canary = f"SYNTH-Pw-{_rand_token(h.rng, 8)}"
            self.injected_secrets.append(canary)
            cmd += f" --db-password={canary}"
        return {
            "kind": "metric", "ts": ts, "hostname": h.hostname, "ip": h.ip,
            "cpu_pct": round(min(100.0, max(0.0, cpu)), 2),
            "mem_pct": round(min(100.0, max(0.0, mem)), 2),
            "disk_pct": round(h.disk, 2),
            "net_mbps": round(max(0.0, net), 2),
            "latency_ms": round(max(0.0, lat), 2),
            "packet_loss_pct": round(min(100.0, max(0.0, loss)), 3),
            "app_response_ms": round(max(1.0, app), 1),
            "app_up": True,
            # --- fields below are NOT allow-listed and must be filtered locally ---
            "kernel": "5.15.0-synthetic",
            "top_processes": [{"pid": h.rng.randint(1000, 65000), "cmdline": cmd}],
            "env_snapshot": {"HOME": f"/home/svc-{h.role}", "PATH": "/usr/local/bin:/usr/bin"},
        }

    def _log_records(self, h: SimHost, ts: str) -> list[dict]:
        rng, sc = h.rng, self.scenarios
        logs: list[str | tuple[str, str]] = []
        if rng.random() < 0.35:
            logs.append(("app", f"INFO GET /api/orders/{rng.randint(1000, 9999)} 200 {rng.randint(40, 200)}ms "
                                f"client={_rand_ip(rng)} user=user{rng.randint(1, 99):03d}@customer.example"))
        if rng.random() < 0.05:
            logs.append(("auth", f"Accepted publickey for deploy from {_rand_ip(rng)} port {rng.randint(1024, 65000)} ssh2"))
        if rng.random() < 0.03:  # rare background failure
            logs.append(("auth", f"Failed password for {rng.choice(ACCOUNTS)} from {_rand_ip(rng)} port "
                                 f"{rng.randint(1024, 65000)} ssh2"))
        k = sc.intensity("failed_logins", h.hostname)
        if k:
            attackers = [f"203.0.113.{rng.randint(2, 254)}" for _ in range(4)]  # TEST-NET-3 (documentation range)
            for _ in range(int(k * rng.randint(5, 9))):
                acct = rng.choice(ACCOUNTS)
                inv = "invalid user " if rng.random() < 0.4 else ""
                logs.append(("auth", f"Failed password for {inv}{acct} from {rng.choice(attackers)} port "
                                     f"{rng.randint(1024, 65000)} ssh2"))
        if sc.intensity("secret_injection", h.hostname):
            pw = f"SYNTH!{_rand_token(rng, 10)}"
            key = f"sk_live_SYNTH{_rand_token(rng, 18)}"
            jwt = f"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJTWU5USCJ9.SYNTH{_rand_token(rng, 16)}"
            aws = "AKIASYNTH" + _rand_token(rng, 11, string.ascii_uppercase + string.digits)
            url_pw = f"SYNTH-Url-{_rand_token(rng, 8)}"
            self.injected_secrets += [pw, key, jwt, aws, url_pw]
            logs += [
                ("app", f"WARN login handler: user=user042 password={pw} rejected by policy"),
                ("app", f"ERROR payment-client: retry with api_key={key} failed (timeout)"),
                ("app", f"DEBUG http-client: outbound Authorization: Bearer {jwt}"),
                ("app", f"ERROR backup-job: s3 upload denied for key {aws}"),
                ("app", f"ERROR db-pool: cannot connect postgres://orders_svc:{url_pw}@{h.hostname}:5432/orders"),
            ]
        return [{"kind": "log", "ts": ts, "hostname": h.hostname, "stream": s, "line": line} for s, line in logs]


def _rand_ip(rng: random.Random) -> str:
    return f"10.{rng.randint(0, 40)}.{rng.randint(0, 255)}.{rng.randint(2, 254)}"


def _local_hostname() -> str:
    import socket
    return socket.gethostname()


def _real_host_record(ts: str) -> dict | None:
    """Optional REAL metrics from this machine (psutil). Proves the collector can ingest real telemetry."""
    try:
        import psutil
    except ImportError:
        return None
    import os
    disk_root = os.path.abspath(os.sep)
    return {
        "kind": "metric", "ts": ts, "hostname": _local_hostname(), "ip": "127.0.0.1",
        "cpu_pct": float(psutil.cpu_percent(interval=None)),
        "mem_pct": float(psutil.virtual_memory().percent),
        "disk_pct": float(psutil.disk_usage(disk_root).percent),
        "app_up": True, "kernel": "real-host",
    }
