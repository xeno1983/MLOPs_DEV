# CARLA / mining_sim をホストから起動（compose 外）
# 例:
#   .\scripts\carla-launch.ps1 -Component server
#   .\scripts\carla-launch.ps1 -Component stack -DryRun
#   .\scripts\carla-launch.ps1 -Component stop
param(
    [ValidateSet("server", "mining_sim", "stack", "stop")]
    [string]$Component = "server",
    [switch]$DryRun,
    [double]$WaitReadySec = 0
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = "src"

if (-not $env:CARLA_ROOT -and (Test-Path (Join-Path $Root ".env"))) {
    Get-Content (Join-Path $Root ".env") | ForEach-Object {
        if ($_ -match '^\s*CARLA_ROOT\s*=\s*(.+)$') {
            $env:CARLA_ROOT = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
}

if ($Component -eq "stop") {
    python -m ugv_mlops.cli carla-stop --component stack
    exit $LASTEXITCODE
}

$argsList = @("-m", "ugv_mlops.cli", "carla-launch", "--component", $Component)
if ($DryRun) { $argsList += "--dry-run" }
if ($WaitReadySec -gt 0) { $argsList += @("--wait-ready-sec", "$WaitReadySec") }
python @argsList
exit $LASTEXITCODE
