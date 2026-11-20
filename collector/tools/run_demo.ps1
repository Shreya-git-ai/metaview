$ErrorActionPreference = "Stop"

# Run each command from the collector directory in a separate PowerShell terminal.
# Terminal 1: generate local telemetry
Write-Host "Terminal 1: .\..\venv\Scripts\python.exe generator.py"

# Terminal 2: start the local dummy SaaS ingest service
Write-Host "Terminal 2: .\..\venv\Scripts\python.exe tools\dummy_ingest.py"

# Terminal 3: start the collector
Write-Host "Terminal 3: .\..\venv\Scripts\python.exe collector.py"
