"""Authenticated TLS transport from the local collector to the central SaaS.

* Refuses any non-https URL (no plaintext fallback).
* Verifies the server certificate against the configured CA (no verify=False anywhere).
* Enrolls once with a tenant enrollment token → receives a per-collector credential, stored locally.
* Sends the EXACT canonical bytes recorded in the egress audit.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
from pathlib import Path

import httpx

from .config import CollectorConfig, PlaintextRefused

log = logging.getLogger("collector.transport")


class TransportUnavailable(Exception):
    """SaaS unreachable (network error, TLS failure, 5xx, or simulated partition)."""


class AuthRejected(Exception):
    pass


class SaaSTransport:
    def __init__(self, cfg: CollectorConfig) -> None:
        if not cfg.saas_url.startswith("https://"):
            raise PlaintextRefused("Plaintext transport refused")
        self.cfg = cfg
        self.ssl_ctx = ssl.create_default_context(cafile=str(cfg.ca_cert))
        self.ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.client = httpx.Client(base_url=cfg.saas_url, verify=self.ssl_ctx, timeout=httpx.Timeout(5.0))
        self.cred_file = Path(cfg.data_dir) / "credential.json"
        self.token: str | None = None
        self.partitioned = False            # set by the SIMULATED saas_outage scenario
        self.lock = threading.Lock()
        self.last_tls: dict = {}
        self._load_credential()

    # ------------------------------------------------------------- credential
    def _load_credential(self) -> None:
        if self.cred_file.exists():
            data = json.loads(self.cred_file.read_text())
            if data.get("collector_id") == self.cfg.collector_id:
                self.token = data["collector_token"]

    def _save_credential(self, token: str) -> None:
        self.cred_file.parent.mkdir(parents=True, exist_ok=True)
        self.cred_file.write_text(json.dumps({"collector_id": self.cfg.collector_id, "collector_token": token}))
        self.token = token

    @property
    def registered(self) -> bool:
        return self.token is not None

    # ------------------------------------------------------------- low level
    def _request(self, method: str, path: str, *, content: str | None = None, json_body=None,
                 auth: bool = True) -> httpx.Response:
        if self.partitioned:
            raise TransportUnavailable("simulated network partition: SaaS unreachable")
        headers = {"User-Agent": f"nexora-collector/{self.cfg.version}"}
        if auth:
            if not self.token:
                raise AuthRejected("collector not registered")
            headers["Authorization"] = f"Bearer {self.token}"
        if content is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self.client.request(method, path, content=content, json=json_body, headers=headers)
        except httpx.TransportError as e:
            raise TransportUnavailable(f"{type(e).__name__}: {e}") from e
        if resp.status_code >= 500:
            raise TransportUnavailable(f"SaaS returned HTTP {resp.status_code}")
        if resp.status_code == 401:
            raise AuthRejected(resp.text[:200])
        return resp

    def ensure_registered(self) -> None:
        with self.lock:
            if self.token:
                return
            resp = self._request("POST", "/api/v1/collectors/register", auth=False, json_body={
                "tenant_id": self.cfg.tenant_id, "collector_id": self.cfg.collector_id,
                "enrollment_token": self.cfg.enrollment_token, "version": self.cfg.version})
            if resp.status_code != 200:
                raise AuthRejected(f"registration rejected: HTTP {resp.status_code} {resp.text[:200]}")
            self._save_credential(resp.json()["collector_token"])
            log.info("registered with SaaS as %s", self.cfg.collector_id)

    def authenticate(self) -> dict:
        self.ensure_registered()
        resp = self._request("POST", "/api/v1/collectors/auth")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------- operations
    def ingest(self, canonical_payloads: list[str]) -> tuple[int, dict]:
        """POST the exact canonical payload bytes. Returns (http_status, response_json)."""
        self.ensure_registered()
        body = '{"events":[' + ",".join(canonical_payloads) + "]}"
        resp = self._request("POST", "/api/v1/ingest", content=body)
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text[:200]}
        return resp.status_code, data

    def heartbeat(self, canonical: str) -> int:
        self.ensure_registered()
        return self._request("POST", "/api/v1/collectors/heartbeat", content=canonical).status_code

    def received(self, event_id: str) -> dict:
        self.ensure_registered()
        resp = self._request("GET", f"/api/v1/collectors/me/received/{event_id}")
        return {"http_status": resp.status_code, **(resp.json() if resp.content else {})}

    def reconcile(self, event_ids: list[str]) -> dict:
        self.ensure_registered()
        resp = self._request("POST", "/api/v1/collectors/me/reconcile", json_body={"event_ids": event_ids})
        resp.raise_for_status()
        return resp.json()
