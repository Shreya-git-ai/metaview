"""Background outbox sender and collector heartbeat worker."""

from __future__ import annotations

import random
import logging
import json
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
        self._session: requests.Session | None = None
        self._failure_backoff_s = self.backoff_base_s
        self._offline_window_active = False

    def _get_session(self) -> requests.Session:
        """Create one keep-alive session lazily in the thread that sends."""
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _verify_setting(self) -> bool | str:
        """Choose an explicit CA bundle when configured, otherwise TLS boolean."""
        return config.CA_CERT if config.CA_CERT else config.VERIFY_TLS

    def _endpoint(self) -> str:
        """Use configured ingest URL as supplied, adding /ingest only for a base URL."""
        return self.ingest_url if self.ingest_url.rstrip("/").endswith("/ingest") else self.ingest_url.rstrip("/") + "/ingest"

    def _log(self, status: str, payload: dict[str, Any]) -> None:
        """Append a status entry without including request or raw-stream content."""
        append_entry(status, payload, path=self.egress_path)

    def _backoff(self) -> float:
        """Return this failure's delay, then grow the next delay to its cap."""
        base = min(self.backoff_max_s, self._failure_backoff_s)
        delay = min(self.backoff_max_s, base * (1 + self._rng.uniform(0, max(0.0, self.jitter_fraction))))
        self._failure_backoff_s = min(self.backoff_max_s, max(self.backoff_base_s, base * 2))
        return delay

    def send_one(self, row: dict[str, Any]) -> tuple[str, float]:
        """Attempt one payload; return result and retry delay when applicable."""
        payload_id = row["payload_id"]
        body = row["body"]
        self.store.mark_attempt(payload_id)
        self._log("sent", body)
        try:
            response = self._get_session().post(
                self._endpoint(),
                json=body,
                headers={"X-API-Key": config.API_KEY},
                timeout=(3.0, 5.0),
                verify=self._verify_setting(),
            )
        except requests.RequestException:
            self._log("retried", body)
            return "retry", self._backoff()

        status_code = int(response.status_code)
        if status_code == 200:
            try:
                response_status = response.json().get("status")
            except (ValueError, AttributeError):
                response_status = None
            if response_status in {"accepted", "duplicate"}:
                self.store.mark_acked(payload_id)
                self._log("acked", body)
                self._failure_backoff_s = self.backoff_base_s
                return "acked", 0.0
            self._log("retried", body)
            return "retry", self._backoff()
        if status_code == 429 or status_code >= 500:
            self._log("retried", body)
            return "retry", self._backoff()
        if 400 <= status_code < 500:
            self.store.mark_rejected(payload_id)
            self._log("rejected", body)
            if status_code in {401, 403}:
                self._auth_failed = True
                _LOGGER.error("Ingest authentication failed; sender paused and queued data retained")
            return "rejected", 0.0
        self._log("retried", body)
        return "retry", self._backoff()

    def _heartbeat_loop(self) -> None:
        """Send heartbeats independently so a slow endpoint cannot stall draining."""
        session = requests.Session()
        due = time.monotonic() + max(self.heartbeat_s, 0.05)
        try:
            while not self.stop_event.is_set():
                remaining = due - time.monotonic()
                if remaining > 0:
                    self.stop_event.wait(min(remaining, 0.1))
                    continue
                if not self.offline_flag.exists():
                    self._send_heartbeat(session)
                due = time.monotonic() + max(self.heartbeat_s, 0.05)
        finally:
            session.close()

    def _record_offline_window(self) -> None:
        """Record a local marker when the demo offline flag becomes active."""
        log_path = self.offline_flag.parent / "offline_windows.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": "force_offline_started",
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def _send_heartbeat(self, session: requests.Session) -> bool:
        """Send a heartbeat with short timeouts using the heartbeat worker session."""
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
            response = session.post(
                self.heartbeat_url,
                json=payload,
                headers={"X-API-Key": config.API_KEY},
                timeout=(0.5, 1.0),
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

    def send_heartbeat(self) -> bool:
        """Send the current queue depth and basic collector heartbeat metadata."""
        return self._send_heartbeat(self._get_session())

    def run(self) -> None:
        """Drain pending rows and heartbeat until the stop event is set."""
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="collector-heartbeat", daemon=True)
        heartbeat_thread.start()
        try:
            while not self.stop_event.is_set():
                if self.offline_flag.exists():
                    if not self._offline_window_active:
                        self._record_offline_window()
                        self._offline_window_active = True
                    self.stop_event.wait(config.SENDER_IDLE_S)
                    continue
                self._offline_window_active = False
                if self._auth_failed:
                    self.stop_event.wait(config.SENDER_IDLE_S)
                    continue
                rows = self.store.next_batch(50)
                if not rows:
                    self.stop_event.wait(config.SENDER_IDLE_S)
                    continue
                for row in rows:
                    if self.stop_event.is_set() or self.offline_flag.exists() or self._auth_failed:
                        break
                    outcome, delay = self.send_one(row)
                    if outcome == "retry":
                        self.stop_event.wait(delay)
                        break
        finally:
            heartbeat_thread.join(timeout=1.5)
            if self._session is not None:
                self._session.close()


def sender_loop(store: QueueStore, stop_event: threading.Event, **kwargs: Any) -> None:
    """Run a sender worker from a standard threading.Thread target."""
    Sender(store, stop_event, **kwargs).run()
