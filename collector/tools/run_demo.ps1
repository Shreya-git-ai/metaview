$ErrorActionPreference = "Stop"

# Run these commands from collector/ in four separate PowerShell terminals.
# Terminal 1: $env:PROBE_MODE='mock'; .\..\venv\Scripts\python.exe probes.py
# Terminal 2: .\..\venv\Scripts\python.exe tools\dummy_ingest.py
# Terminal 3: $env:INGEST_URL='http://127.0.0.1:8000/ingest'; $env:HEARTBEAT_URL='http://127.0.0.1:8000/heartbeat'; .\..\venv\Scripts\python.exe collector.py
# Terminal 4: .\..\venv\Scripts\python.exe egress_panel.py (open http://127.0.0.1:5000)

Write-Host "Open separate terminals in collector/ and run the commands in this file's comments."
