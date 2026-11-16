# Create .venv-gpuworker for the GPU host worker. The AutoE2E trainer itself runs in Autoware_e2e\.venv-autoe2e.
# Usage:  .\scripts\setup-gpu-worker.ps1
# ASCII only on purpose: Windows PowerShell 5 reads BOM-less files as ANSI.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venv = Join-Path $Root ".venv-gpuworker"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-hostworker.txt
& $py -m pip install -e . --no-deps
& $py -c "import ugv_mlops.autoe2e, ugv_mlops.jobs, ugv_mlops.integrations; print('host worker imports OK')"

$aw = "C:\Users\xeno1\Autoware_e2e"
$line = (Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match '^AUTOWARE_E2E_ROOT=' } | Select-Object -First 1)
if ($line) {
    $v = ($line -split '=', 2)[1].Trim()
    if ($v -notlike "*autoware_stub*") { $aw = $v }
}
Write-Host ""
Write-Host "AUTOWARE_E2E_ROOT = $aw"
foreach ($p in @("tools\auto_e2e_finetune_offroad.py", ".venv-autoe2e\Scripts\python.exe", "config\rosbag2_autoe2e\rig.yaml", "checkpoints\auto_e2e\models\nuplan-epoch-5\checkpoint.pt")) {
    $full = Join-Path $aw $p
    $mark = if (Test-Path $full) { "OK     " } else { "MISSING" }
    Write-Host ("  [{0}] {1}" -f $mark, $p)
}
Write-Host ""
Write-Host "Next: start compose, then launcher\MLOPS_gpu_worker.bat"
