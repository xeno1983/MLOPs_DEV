$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — change passwords before any sensitive data."
}

New-Item -ItemType Directory -Force -Path "data/inbox", "data/work", "data/keys", "data/airgap" | Out-Null

$init = Join-Path $root "schemas/00-init.sh"
$unix = [IO.File]::ReadAllText($init) -replace "`r`n", "`n" -replace "`r", "`n"
[IO.File]::WriteAllText($init, $unix)

Write-Host "Bootstrap complete. Next: docker compose up -d --build"
