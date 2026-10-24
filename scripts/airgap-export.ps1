$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "data/airgap" | Out-Null

Write-Host "Pulling base images and building local services (needs network)..."
docker compose pull
docker compose build

$images = @(docker compose config --images)
$images += @(
    "ugv-mlops-catalog:latest",
    "ugv-mlops-worker:latest",
    "ugv-mlops-mlflow:latest"
)
$unique = $images | Where-Object { $_ -and $_ -ne "<none>:<none>" } | Sort-Object -Unique
$manifest = Join-Path $root "data/airgap/images.txt"
$unique | Set-Content -Path $manifest -Encoding utf8
$tar = Join-Path $root "data/airgap/images.tar"
Write-Host "Saving $($unique.Count) images to $tar"
docker save -o $tar @unique
Write-Host "Done. Copy this repository AND data/airgap/images.tar to the air-gapped host."
Write-Host "On that host: .\scripts\airgap-import.ps1"
