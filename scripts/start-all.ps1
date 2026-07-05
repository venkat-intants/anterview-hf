# start-all.ps1 — Intants AI Interview Platform — full local dev stack startup
# Usage:  .\scripts\start-all.ps1
# Brings up: Docker (Postgres + Redis + MinIO + Mailpit) -> data_gateway -> interview_core -> web
# Each service is polled before the next one starts (max 30 s each, exit 1 on timeout).
# S3-014 retro action item 3 — closes sprint-02/retro.md action item 3.
#
# Requirements:
#   - Docker Desktop running
#   - Poetry installed and `poetry install` already run in each service directory
#   - npm (npm.cmd) installed for the web/ directory

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    [WARN] $msg" -ForegroundColor Yellow
}

function Write-Fail([string]$msg) {
    Write-Host "    [FAIL] $msg" -ForegroundColor Red
}

# Poll a condition scriptblock until it returns $true.
# Returns $true on success, exits script with code 1 on timeout.
function Wait-Until {
    param(
        [scriptblock]$Condition,
        [string]$Label,
        [int]$TimeoutSecs = 30,
        [int]$IntervalSecs = 2
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSecs)
    Write-Host "    Waiting for: $Label (timeout ${TimeoutSecs}s) ..." -ForegroundColor DarkCyan
    while ((Get-Date) -lt $deadline) {
        try {
            $result = & $Condition
            if ($result) {
                Write-OK "$Label is ready."
                return $true
            }
        } catch {
            # condition threw — keep polling
        }
        Start-Sleep -Seconds $IntervalSecs
    }
    Write-Fail "Timeout after ${TimeoutSecs}s waiting for: $Label"
    exit 1
}

# Poll an HTTP endpoint until it returns HTTP 200.
function Wait-Http {
    param([string]$Url, [string]$Label, [int]$TimeoutSecs = 30)
    Wait-Until -Label "$Label ($Url)" -TimeoutSecs $TimeoutSecs -Condition {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            return ($resp.StatusCode -eq 200)
        } catch {
            return $false
        }
    }
}

# Discover the Poetry-managed Python interpreter for a service directory.
# Returns the full path to python.exe.
# Falls back to <dir>\.venv\Scripts\python.exe with a warning if poetry fails.
function Get-PoetryPython([string]$ServiceDir) {
    try {
        $envPath = & poetry --directory $ServiceDir env info --path 2>$null
        if ($LASTEXITCODE -eq 0 -and $envPath -and (Test-Path "$envPath\Scripts\python.exe")) {
            return "$envPath\Scripts\python.exe"
        }
    } catch { }

    # Fallback
    $fallback = Join-Path $ServiceDir ".venv\Scripts\python.exe"
    if (Test-Path $fallback) {
        Write-Warn "poetry env info failed for $ServiceDir — falling back to $fallback"
        return $fallback
    }
    Write-Fail "Cannot find Python interpreter for $ServiceDir. Run 'poetry install' inside that directory."
    exit 1
}

# Ensure the logs/ directory exists under repo root.
function Ensure-LogDir {
    $logsDir = Join-Path $RepoRoot "logs"
    if (-not (Test-Path $logsDir)) {
        New-Item -ItemType Directory -Path $logsDir | Out-Null
        Write-Host "    Created logs/ directory at $logsDir" -ForegroundColor DarkGray
    }
    return $logsDir
}

# Start a service in a new PowerShell window, redirecting stdout+stderr to a
# timestamped log file. Returns the log file path.
function Start-Service {
    param(
        [string]$Name,
        [string]$WorkingDir,
        [string]$Command,       # full command string passed to pwsh -Command
        [string]$LogsDir
    )
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logFile = Join-Path $LogsDir "${Name}_${timestamp}.log"

    # Build a self-contained script block that changes to the working dir,
    # runs the command, and tees output to the log file.
    $innerScript = @"
Set-Location '$WorkingDir'
& $Command *>&1 | Tee-Object -FilePath '$logFile'
"@

    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit", "-Command", $innerScript `
        -WindowStyle Normal

    Write-Host "    Log file: $logFile" -ForegroundColor DarkGray
    Write-Host "    (tail with: Get-Content -Wait '$logFile')" -ForegroundColor DarkGray
    return $logFile
}

