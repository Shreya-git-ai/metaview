# Starts the five NEXORA processes (SaaS API, 2 collectors, SaaS dashboard, Tier-1 Local Console), each in its own PowerShell window.
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path (Join-Path $root "config\saas.env"))) { & $py (Join-Path $root "scripts\setup_dev.py") }

Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='NEXORA SaaS (TLS :8443)'; & '$py' -m saas_backend.main --env config/saas.env"
Start-Sleep -Seconds 3
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='Collector col-alpha-01'; & '$py' -m local_collector.collector --env config/col-alpha-01.env"
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='Collector col-beta-01'; & '$py' -m local_collector.collector --env config/col-beta-01.env"
Start-Process powershell -WorkingDirectory (Join-Path $root "frontend") -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='NEXORA SaaS dashboard :5173'; npm run dev"
Start-Process powershell -WorkingDirectory (Join-Path $root "frontend") -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='NEXORA Local Console (Tier 1) :5174'; npm run dev:local"
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173/login"
