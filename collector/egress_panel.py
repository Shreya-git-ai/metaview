"""Local-only Flask panel proving what the collector sent across its boundary."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config
from egress_log import verify_chain
from queue_store import QueueStore

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Collector Egress Proof</title>
  <style>
    :root { --bg:#0b1220; --panel:#131e30; --line:#26364d; --text:#e6eef9; --muted:#9db0c9; --green:#3ddc97; --red:#ff657a; --amber:#ffd166; --blue:#8ab4ff; }
    * { box-sizing:border-box } body { margin:0; padding:24px; background:var(--bg); color:var(--text); font:14px/1.45 system-ui,Segoe UI,sans-serif }
    h1,h2 { margin:0 0 14px } h1 { font-size:24px } h2 { margin-top:26px; font-size:18px }
    .sub { color:var(--muted); margin-bottom:20px } .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(125px,1fr)); gap:10px }
    .card,.entry,.proof { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:13px }
    .label { color:var(--muted); font-size:12px } .value { font-size:22px; font-weight:700; margin-top:5px }
    .badge { display:inline-block; border-radius:999px; padding:2px 8px; margin:2px 3px 2px 0; background:#223653; color:var(--blue); font-size:12px }
    .ok { color:var(--green) } .bad { color:var(--red) } .warn { color:var(--amber) }
    #payloads,#proofs { display:grid; gap:10px } .entry-head { display:flex; flex-wrap:wrap; align-items:center; gap:10px; color:var(--muted) }
    .entry pre,.proof pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:360px; overflow:auto; color:#d6e2f2; background:#0d1727; border-radius:7px; padding:12px }
    .proof-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px } .raw-label { color:var(--red); font-weight:800; letter-spacing:.04em }
    .chain { font-weight:800; border:1px solid var(--line); padding:5px 10px; border-radius:999px }
    @media(max-width:700px){body{padding:14px}.proof-grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <h1>Collector egress proof</h1>
  <div class="sub">Local collector view · refreshes every 2 seconds · raw lines stay in the local-only proof section</div>
  <div class="cards" id="counters"></div>
  <h2>Hash chain <span id="chain" class="chain">Checking…</span></h2>
  <h2>Latest payloads</h2><div id="payloads"></div>
  <h2>Redaction proof</h2><div id="proofs"></div>
<script>
const esc = (s) => String(s ?? '');
const node = (tag, cls, value) => { const n=document.createElement(tag); if(cls)n.className=cls; if(value!==undefined)n.textContent=value; return n; };
async function load() {
  const [c,p,f,h] = await Promise.all(['/api/counters','/api/payloads','/api/proof','/api/chain'].map(x=>fetch(x).then(r=>r.json())));
  const counters=document.getElementById('counters'); counters.replaceChildren();
  for (const [label,key] of [['Queued','queued'],['Sent','sent'],['Acked','acked'],['Retried','retried'],['Blocked','blocked'],['Rejected','rejected'],['Duplicates','duplicates'],['Queue depth','queue_depth'],['Connection','online_state']]) {
    const card=node('div','card'); card.append(node('div','label',label)); card.append(node('div','value',c[key])); counters.append(card);
  }
  const chain=document.getElementById('chain'); chain.textContent=h.valid?'VALID':'BROKEN'; chain.className='chain '+(h.valid?'ok':'bad');
  const payloads=document.getElementById('payloads'); payloads.replaceChildren();
  for(const item of p.payloads||[]) {
    const card=node('article','entry'), head=node('div','entry-head');
    head.append(node('b','',item.status),node('span','',item.ts_utc||''),node('span','',item.payload?.payload_id||''));
    card.append(head);
    if(item.affected_services?.length){const badges=node('div',''); for(const svc of item.affected_services)badges.append(node('span','badge',svc)); card.append(badges);}
    const audit=node('div','label',`Dropped fields: ${JSON.stringify(item.dropped_fields||[])} · Redactions: ${(item.redactions||[]).join(', ')}`); card.append(audit);
    const pre=node('pre',''); pre.textContent=JSON.stringify(item.payload,null,2); card.append(pre); payloads.append(card);
  }
  const proofs=document.getElementById('proofs'); proofs.replaceChildren();
  for(const item of f.proofs||[]) {
    const card=node('article','proof'), grid=node('div','proof-grid'), left=node('div',''), right=node('div','');
    left.append(node('div','raw-label',item.raw_line_label||'LOCAL ONLY, NEVER SENT'),node('pre','',item.raw_line||''));
    left.append(node('div','label','Redacted result'),node('pre','',item.redacted_line||''));
    right.append(node('div','label','Payload sent'),node('pre','',JSON.stringify(item.sent_payload,null,2)));
    grid.append(left,right); card.append(grid); proofs.append(card);
  }
}
load(); setInterval(load,2000);
</script>
</body></html>"""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load valid JSON objects from a JSONL file, ignoring malformed lines."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                entries.append(value)
    return entries


