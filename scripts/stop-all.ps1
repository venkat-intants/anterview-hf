# stop-all.ps1 — Intants AI Interview Platform — kill dev service processes
# Usage:  .\scripts\stop-all.ps1
# Kills processes bound to ports 8001 (interview_core), 8002 (data_gateway), 5174 (web/Vite).
# Does NOT stop Docker — Postgres data is left intact between sessions.
# S3-014 retro action item 3.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'   # don't abort if one port is already free

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Stop-Port {
    param([int]$Port)
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        Write-Host "    Port $Port — nothing listening, skipped." -ForegroundColor DarkGray
        return
    }
    foreach ($conn in $connections) {
        $pid = $conn.OwningProcess
        try {
            $proc = Get-Process -Id $pid -ErrorAction Stop
            $procName = $proc.ProcessName
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "    Port $Port — killed PID $pid ($procName)" -ForegroundColor Green
        } catch {
            Write-Host "    Port $Port — PID $pid could not be stopped: $_" -ForegroundColor Yellow
        }
    }
}

Write-Step "Stopping Intants dev services (ports 8001, 8002, 5174)..."

Stop-Port -Port 8001
Stop-Port -Port 8002
Stop-Port -Port 5174

Write-Host ""
Write-Host "Done. Docker containers (Postgres, Redis, MinIO, Mailpit) left running." -ForegroundColor Cyan
Write-Host "To stop Docker too:  cd infra\docker && docker compose down" -ForegroundColor DarkGray
Write-Host ""
