"""Local processing: FILTER → REDACT → AGGREGATE → ANALYZE → build candidate payloads.

Everything here runs inside the customer environment. The output of `build_payloads` is only a
*candidate*; it still has to pass the policy engine before it may leave.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from shared.contract import SCHEMA_VERSION, iso
from shared.health import DEFAULT_THRESHOLDS, classify, severity_for

from .redaction import Redactor

# Fields of a raw metric record that may be used for aggregation. Everything else is dropped here.
RAW_METRIC_ALLOW = {"cpu_pct", "mem_pct", "disk_pct", "net_mbps", "latency_ms", "packet_loss_pct",
                    "app_response_ms", "app_up"}
_AUTH_FAIL_RE = re.compile(r"Failed password for (?:invalid user )?(?P<acct>\S+) from (?P<src>\S+)")
CONTEXT_FOR_METRIC = {
    "cpu_pct": "HIGH_CPU", "mem_pct": "HIGH_MEMORY", "disk_pct": "HIGH_DISK", "latency_ms": "HIGH_LATENCY",
    "packet_loss_pct": "PACKET_LOSS", "app_response_ms": "SLOW_APP_RESPONSE", "app_up": "APP_DOWN",
    "auth_failures": "AUTH_FAILURE_BURST",
}
AUTH_BURST_PER_WINDOW = 6


def source_id_for(hostname: str, salt: str) -> str:
    """Pseudonymous, stable source id. The hostname and salt never leave the collector."""
    return "src-" + hmac.new(salt.encode(), hostname.encode(), hashlib.sha256).hexdigest()[:12]


def filter_metric_record(raw: dict) -> tuple[dict, list[str]]:
    kept = {k: v for k, v in raw.items() if k in RAW_METRIC_ALLOW}
    dropped = sorted(k for k in raw if k not in RAW_METRIC_ALLOW and k not in ("kind", "ts"))
    return kept, dropped


@dataclass
class HostWindow:
    samples: dict = field(default_factory=lambda: defaultdict(list))
    app_up: bool = True
    auth_failures: int = 0
    auth_accounts: set = field(default_factory=set)
    auth_sources: set = field(default_factory=set)
    raw_count: int = 0
    fields_dropped: int = 0
    secrets: int = 0
    identifiers: int = 0
    rules: set = field(default_factory=set)


class LocalAnalyzer:
    """Per-host EWMA baseline used for LOCAL anomaly flagging (SaaS runs its own, richer analysis)."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.alpha = alpha
        self.state: dict[tuple[str, str], tuple[float, float, int]] = {}

    def update(self, host: str, metric: str, value: float) -> float | None:
        key = (host, metric)
        mean, var, n = self.state.get(key, (value, 0.0, 0))
        z = None
        if n >= 10:
            sd = max(math.sqrt(var), 1e-6 + 0.02 * abs(mean) + 0.5)
            z = (value - mean) / sd
        if z is None or abs(z) < 4:  # do not let anomalies poison the local baseline
            diff = value - mean
            mean += self.alpha * diff
            var = (1 - self.alpha) * (var + self.alpha * diff * diff)
        self.state[key] = (mean, var, n + 1)
        return z


