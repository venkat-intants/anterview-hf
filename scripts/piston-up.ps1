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
    SECURITY POSTURE (2026-08-06). This container executes candidate-submitted
    code, so it runs with the narrowest privilege set that still works, not
    --privileged. See the block above the docker run line for what each flag buys
    and, more importantly, for what this still does NOT protect against.
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

# 0. Remove the cgroup tree a previous run left in the host hierarchy. See the
# cgroup note below the language table for why it outlives the container. This
# helper is deliberately minimal -- no capabilities except the one rmdir needs.
Write-Host 'Clearing any stale piston cgroup...' -ForegroundColor DarkGray
docker run --rm --cap-drop=ALL --cap-add=DAC_OVERRIDE `
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --entrypoint sh alpine `
    -c 'rmdir /sys/fs/cgroup/isolate/init 2>/dev/null; rmdir /sys/fs/cgroup/isolate 2>/dev/null; exit 0' 2>$null | Out-Null
Write-Host "Running Piston ($Image)..." -ForegroundColor Cyan
$tmpfs = "/piston/jobs:exec,uid=1000,gid=1000,mode=711"
# Raise Piston's run/compile timeout ceilings to 15s so our per-question
# time_limit_ms (up to 15000) is accepted — Piston defaults the max to 3000ms and
# would otherwise 400 with "run_timeout cannot exceed the configured limit".
$timeoutEnv = @(
    '-e', 'PISTON_RUN_TIMEOUT=15000',
    '-e', 'PISTON_RUN_CPU_TIME=15000',
    '-e', 'PISTON_COMPILE_TIMEOUT=15000',
    '-e', 'PISTON_COMPILE_CPU_TIME=15000',
    # Per-job network lockdown. Piston runs each job in its own network
    # namespace with no interfaces, so candidate code cannot reach the network
    # even though the container itself can (it must, to download runtimes in
    # step 3). Upstream already defaults this to true; set it explicitly so the
    # control is ours and not an upstream default that could change under us.
    '-e', 'PISTON_DISABLE_NETWORKING=true'
)

# --- Sandbox privileges -------------------------------------------------------
# This container runs code written by candidates. It used to run --privileged,
# which grants every capability, all host devices, an unmasked /proc and an
# unconfined seccomp/AppArmor profile -- i.e. it removed the isolation that is
# the entire point of a sandbox. The set below was derived empirically: each
# capability was verified necessary by removing it and watching Piston fail.
#
#   SYS_ADMIN    isolate's mount/namespace work (the one that hurts -- see below)
#   SYS_CHROOT   pivot into the per-job box
#   SYS_RESOURCE rlimits on the job
#   NET_ADMIN    brings up 'lo' INSIDE the job's empty netns. Counter-intuitive,
#                but this is the capability that lets isolate finish the network
#                lockdown; without it every run dies with SIOCSIFFLAGS on 'lo'.
#   SETUID/SETGID drop from root to the piston user
#   CHOWN/DAC_OVERRIDE/FOWNER/MKNOD  build the per-job box filesystem
#   KILL         terminate a job that exceeds its budget
#
# HONEST LIMIT: CAP_SYS_ADMIN is close to root and, combined with
# apparmor=unconfined, a container escape is not off the table. What this change
# removes is the easy paths -- host device access via /dev, unmasked /proc, and
# the full capability set. Treat this as "materially harder to escape", NOT as
# "isolated". Do not run this on a host that holds production credentials.
#
# Cgroups. --privileged used to mount the container's own cgroup subtree rw,
# which is why the old script never had to think about this. Without it Docker
# mounts /sys/fs/cgroup read-only and Piston's entrypoint dies on
# "mkdir: cannot create directory 'isolate/': Read-only file system", so we bind
# the cgroup2 root in rw. Note the consequence honestly: the bind mount overrides
# --cgroupns, so the 'isolate' cgroup is created in the HOST hierarchy and
# outlives the container -- the next start would fail with "File exists". Step 0
# below removes the stale tree so the script stays re-runnable.
$sandboxOpts = @(
    '--cap-drop=ALL',
    '--cap-add=SYS_ADMIN', '--cap-add=SYS_CHROOT', '--cap-add=SYS_RESOURCE',
    '--cap-add=NET_ADMIN', '--cap-add=SETUID', '--cap-add=SETGID',
    '--cap-add=CHOWN', '--cap-add=DAC_OVERRIDE', '--cap-add=FOWNER',
    '--cap-add=MKNOD', '--cap-add=KILL',
    # isolate needs mount operations the default AppArmor profile denies.
    '--security-opt', 'apparmor=unconfined',
    '--cgroupns=private',
    # cgroup v2 must be writable: the entrypoint creates the 'isolate' subtree.
    # --privileged used to provide this implicitly.
    '-v', '/sys/fs/cgroup:/sys/fs/cgroup:rw',
    # Blast radius caps if a job does get out of the per-job cgroup.
    '--pids-limit', '512'
)

# 127.0.0.1, not 0.0.0.0: an unauthenticated arbitrary-code-execution API must
# not be reachable from the local network.
docker run -d --restart unless-stopped -p 127.0.0.1:2000:2000 @sandboxOpts -v piston_packages:/piston/packages --tmpfs $tmpfs @timeoutEnv --name $Container $Image | Out-Null

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
