"""Background outbox sender and collector heartbeat worker."""

from __future__ import annotations

import random
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import config
from egress_log import append_entry
from queue_store import QueueStore

_LOGGER = logging.getLogger(__name__)


class Sender:
    """Drain a QueueStore and send heartbeats using its own DB operations."""

    def __init__(
        self,
        store: QueueStore,
        stop_event: threading.Event,
        *,
        ingest_url: str | None = None,
        heartbeat_url: str | None = None,
        offline_flag: str | Path | None = None,
        egress_path: str | Path | None = None,
        heartbeat_s: float | None = None,
        backoff_base_s: float | None = None,
        backoff_max_s: float | None = None,
        jitter_fraction: float | None = None,
    ) -> None:
        """Create a sender with injectable endpoints and local paths for testing."""
        self.store = store
        self.stop_event = stop_event
        self.ingest_url = ingest_url or config.INGEST_URL
        self.heartbeat_url = heartbeat_url or config.HEARTBEAT_URL
        self.offline_flag = Path(offline_flag) if offline_flag is not None else Path(config.FORCE_OFFLINE_FLAG)
        self.egress_path = Path(egress_path) if egress_path is not None else None
        self.heartbeat_s = config.HEARTBEAT_S if heartbeat_s is None else heartbeat_s
        self.backoff_base_s = config.BACKOFF_BASE_S if backoff_base_s is None else backoff_base_s
        self.backoff_max_s = config.BACKOFF_MAX_S if backoff_max_s is None else backoff_max_s
        self.jitter_fraction = config.BACKOFF_JITTER if jitter_fraction is None else jitter_fraction
        self._last_heartbeat = 0.0
        self._rng = random.Random()
        self._auth_failed = False

    def _verify_setting(self) -> bool | str:
        """Choose an explicit CA bundle when configured, otherwise TLS boolean."""
        return config.CA_CERT if config.CA_CERT else config.VERIFY_TLS

    def _endpoint(self) -> str:
        """Use configured ingest URL as supplied, adding /ingest only for a base URL."""
        return self.ingest_url if self.ingest_url.rstrip("/").endswith("/ingest") else self.ingest_url.rstrip("/") + "/ingest"

    def _log(self, status: str, payload: dict[str, Any]) -> None:
        """Append a status entry without including request or raw-stream content."""
        append_entry(status, payload, path=self.egress_path)

    def _backoff(self, attempts_before_send: int) -> float:
        """Return capped exponential delay with bounded random jitter."""
        base = min(self.backoff_max_s, self.backoff_base_s * (2 ** min(attempts_before_send, 30)))
        return min(self.backoff_max_s, base * (1 + self._rng.uniform(0, max(0.0, self.jitter_fraction))))

    def send_one(self, row: dict[str, Any]) -> tuple[str, float]:
        """Attempt one payload; return result and retry delay when applicable."""
        payload_id = row["payload_id"]
        body = row["body"]
        self.store.mark_attempt(payload_id)
        self._log("sent", body)
        try:
            response = requests.post(
                self._endpoint(),
                json=body,
                headers={"X-API-Key": config.API_KEY},
                timeout=config.SEND_TIMEOUT_S,
                verify=self._verify_setting(),
            )
        except requests.RequestException:
            self._log("retried", body)
            return "retry", self._backoff(int(row.get("attempts", 0)))

        status_code = int(response.status_code)
        if status_code == 200:
            try:
                response_status = response.json().get("status")
            except (ValueError, AttributeError):
                response_status = None
            if response_status in {"accepted", "duplicate"}:
                self.store.mark_acked(payload_id)
                self._log("acked", body)
                return "acked", 0.0
            self._log("retried", body)
            return "retry", self._backoff(int(row.get("attempts", 0)))
        if status_code == 429 or status_code >= 500:
            self._log("retried", body)
            return "retry", self._backoff(int(row.get("attempts", 0)))
        if 400 <= status_code < 500:
            self.store.mark_rejected(payload_id)
            self._log("rejected", body)
            if status_code in {401, 403}:
                self._auth_failed = True
                _LOGGER.error("Ingest authentication failed; sender paused and queued data retained")
            return "rejected", 0.0
        self._log("retried", body)
        return "retry", self._backoff(int(row.get("attempts", 0)))

    def send_heartbeat(self) -> bool:
        """Send the current queue depth and basic collector heartbeat metadata."""
        payload = {
            "collector_id": config.COLLECTOR_ID,
            "tenant_id": config.TENANT_ID,
            "version": config.COLLECTOR_VERSION,
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "queue_depth": self.store.pending_count(),
            "cpu_pct": None,
            "mem_pct": None,
            "config_status": "ok",
        }
        try:
            response = requests.post(
                self.heartbeat_url,
                json=payload,
                headers={"X-API-Key": config.API_KEY},
                timeout=config.SEND_TIMEOUT_S,
                verify=self._verify_setting(),
            )
            self._last_heartbeat = time.monotonic()
            succeeded = 200 <= int(response.status_code) < 300
            if not succeeded:
                _LOGGER.warning("Heartbeat request failed with HTTP status %s", int(response.status_code))
            return succeeded
        except requests.RequestException:
            self._last_heartbeat = time.monotonic()
            _LOGGER.warning("Heartbeat request failed")
            return False

    def run(self) -> None:
        """Drain pending rows and heartbeat until the stop event is set."""
        while not self.stop_event.is_set():
            if self.offline_flag.exists():
                self.stop_event.wait(0.2)
                continue
            if self._auth_failed:
                self.stop_event.wait(0.5)
                continue
            if time.monotonic() - self._last_heartbeat >= self.heartbeat_s:
                self.send_heartbeat()
                if self.stop_event.wait(0.01):
                    break
            rows = self.store.next_batch(100)
            if not rows:
                self.stop_event.wait(min(max(self.heartbeat_s / 4, 0.05), 0.5))
                continue
            for row in rows:
                if self.stop_event.is_set() or self.offline_flag.exists():
                    break
                outcome, delay = self.send_one(row)
                if outcome == "retry":
                    self.stop_event.wait(delay)
                    break


def sender_loop(store: QueueStore, stop_event: threading.Event, **kwargs: Any) -> None:
    """Run a sender worker from a standard threading.Thread target."""
    Sender(store, stop_event, **kwargs).run()
