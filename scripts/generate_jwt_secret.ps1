# ==============================================================================
# generate_jwt_secret.ps1
#
# PURPOSE: First-time dev setup OR secret rotation.
#          Generates a fresh cryptographically-secure 64-hex JWT secret and
#          writes it into ALL 4 backend .env files so they stay identical.
#
# USAGE:   pwsh ./scripts/generate_jwt_secret.ps1
#
# Run this whenever you:
#   - Set up a new dev environment
#   - Rotate the JWT secret (e.g., after a security incident or periodic rotation)
#
# After rotation, restart all 4 backend services so they pick up the new secret.
# ==============================================================================

$ErrorActionPreference = "Stop"

# Generate 64-hex secret using cryptographically secure RNG
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 32
$rng.GetBytes($bytes)
$newSecret = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""

Write-Host "Generated new JWT_SECRET (first 8 chars): $($newSecret.Substring(0, 8))..."

# All 4 backend .env files that must share the same JWT_SECRET
$envFiles = @(
    "$PSScriptRoot\..\services\data_gateway\.env",
    "$PSScriptRoot\..\services\interview_core\.env",
    "$PSScriptRoot\..\services\feedback_billing\.env",
    "$PSScriptRoot\..\services\admin_ops\.env"
)

foreach ($envFile in $envFiles) {
    $resolved = Resolve-Path $envFile -ErrorAction SilentlyContinue
    if (-not $resolved) {
        Write-Host "FAILED — file not found: $envFile"
        continue
    }

    $content = Get-Content $resolved -Raw
    if ($content -match "JWT_SECRET=") {
        # Replace the existing JWT_SECRET line
        $updated = $content -replace "(?m)^JWT_SECRET=.*$", "JWT_SECRET=$newSecret"
        Set-Content -Path $resolved -Value $updated -NoNewline
        Write-Host "OK     — $resolved"
    } else {
        Write-Host "FAILED — JWT_SECRET line not found in: $resolved"
    }
}

Write-Host ""
Write-Host "Done. Restart all 4 backend services to apply the new secret."
