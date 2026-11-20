"""Local-first telemetry reader, redactor, detector, and outbox producer."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

import config
from allowlist import apply_allowlist, build_payload
from egress_log import append_entry, write_redaction_audit
from local_detect import LocalDetector, health_hint
from queue_store import QueueStore
from redact import redact_text
from sender import Sender

MALFORMED_JSON_LINES = 0


def _safe_blocked_value(value: Any) -> Any:
    """Recursively redact strings before a blocked candidate is audited locally."""
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, list):
        return [_safe_blocked_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_blocked_value(item) for key, item in value.items()}
    return value


def read_events(
    path: str | Path,
    offset: int = 0,
    on_malformed: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield complete JSON records and byte offsets, retaining partial final lines."""
    global MALFORMED_JSON_LINES
    stream_path = Path(path)
    if not stream_path.exists():
        return
    with stream_path.open("rb") as stream:
        stream.seek(offset)
        while True:
            raw_line = stream.readline()
            if not raw_line or not raw_line.endswith(b"\n"):
                return
            new_offset = stream.tell()
            try:
                decoded = raw_line.decode("utf-8").strip()
                record = json.loads(decoded)
                if not isinstance(record, dict):
                    raise ValueError("record must be an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                MALFORMED_JSON_LINES += 1
                if on_malformed is not None:
                    on_malformed(new_offset)
                continue
            yield record, new_offset


class TelemetryCollector:
    """Process complete stream records into safe payloads and the durable outbox."""

    def __init__(
        self,
        stream_path: str | Path | None = None,
        store: QueueStore | None = None,
        detector: LocalDetector | None = None,
        *,
        egress_path: str | Path | None = None,
        audit_path: str | Path | None = None,
        poll_interval_s: float | None = None,
    ) -> None:
        """Create a collector with injectable store and log paths for testing."""
        self.stream_path = Path(stream_path) if stream_path is not None else Path(config.STREAM_PATH)
        self.store = store or QueueStore()
        self.detector = detector or LocalDetector()
        self.egress_path = Path(egress_path) if egress_path is not None else None
        self.audit_path = Path(audit_path) if audit_path is not None else None
        self.poll_interval_s = config.POLL_INTERVAL_S if poll_interval_s is None else poll_interval_s
        self.malformed_count = 0
        self.blocked_count = 0

    def _malformed(self, offset: int) -> None:
        """Count and persist a byte offset after a malformed complete JSON line."""
        self.malformed_count += 1
        self.store.set_offset(offset)

    def process_record(self, record: dict[str, Any], new_offset: int) -> list[dict[str, Any]]:
        """Build clean payloads and atomically enqueue them with the record offset."""
        host = record.get("host")
        event_time = record.get("ts")
        if not isinstance(host, str) or not isinstance(event_time, str):
            self.store.set_offset(new_offset)
            self.malformed_count += 1
            return []

        raw_line = record.get("log_line")
        raw_line = raw_line if isinstance(raw_line, str) else None
        redacted_line, redactions = redact_text(raw_line) if raw_line is not None else (None, [])

        metrics = record.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else None
        detector_events = self.detector.process(host, metrics, raw_line, event_time)
        descriptors: list[dict[str, Any]] = []
        if metrics is not None:
            descriptors.append(
                {
                    "metrics": metrics,
                    "category": "infra",
                    "health_state": health_hint(metrics),
                }
            )
        for event in detector_events:
            descriptors.append(
                {
                    "event": {key: event[key] for key in ("type", "severity", "count")},
                    "category": event["category"],
                    "alert_context": event["alert_context"],
                    "health_state": health_hint(metrics, [event]),
                }
            )
        if redactions:
            descriptors.append(
                {
                    "event": {"type": "secret_redacted", "severity": "medium", "count": 1},
                    "category": "security",
                    "alert_context": "Sensitive log values were redacted locally",
                    "health_state": health_hint(metrics, [{"type": "secret_redacted", "severity": "medium"}]),
                    "secret_redaction": True,
                }
            )

        accepted: list[dict[str, Any]] = []
        for descriptor in descriptors:
            payload_id = self.store.reserve_payload_id()
            if descriptor.get("secret_redaction") and raw_line is not None and redacted_line is not None:
                write_redaction_audit(payload_id, raw_line, redacted_line, path=self.audit_path)
            candidate = build_payload(
                host,
                event_time,
                descriptor.get("metrics"),
                payload_id=payload_id,
                category=descriptor["category"],
                event=descriptor.get("event"),
                health_state=descriptor.get("health_state"),
                alert_context=descriptor.get("alert_context"),
            )
            clean, dropped = apply_allowlist(candidate)
            serialized = json.dumps(clean, sort_keys=True)
            checked, safety_redactions = redact_text(serialized)
            if checked != serialized:
                self.blocked_count += 1
                safe_blocked = _safe_blocked_value(clean)
                append_entry(
                    "blocked",
                    safe_blocked,
                    dropped,
                    list(dict.fromkeys([*redactions, *safety_redactions])),
                    path=self.egress_path,
                )
                continue
            append_entry("queued", clean, dropped, redactions, path=self.egress_path)
            accepted.append(clean)

        if accepted:
            self.store.enqueue_many_with_offset(accepted, new_offset)
        else:
            self.store.set_offset(new_offset)
        return accepted

    def poll_once(self) -> int:
        """Process complete records currently available and return their count."""
        handled = 0
        for record, new_offset in read_events(self.stream_path, self.store.get_offset(), self._malformed):
            self.process_record(record, new_offset)
            handled += 1
        return handled

    def run(self, stop_event: threading.Event | None = None, start_sender: bool = True) -> None:
        """Poll on the main thread, run sender separately, and shut down cleanly."""
        stopper = stop_event or threading.Event()
        sender_thread: threading.Thread | None = None
        if start_sender:
            sender_thread = threading.Thread(
                target=Sender(self.store, stopper, egress_path=self.egress_path).run,
                name="collector-sender",
                daemon=True,
            )
            sender_thread.start()
        try:
            while not stopper.is_set():
                if self.poll_once() == 0:
                    stopper.wait(self.poll_interval_s)
        finally:
            stopper.set()
            if sender_thread is not None:
                sender_thread.join(timeout=5)


def main() -> None:
    """Run the collector until Ctrl+C requests a clean shutdown."""
    collector = TelemetryCollector()
    stop_event = threading.Event()
    try:
        collector.run(stop_event)
    except KeyboardInterrupt:
        stop_event.set()


if __name__ == "__main__":
    main()