class Processor:
    def __init__(self, tenant_id: str, collector_id: str, salt: str, roles: dict[str, str],
                 redactor: Redactor, window_s: float) -> None:
        self.tenant_id = tenant_id
        self.collector_id = collector_id
        self.salt = salt
        self.roles = roles
        self.redactor = redactor
        self.window_s = window_s
        self.analyzer = LocalAnalyzer()
        self.window: dict[str, HostWindow] = defaultdict(HostWindow)
        self.window_canaries: set[str] = set()
        self.stats = defaultdict(int)
        self.last_filter_example: dict | None = None
        self.last_aggregates: list[dict] = []

    def source_id(self, host: str) -> str:
        return source_id_for(host, self.salt)

    # ---------------------------------------------------------------- per raw record
    def ingest_raw(self, rec: dict) -> dict:
        """Filter/redact one raw record into the current window. Returns processing details
        (redacted text, findings, dropped fields) for the local audit trail."""
        host = rec["hostname"]
        w = self.window[host]
        w.raw_count += 1
        self.stats["raw_records"] += 1
        if rec["kind"] == "metric":
            kept, dropped = filter_metric_record(rec)
            self.stats["fields_dropped"] += len(dropped)
            w.fields_dropped += len(dropped)
            for k, v in kept.items():
                if k == "app_up":
                    w.app_up = w.app_up and bool(v)
                else:
                    w.samples[k].append(float(v))
            # also scan non-allow-listed raw text (e.g. process command lines) for canaries
            _, findings, secrets = self.redactor.redact(str(rec.get("top_processes", "")))
            self.window_canaries.update(secrets)
            self._count(w, findings)
            self.last_filter_example = {"hostname_local_only": host, "raw": rec, "filtered": kept,
                                        "dropped_fields": dropped}
            return {"dropped": dropped, "findings": findings, "redacted": None}
        # log line
        line = rec["line"]
        m = _AUTH_FAIL_RE.search(line)
        if m:
            w.auth_failures += 1
            w.auth_accounts.add(m.group("acct"))
            w.auth_sources.add(m.group("src"))
        redacted, findings, secrets = self.redactor.redact(line)
        self.window_canaries.update(secrets)
        n_secret = sum(f["matches"] for f in findings if f["kind"] == "secret")
        self.stats["secrets_redacted"] += n_secret
        self.stats["identifiers_pseudonymised"] += sum(f["matches"] for f in findings if f["kind"] == "identifier")
        self._count(w, findings)
        return {"dropped": [], "findings": findings, "redacted": redacted, "secret_count": n_secret}

    @staticmethod
    def _count(w: "HostWindow", findings: list[dict]) -> None:
        for f in findings:
            w.rules.add(f["rule_id"])
            if f["kind"] == "secret":
                w.secrets += f["matches"]
            else:
                w.identifiers += f["matches"]

    # ---------------------------------------------------------------- per window
    def build_payloads(self, window_start, window_end, window_id: str) -> tuple[list[dict], set[str]]:
        """Aggregate + analyse the closing window and build candidate payloads (one per host,
        plus a security summary on auth bursts). Returns (candidates, canaries)."""
        candidates: list[dict] = []
        aggregates: list[dict] = []
        for host, w in self.window.items():
            if not w.samples and not w.auth_failures:
                continue
            metrics: dict = {}
            for k, vals in w.samples.items():
                metrics[k] = round(sum(vals) / len(vals), 3 if k == "packet_loss_pct" else 2)
            if w.samples:
                metrics["app_up"] = w.app_up
            metrics["auth_failures"] = w.auth_failures
            metrics["auth_distinct_accounts"] = len(w.auth_accounts)
            metrics["auth_distinct_sources"] = len(w.auth_sources)

            state, trigger, reason = classify(metrics, DEFAULT_THRESHOLDS)
            local_z = {}
            for k in ("cpu_pct", "mem_pct", "latency_ms", "packet_loss_pct", "app_response_ms"):
                if k in metrics:
                    z = self.analyzer.update(host, k, metrics[k])
                    if z is not None:
                        local_z[k] = round(z, 2)
            context_code, params = None, {}
            if trigger:
                context_code = CONTEXT_FOR_METRIC.get(trigger)
                v = metrics.get(trigger)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    params = {"value": v}
            elif local_z and max(local_z.values()) >= 4:
                m = max(local_z, key=local_z.get)
                context_code, params = "LOCAL_ANOMALY", {"z": local_z[m]}

            sid = self.source_id(host)
            base = {
                "schema_version": SCHEMA_VERSION,
                "tenant_id": self.tenant_id,
                "collector_id": self.collector_id,
                "source_id": sid,
                "source_role": self.roles.get(host, "host"),
                "window_start": iso(window_start),
                "event_time": iso(window_end),
                "health_state": state,
                "severity": severity_for(state),
            }
            p = dict(base, event_id=str(uuid.uuid4()), category="metrics", metrics=metrics,
                     processing={"raw_records": w.raw_count, "fields_dropped": w.fields_dropped,
                                 "secrets_redacted": w.secrets, "identifiers_pseudonymised": w.identifiers,
                                 "rules_triggered": sorted(w.rules)})
            if context_code:
                p["context_code"] = context_code
                p["context_params"] = params
            candidates.append(p)
            if w.auth_failures >= AUTH_BURST_PER_WINDOW:
                candidates.append(dict(
                    base, event_id=str(uuid.uuid4()), category="security_summary",
                    metrics={"auth_failures": w.auth_failures,
                             "auth_distinct_accounts": len(w.auth_accounts),
                             "auth_distinct_sources": len(w.auth_sources)},
                    severity="critical" if w.auth_failures >= 15 else "warning",
                    context_code="AUTH_FAILURE_BURST",
                    context_params={"count": w.auth_failures, "window_s": int(self.window_s),
                                    "accounts": len(w.auth_accounts), "sources": len(w.auth_sources)},
                ))
            aggregates.append({"window_id": window_id, "hostname_local_only": host, "source_id": sid,
                               "raw_records": w.raw_count, "metrics": metrics, "local_health": state,
                               "local_reason": reason, "local_z_scores": local_z})
        self.last_aggregates = aggregates
        canaries = set(self.window_canaries)
        self.window = defaultdict(HostWindow)
        self.window_canaries = set()
        return candidates, canaries
