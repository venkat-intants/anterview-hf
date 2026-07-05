<#
.SYNOPSIS
    Stop any running Intants dev servers, then relaunch the full stack.

.DESCRIPTION
    1. Kills whatever is listening on the stack ports (8001-8004, 5174).
    2. Kills the LiveKit interview worker (no HTTP port) by command line.
    3. Runs dev-up.ps1 to relaunch all 6 processes in their own windows.

    Run from the repo root:  .\restart-stack.ps1
    (stop-all.ps1 only covers 8001/8002/5174 -- this covers the whole stack.)
#>

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot

Write-Host ''
Write-Host '==> Stopping existing dev servers...' -ForegroundColor Cyan

$ports = 8001, 8002, 8003, 8004, 5174
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "    Port $port -- nothing listening." -ForegroundColor DarkGray
        continue
    }
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        try {
            $p = Get-Process -Id $procId -ErrorAction Stop
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "    Port $port -- killed PID $procId ($($p.ProcessName))" -ForegroundColor Green
        } catch {
            Write-Host "    Port $port -- PID $procId could not stop: $_" -ForegroundColor Yellow
        }
    }
}

# LiveKit interview worker has no HTTP port -- find it by command line.
$workers = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'interview_worker' }
foreach ($w in $workers) {
    try {
        Stop-Process -Id $w.ProcessId -Force -ErrorAction Stop
        Write-Host "    Worker -- killed PID $($w.ProcessId)" -ForegroundColor Green
    } catch {
        Write-Host "    Worker PID $($w.ProcessId) could not stop: $_" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '==> Relaunching stack via dev-up.ps1...' -ForegroundColor Cyan
& (Join-Path $root 'dev-up.ps1')