# ---------------------------------------------------------------------------
# Step 0 — Check Docker Desktop is running
# ---------------------------------------------------------------------------
Write-Step "Checking Docker Desktop..."
try {
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker info returned non-zero" }
    Write-OK "Docker Desktop is running."
} catch {
    Write-Fail "Docker Desktop is not running. Please start Docker Desktop and retry."
    exit 1
}

# ---------------------------------------------------------------------------
# Step 1 — Bring up infra containers
# ---------------------------------------------------------------------------
Write-Step "Starting infra containers (docker compose up -d)..."
$logsDir = Ensure-LogDir
$dockerComposeDir = Join-Path $RepoRoot "infra\docker"
Push-Location $dockerComposeDir
try {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "docker compose up -d failed."
        exit 1
    }
    Write-OK "Docker compose started."
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Step 2 — Wait for Postgres
# ---------------------------------------------------------------------------
Write-Step "Waiting for Postgres to be ready..."
Wait-Until -Label "Postgres pg_isready" -TimeoutSecs 30 -Condition {
    $result = docker compose --project-directory $dockerComposeDir exec -T postgres `
        pg_isready -U intants 2>&1
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------
# Step 3 — Start data_gateway (port 8002)
# ---------------------------------------------------------------------------
Write-Step "Starting data_gateway (port 8002)..."
$dgDir = Join-Path $RepoRoot "services\data_gateway"
$dgPython = Get-PoetryPython -ServiceDir $dgDir
$dgCmd = "'$dgPython' -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8002"
Start-Service -Name "data_gateway" -WorkingDir $dgDir -Command $dgCmd -LogsDir $logsDir | Out-Null

Write-Step "Polling data_gateway health..."
Wait-Http -Url "http://localhost:8002/health/live" -Label "data_gateway" -TimeoutSecs 30

# ---------------------------------------------------------------------------
# Step 4 — Start interview_core (port 8001)
# ---------------------------------------------------------------------------
Write-Step "Starting interview_core (port 8001)..."
$icDir = Join-Path $RepoRoot "services\interview_core"
$icPython = Get-PoetryPython -ServiceDir $icDir
$icCmd = "'$icPython' -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001"
Start-Service -Name "interview_core" -WorkingDir $icDir -Command $icCmd -LogsDir $logsDir | Out-Null

Write-Step "Polling interview_core health..."
Wait-Http -Url "http://localhost:8001/health/live" -Label "interview_core" -TimeoutSecs 30

# ---------------------------------------------------------------------------
# Step 5 — Start web (Vite dev server, port 5174)
# ---------------------------------------------------------------------------
Write-Step "Starting web (Vite dev server, port 5174)..."
$webDir = Join-Path $RepoRoot "web"
# On Windows, npm is npm.cmd — invoke via cmd.exe to ensure correct resolution.
$webCmd = "cmd.exe /c 'npm.cmd run dev'"
Start-Service -Name "web" -WorkingDir $webDir -Command $webCmd -LogsDir $logsDir | Out-Null

Write-Step "Polling web dev server..."
Wait-Http -Url "http://localhost:5174" -Label "web (Vite)" -TimeoutSecs 30

# ---------------------------------------------------------------------------
# Success block
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  All 4 services up -- open http://localhost:5174" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Service URLs:" -ForegroundColor White
Write-Host "    web (Vite)        http://localhost:5174" -ForegroundColor White
Write-Host "    interview_core    http://localhost:8001   ws://localhost:8001/ws" -ForegroundColor White
Write-Host "    data_gateway      http://localhost:8002" -ForegroundColor White
Write-Host "    MinIO console     http://localhost:9001" -ForegroundColor White
Write-Host "    Mailpit UI        http://localhost:8025" -ForegroundColor White
Write-Host ""
Write-Host "  Logs directory: $(Join-Path $RepoRoot 'logs')" -ForegroundColor DarkGray
Write-Host "  To stop:  .\scripts\stop-all.ps1" -ForegroundColor DarkGray
Write-Host ""
