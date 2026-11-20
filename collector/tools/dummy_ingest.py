"""Tiny in-memory Flask ingest endpoint for local collector integration."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request

import config


def create_app(api_key: str | None = None, received_path: str | Path | None = None) -> Flask:
    """Create a dummy ingest app with isolated state for tests or local runs."""
    app = Flask(__name__)
    expected_key = config.API_KEY if api_key is None else api_key
    output_path = Path(received_path) if received_path is not None else Path(__file__).resolve().parents[1] / "data" / "received.jsonl"
    app.extensions["received_ids"] = set()
    app.extensions["received_payloads"] = []
    app.extensions["last_heartbeat"] = None
    app.extensions["accepted_count"] = 0
    app.extensions["duplicate_count"] = 0
    app.extensions["state_lock"] = threading.Lock()

    @app.post("/ingest")
    def ingest() -> Any:
        """Accept a new payload ID or report a repeat as a duplicate."""
        if request.headers.get("X-API-Key") != expected_key:
            return jsonify({"error": "unauthorized"}), 401
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("payload_id"), str):
            return jsonify({"error": "payload_id required"}), 400
        payload_id = payload["payload_id"]
        with app.extensions["state_lock"]:
            received_ids: set[str] = app.extensions["received_ids"]
            if payload_id in received_ids:
                app.extensions["duplicate_count"] += 1
                return jsonify({"status": "duplicate"})
            received_ids.add(payload_id)
            received_payloads: list[dict[str, Any]] = app.extensions["received_payloads"]
            received_payloads.append(payload)
            app.extensions["accepted_count"] += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        return jsonify({"status": "accepted"})

    @app.post("/heartbeat")
    def heartbeat() -> Any:
        """Store the latest heartbeat for test visibility."""
        payload = request.get_json(silent=True)
        with app.extensions["state_lock"]:
            app.extensions["last_heartbeat"] = payload
        return jsonify({"status": "ok"})

    @app.get("/api/received")
    def received() -> Any:
        """List IDs accepted during this process lifetime."""
        with app.extensions["state_lock"]:
            return jsonify(sorted(app.extensions["received_ids"]))

    @app.get("/api/stats")
    def stats() -> Any:
        """Return accepted and duplicate request counts."""
        with app.extensions["state_lock"]:
            return jsonify(
                {
                    "accepted": app.extensions["accepted_count"],
                    "duplicates": app.extensions["duplicate_count"],
                }
            )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
