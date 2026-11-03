# GPU host worker: picks up runner=host jobs (AutoE2E dataset build / fine-tune) from the Catalog.
# compose (postgres, garage, mlflow) must already be running; their ports are published on localhost.
# ASCII only on purpose: Windows PowerShell 5 reads BOM-less files as ANSI.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Read-DotEnv([string]$Key) {
    $file = Join-Path $Root ".env"
    if (-not (Test-Path $file)) { return $null }
    foreach ($line in Get-Content -LiteralPath $file -Encoding UTF8) {
        $t = $line.Trim()
        if ($t.StartsWith("#") -or ($t.IndexOf("=") -lt 1)) { continue }
        $eq = $t.IndexOf("=")
        if ($t.Substring(0, $eq).Trim() -eq $Key) { return $t.Substring($eq + 1).Trim().Trim('"').Trim("'") }
    }
    return $null
}

$py = Join-Path $Root ".venv-gpuworker\Scripts\python.exe"
if (-not (Test-Path $py)) { throw ".venv-gpuworker is missing. Run .\scripts\setup-gpu-worker.ps1 first." }

# .env uses compose-internal host names (postgres / garage / mlflow); from the host use the published localhost ports.
$pgUser = Read-DotEnv "POSTGRES_USER"; if (-not $pgUser) { $pgUser = "ugv" }
$pgPass = Read-DotEnv "POSTGRES_PASSWORD"; if (-not $pgPass) { $pgPass = "ugv_change_me" }
$env:CATALOG_DATABASE_URL = "postgresql+psycopg://${pgUser}:${pgPass}@127.0.0.1:5432/catalog"
$env:MINIO_ENDPOINT = "127.0.0.1:9000"
$env:MINIO_SECURE = "false"
$env:S3_REGION = "garage"
foreach ($k in @(
    "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "DEFAULT_LEARNING_STAGE", "AUTOWARE_E2E_ROOT",
    "AUTOE2E_RIG", "AUTOE2E_PYTHON", "AUTOE2E_EPOCHS", "AUTOE2E_STRIDE", "AUTOE2E_ACCUM_STEPS", "AUTOE2E_EXTRA_ARGS"
)) {
    $v = Read-DotEnv $k
    if ($v) { Set-Item -Path "Env:$k" -Value $v }
}
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
$env:INBOX_DIR = (Join-Path $Root "data\inbox")
$env:WORK_DIR = (Join-Path $Root "data\work")
$env:LAKEFS_URL = "http://127.0.0.1:8001"   # only reachable with --profile full; the commit is best-effort
$env:PYTHONUTF8 = "1"
$env:MLFLOW_DISABLE_AGENT_HINT = "1"
$env:JOB_RUNNER = "host"
$env:PYTHONPATH = (Join-Path $Root "src")

# If AUTOWARE_E2E_ROOT is still the compose stub (./data/autoware_stub), fall back to the real checkout.
if ((-not $env:AUTOWARE_E2E_ROOT) -or ($env:AUTOWARE_E2E_ROOT -like "*autoware_stub*")) {
    $env:AUTOWARE_E2E_ROOT = "C:\Users\xeno1\Autoware_e2e"
}

Write-Host "GPU host worker (Ctrl+C to stop)  Autoware_e2e = $env:AUTOWARE_E2E_ROOT"
& $py -u -m ugv_mlops.host_worker
