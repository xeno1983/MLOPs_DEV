# AutoE2E × MLOPS 連携の前提を整える（Autoware_e2e は別リポ）
#   .\scripts\setup-autoe2e.ps1
#   .\scripts\setup-autoe2e.ps1 -StartWorker
param(
    [switch]$StartWorker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = "src"

$aw = $env:AUTOWARE_E2E_ROOT
if (-not $aw) {
    foreach ($line in Get-Content (Join-Path $Root ".env") -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*AUTOWARE_E2E_ROOT\s*=\s*(.+)$') {
            $aw = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
}
if (-not $aw) { $aw = "C:\Users\xeno1\Autoware_e2e" }

$rigCarla = Join-Path $aw "config\rosbag2_autoe2e\rig.carla.yaml"
$rigDef = Join-Path $aw "config\rosbag2_autoe2e\rig.yaml"
if ((Test-Path $rigCarla) -and -not (Test-Path $rigDef)) {
    Copy-Item $rigCarla $rigDef
    Write-Host "created $rigDef"
}

# .env upsert AUTOE2E_RIG
$envPath = Join-Path $Root ".env"
if (Test-Path $envPath) {
    $raw = Get-Content $envPath -Raw
    $line = "AUTOE2E_RIG=$($rigCarla -replace '\\','/')"
    if ($raw -match "(?m)^#?\s*AUTOE2E_RIG\s*=") {
        $raw = [regex]::Replace($raw, "(?m)^#?\s*AUTOE2E_RIG\s*=.*$", $line)
    } else {
        if ($raw -notmatch "\n$") { $raw += "`n" }
        $raw += $line + "`n"
    }
    Set-Content $envPath $raw -Encoding UTF8
    Write-Host "Updated AUTOE2E_RIG in .env"
}

Write-Host "=== autoe2e-status ==="
python -m ugv_mlops.cli autoe2e-status
if ($LASTEXITCODE -ne 0) {
    Write-Host "前提が足りません。Autoware_e2e の .venv-autoe2e と tools を確認してください。"
}

if ($StartWorker) {
    $bat = Join-Path $Root "launcher\MLOPS_gpu_worker.bat"
    if (Test-Path $bat) {
        Write-Host "Starting GPU host worker…"
        Start-Process $bat
    } else {
        Write-Host "missing $bat — run scripts\setup-gpu-worker.ps1 first"
    }
}

Write-Host ""
Write-Host "次: CARLA 録画 scripts/carla-autoe2e-record.sh → ポータル ingest → task=autoe2e"
Write-Host "詳細: docs/autoe2e.md"
