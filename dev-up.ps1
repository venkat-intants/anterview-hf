<#
.SYNOPSIS
    Launch all local dev servers for the Intants AI interview platform.

.DESCRIPTION
    Opens each service in its own PowerShell window so you can watch its logs
    independently. Run from the repo root:  .\dev-up.ps1

    The 6 processes:
      1. data_gateway      :8002   auth / users / DPDP consent   (in-project .venv)
      2. interview_core    :8001   API: sessions, room tokens, /api/avatars
      3. feedback_billing  :8003   scoring + scorecard + PDF. Follows
                                     LLM_PROVIDER (groq locally, not Gemini) --
                                     except embeddings, which are Gemini-only
                                     because Groq serves no embeddings API.
      4. admin_ops         :8004   admin/analytics dashboard API
      5. interview_core    (no port) LiveKit worker -- the real-time avatar+voice
                                     engine. MUST be its own process (cli.run_app
                                     owns the process + spawns a job subprocess
                                     per interview; can't live inside uvicorn).
      6. web               :5174   React/Vite frontend

    Datastores are LOCAL containers, not cloud: Postgres :55432, Redis :6379,
    MinIO :9000, Mailpit :8025 (see docker-compose). Start them first -- every
    service above fails its first query without them. This block used to say
    the DB was Neon and Redis was Upstash with "nothing local", and that
    .env stopped being true some time ago; it also said admin_ops "is not
    started" while step 4 below has always started it.

.NOTES
    All four services use their in-project .venv python directly (created with
    `<python> -m venv .venv` + `pip install -r requirements.txt`), then a
    .pth file pointing at the repo root so `shared` imports. The venvs on this
    machine are 3.13.2, not the 3.12 this line used to name -- and the `py`
    launcher it used to invoke is not installed here, so copy the interpreter
    from a sibling service's .venv when creating a new one. Do NOT
    `poetry install` into these dev venvs -- they are pip-managed. `shared` is
    wired in via a .pth file in each .venv.

    requirements.txt is the ONLY install source anywhere: dev venvs, all four
    services/*/Dockerfile builds, and CI (.github/workflows/ci.yml installs
    `pip install -r requirements.txt` per service). This note used to say
    "poetry.lock is the CI test env" -- it never was, and believing it is what
    let the pyproject dependency blocks drift out of sync with what the services
    actually import without anyone noticing. The poetry.lock files on disk are
    unused build artefacts; do not hand-sync them and do not trust them to tell
    you what ships. (Closes code-review finding DEP-2.)
#>

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Start-Svc {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$WorkDir,
        [Parameter(Mandatory)][string]$Command
    )
    if (-not (Test-Path $WorkDir)) {
        Write-Warning "Skipping '$Title' -- directory not found: $WorkDir"
        return
    }
    # Build the child-shell command. Backtick-escape $host so it stays literal
    # and runs in the new window; $Title/$WorkDir/$Command are expanded here.
    $inner = "`$host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$WorkDir'; Write-Host '=== $Title ===' -ForegroundColor Cyan; $Command"
    Start-Process -FilePath 'powershell' -ArgumentList @('-NoExit', '-NoProfile', '-Command', $inner) | Out-Null
    Write-Host "  launched: $Title" -ForegroundColor Green
}

$ic  = Join-Path $root 'services\interview_core'
$dg  = Join-Path $root 'services\data_gateway'
$fb  = Join-Path $root 'services\feedback_billing'
$ao  = Join-Path $root 'services\admin_ops'
$web = Join-Path $root 'web'

$venvPy = '.\.venv\Scripts\python.exe'

Write-Host ''
Write-Host 'Starting Intants dev stack (6 processes)...' -ForegroundColor Yellow

# 1. data_gateway -- auth (:8002) -- in-project .venv
Start-Svc 'data_gateway :8002' $dg `
    "$venvPy -m uvicorn app.main:app --host 0.0.0.0 --port 8002"

# 2. interview_core -- API (:8001) -- in-project .venv
Start-Svc 'interview_core API :8001' $ic `
    "$venvPy -m uvicorn app.main:app --host 0.0.0.0 --port 8001"

# 3. feedback_billing -- scoring (:8003) -- in-project .venv
Start-Svc 'feedback_billing :8003' $fb `
    "$venvPy -m uvicorn app.main:app --host 0.0.0.0 --port 8003"

# 4. admin_ops -- admin/analytics dashboard API (:8004) -- in-project .venv
Start-Svc 'admin_ops :8004' $ao `
    "$venvPy -m uvicorn app.main:app --host 0.0.0.0 --port 8004"

# 5. interview_core -- LiveKit worker (no HTTP port). PYTHONUTF8 = clean Windows logs.
Start-Svc 'interview_core worker' $ic `
    "`$env:PYTHONUTF8 = '1'; $venvPy -m app.worker.interview_worker dev"

# 6. web -- frontend (:5174)
Start-Svc 'web :5174' $web 'npm run dev'

Write-Host ''
Write-Host 'All processes launched in separate windows.' -ForegroundColor Green
Write-Host '  data_gateway     http://localhost:8002'
Write-Host '  interview_core   http://localhost:8001'
Write-Host '  feedback_billing http://localhost:8003'
Write-Host '  admin_ops        http://localhost:8004'
Write-Host '  interview worker (LiveKit -- no HTTP port)'
Write-Host '  web (frontend)   http://localhost:5174'
Write-Host ''
Write-Host 'Once every window shows it has started, open http://localhost:5174' -ForegroundColor Cyan
Write-Host 'Tip: start the worker window BEFORE beginning an interview.' -ForegroundColor DarkGray
