"""Tests for append-only egress hash chain and local raw-line audit."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from egress_log import append_entry, verify_chain, write_redaction_audit


def _payload(number: int) -> dict[str, object]:
    return {"payload_id": f"test-{number:04d}", "event_time_utc": "2026-11-20T10:20:15Z", "category": "infra"}


def test_chain_valid_after_many_appends() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "egress.jsonl"
        for number in range(100):
            append_entry("queued", _payload(number), path=path)
        assert verify_chain(path)


def test_chain_invalid_after_editing_one_line() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "egress.jsonl"
        for number in range(4):
            append_entry("queued", _payload(number), path=path)
        lines = path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["payload"]["category"] = "security"
        lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert not verify_chain(path)


def test_concurrent_appends_keep_chain_valid() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "egress.jsonl"
        errors: list[BaseException] = []

        def worker(worker_id: int) -> None:
            try:
                for number in range(25):
                    append_entry("sent", _payload(worker_id * 100 + number), path=path)
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(worker_id,)) for worker_id in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 100
        assert [entry["seq"] for entry in entries] == list(range(1, 101))
        assert verify_chain(path)


def test_restart_resumes_sequence_and_hash_chain() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "egress.jsonl"
        append_entry("queued", _payload(1), path=path)
        append_entry("sent", _payload(1), path=path)
        restarted_entry = append_entry("acked", _payload(1), path=path)
        assert restarted_entry["seq"] == 3
        assert verify_chain(path)


def test_raw_secret_is_written_only_to_local_audit() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        egress = root / "egress.jsonl"
        audit = root / "redaction_audit.jsonl"
        raw = "password=do-not-egress"
        write_redaction_audit("test-1", raw, "password=[REDACTED]", path=audit)
        append_entry("queued", _payload(1), path=egress)
        assert raw in audit.read_text(encoding="utf-8")
        assert raw not in egress.read_text(encoding="utf-8")
