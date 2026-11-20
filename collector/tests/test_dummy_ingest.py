"""Tests for the local dummy Flask ingest service."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dummy_ingest import create_app


def test_accepted_duplicate_and_missing_api_key() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = create_app("test-key", Path(directory) / "received.jsonl")
        app.testing = True
        client = app.test_client()
        payload = {"payload_id": "collector-000001", "category": "infra", "event": {"type": "cpu_anomaly"}, "secret": "must not persist"}
        headers = {"X-API-Key": "test-key"}
        first = client.post("/ingest", json=payload, headers=headers)
        duplicate = client.post("/ingest", json=payload, headers=headers)
        unauthorized = client.post("/ingest", json={"payload_id": "unauthorized"})
        assert first.status_code == 200
        assert first.get_json() == {"status": "accepted"}
        assert duplicate.get_json() == {"status": "duplicate"}
        assert unauthorized.status_code == 401
        assert client.get("/api/received").get_json() == ["collector-000001"]
        assert client.get("/api/stats").get_json() == {"accepted": 1, "duplicates": 1}
        lines = (Path(directory) / "received.jsonl").read_text(encoding="utf-8").splitlines()
        accepted, duplicate_record = [json.loads(line) for line in lines]
        assert accepted["payload_id"] == "collector-000001"
        assert accepted["event_type"] == "cpu_anomaly"
        assert isinstance(accepted["received_at"], float)
        assert "duplicate" not in accepted
        assert duplicate_record["duplicate"] is True
        assert duplicate_record["event_type"] == "cpu_anomaly"
        assert "secret" not in lines[0] and "category" not in lines[0]
