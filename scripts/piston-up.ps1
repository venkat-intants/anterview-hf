<#
.SYNOPSIS
    Start a self-hosted Piston code-execution engine and install the languages the
    coding round supports. Run ONCE after installing Docker Desktop.

.DESCRIPTION
    The free public Piston API (emkc.org) became whitelist-only on 2026-02-15, so
    the coding round must use a self-hosted Piston. This script:
      1. runs (or starts) the official Piston container on localhost:2000,
      2. waits for its API,
      3. installs each language runtime the platform supports.

    Then set in services/data_gateway/.env:
      EXECUTION_PROVIDER=piston
      PISTON_API_URL=http://localhost:2000/api/v2
    ...and restart data_gateway. (setup already wrote this env block.)

    Tear down later with:  docker rm -f piston_api

.NOTES
    --privileged is REQUIRED: Piston sandboxes each run with isolate/nsjail.
    First run downloads each runtime, so installing all languages takes a few minutes.
    ASCII-only on purpose (Windows PowerShell 5.1 reads .ps1 as ANSI).
#>

$ErrorActionPreference = 'Stop'
$Base = 'http://localhost:2000/api/v2'
$Container = 'piston_api'
$Image = 'ghcr.io/engineer-man/piston'

# Our language slug -> the Piston package "language" name(s) to match (aliases).
$Wanted = [ordered]@{
    python     = @('python')
    javascript = @('node')
    typescript = @('typescript')
    java       = @('java')
    cpp        = @('c++')
    c          = @('c', 'gcc')
    go         = @('go')
    csharp     = @('csharp', 'mono', 'csharp.net')
    ruby       = @('ruby')
    rust       = @('rust')
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Docker is not installed / not on PATH.' -ForegroundColor Red
    Write-Host 'Install Docker Desktop, start it, then re-run this script.'
    exit 1
}

# 1. (Re)create the container. Piston needs a writable packages dir and an
# EXEC-able /piston/jobs tmpfs (compiled programs run from there). Installed
# languages live in the named volume 'piston_packages', so recreating the
# container is cheap and non-destructive.
$exists = (docker ps -a --filter "name=$Container" --format '{{.Names}}') -eq $Container
if ($exists) {
    Write-Host "Removing old '$Container' (languages persist in the volume)..." -ForegroundColor DarkGray
    docker rm -f $Container | Out-Null
}
Write-Host "Running Piston ($Image)..." -ForegroundColor Cyan
$tmpfs = "/piston/jobs:exec,uid=1000,gid=1000,mode=711"
# Raise Piston's run/compile timeout ceilings to 15s so our per-question
# time_limit_ms (up to 15000) is accepted — Piston defaults the max to 3000ms and
# would otherwise 400 with "run_timeout cannot exceed the configured limit".
$timeoutEnv = @(
    '-e', 'PISTON_RUN_TIMEOUT=15000',
    '-e', 'PISTON_RUN_CPU_TIME=15000',
    '-e', 'PISTON_COMPILE_TIMEOUT=15000',
    '-e', 'PISTON_COMPILE_CPU_TIME=15000'
)
docker run --privileged -d --restart unless-stopped -p 2000:2000 -v piston_packages:/piston/packages --tmpfs $tmpfs --dns 8.8.8.8 @timeoutEnv --name $Container $Image | Out-Null

# 2. Wait for the API to answer.
Write-Host 'Waiting for the Piston API on :2000 ...' -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(120)
$ready = $false
while ((Get-Date) -lt $deadline -and -not $ready) {
    try { Invoke-RestMethod "$Base/runtimes" -TimeoutSec 4 | Out-Null; $ready = $true }
    catch { Start-Sleep -Seconds 3 }
}
if (-not $ready) { Write-Host 'Piston did not come up in time.' -ForegroundColor Red; exit 1 }
Write-Host 'Piston API is up.' -ForegroundColor Green

# 3. Install each wanted language (latest available version) if not already installed.
$packages = Invoke-RestMethod "$Base/packages"   # [{language, language_version, installed}]
foreach ($slug in $Wanted.Keys) {
    $aliases = $Wanted[$slug]
    $match = $packages |
        Where-Object { $aliases -contains $_.language } |
        Sort-Object { try { [version]($_.language_version -replace '[^0-9.].*$', '') } catch { [version]'0.0.0' } } |
        Select-Object -Last 1
    if (-not $match) {
        Write-Host ("  {0,-11} no matching Piston package, skipped" -f $slug) -ForegroundColor DarkYellow
        continue
    }
    if ($match.installed) {
        Write-Host ("  {0,-11} already installed ({1} {2})" -f $slug, $match.language, $match.language_version) -ForegroundColor DarkGray
        continue
    }
    Write-Host ("  {0,-11} installing {1} {2} ..." -f $slug, $match.language, $match.language_version) -ForegroundColor Cyan
    try {
        $body = @{ language = $match.language; version = $match.language_version } | ConvertTo-Json
        Invoke-RestMethod "$Base/packages" -Method Post -ContentType 'application/json' -Body $body | Out-Null
        Write-Host ("  {0,-11} installed" -f $slug) -ForegroundColor Green
    } catch {
        Write-Host ("  {0,-11} install failed: {1}" -f $slug, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host ''
Write-Host 'Done. Self-hosted Piston is running at http://localhost:2000/api/v2' -ForegroundColor Green
Write-Host 'services/data_gateway/.env already points at it. Restart data_gateway to use it.'
Write-Host 'Tear down with:  docker rm -f piston_api'
