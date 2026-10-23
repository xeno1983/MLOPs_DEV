$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$tar = Join-Path $root "data/airgap/images.tar"
if (-not (Test-Path $tar)) {
    throw "Missing $tar. Run scripts/airgap-export.ps1 on a connected machine first."
}
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host "Loading images (no registry access)..."
docker load -i $tar

New-Item -ItemType Directory -Force -Path "data/inbox", "data/work", "data/keys" | Out-Null
$init = Join-Path $root "schemas/00-init.sh"
$unix = [IO.File]::ReadAllText($init) -replace "`r`n", "`n" -replace "`r", "`n"
[IO.File]::WriteAllText($init, $unix)

Write-Host "Starting stack offline (no pull, no build, no PyPI)..."
docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --no-build --pull never
docker compose ps
Write-Host "Portal: http://localhost:8080"