def _duplicate_count(received: list[dict[str, Any]]) -> int:
    """Count repeated payload IDs in the local received payload log."""
    seen: set[str] = set()
    duplicates = 0
    for entry in received:
        payload_id = entry.get("payload_id")
        if isinstance(payload_id, str):
            if payload_id in seen:
                duplicates += 1
            seen.add(payload_id)
    return duplicates


def create_app(data_dir: str | Path | None = None) -> Flask:
    """Create the local proof panel, with an injectable data directory for tests."""
    root = Path(data_dir) if data_dir is not None else ROOT / "data"
    egress_path = root / "egress.jsonl"
    audit_path = root / "redaction_audit.jsonl"
    received_path = root / "received.jsonl"
    queue_path = root / "queue.db"
    offline_flag = Path(config.FORCE_OFFLINE_FLAG)
    if not offline_flag.is_absolute() and data_dir is not None:
        offline_flag = root / offline_flag.name

    app = Flask(__name__)
    store = QueueStore(queue_path)

    @app.get("/")
    def index() -> str:
        """Render the client-side live egress view."""
        return render_template_string(_PAGE)

    @app.get("/api/counters")
    def counters() -> Any:
        """Return egress status counts, queue depth, and current online state."""
        entries = _read_jsonl(egress_path)
        counts = {status: sum(1 for entry in entries if entry.get("status") == status) for status in ("queued", "sent", "acked", "retried", "blocked", "rejected")}
        received = _read_jsonl(received_path)
        last_acked = next((entry.get("ts_utc") for entry in reversed(entries) if entry.get("status") == "acked"), None)
        is_online = False
        if not offline_flag.exists() and isinstance(last_acked, str):
            try:
                ack_time = datetime.fromisoformat(last_acked.replace("Z", "+00:00"))
                if ack_time.tzinfo is None:
                    ack_time = ack_time.replace(tzinfo=timezone.utc)
                is_online = (datetime.now(timezone.utc) - ack_time.astimezone(timezone.utc)).total_seconds() <= 30
            except ValueError:
                is_online = False
        return jsonify(
            {
                **counts,
                "duplicates": _duplicate_count(received),
                "queue_depth": store.pending_count(),
                "online": is_online,
                "online_state": "online" if is_online else "offline",
            }
        )

    @app.get("/api/payloads")
    def payloads() -> Any:
        """Return the newest 20 egress entries without local raw-line audit data."""
        entries = _read_jsonl(egress_path)[-20:][::-1]
        return jsonify(
            {
                "payloads": [
                    {
                        "seq": entry.get("seq"),
                        "ts_utc": entry.get("ts_utc"),
                        "status": entry.get("status"),
                        "payload": entry.get("payload", {}),
                        "dropped_fields": entry.get("dropped_fields", []),
                        "redactions": entry.get("redactions", []),
                        "affected_services": entry.get("payload", {}).get("affected_services", []),
                    }
                    for entry in entries
                ]
            }
        )

    @app.get("/api/proof")
    def proof() -> Any:
        """Pair local-only raw audit lines with the matching sent payload."""
        egress_entries = _read_jsonl(egress_path)
        sent_by_id: dict[str, dict[str, Any]] = {}
        for entry in egress_entries:
            if entry.get("status") not in {"sent", "acked"}:
                continue
            payload = entry.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("payload_id"), str):
                sent_by_id[payload["payload_id"]] = payload
        proofs: list[dict[str, Any]] = []
        for audit in reversed(_read_jsonl(audit_path)):
            payload_id = audit.get("payload_id")
            if not isinstance(payload_id, str):
                continue
            proofs.append(
                {
                    "payload_id": payload_id,
                    "raw_line_label": "LOCAL ONLY, NEVER SENT",
                    "raw_line": audit.get("raw_line", ""),
                    "redacted_line": audit.get("redacted_line", ""),
                    "sent_payload": sent_by_id.get(payload_id),
                }
            )
        return jsonify({"proofs": proofs})

    @app.get("/api/chain")
    def chain() -> Any:
        """Return the hash-chain verification result."""
        return jsonify({"valid": verify_chain(egress_path)})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
