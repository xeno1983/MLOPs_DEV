# Windows: CARLA オフロード収集環境（WSL Python3.10 + carla-0-9-15 wheel + Mine_01）
#   .\scripts\setup-carla-offroad.ps1
#   .\scripts\setup-carla-offroad.ps1 -SkipMine
param(
    [switch]$SkipMine
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$sh = "/mnt/c/Users/xeno1/MLOPS/scripts/setup-carla-offroad-env.sh"
# Prefer Ubuntu (CARLA lives there), fall back to Ubuntu-24.04
$distro = "Ubuntu"
wsl -d $distro -- echo ok *> $null
if ($LASTEXITCODE -ne 0) { $distro = "Ubuntu-24.04" }

$args = @()
if ($SkipMine) { $args += "--skip-mine" }
$argStr = ($args -join " ")

Write-Host "WSL distro: $distro"
Write-Host "Running setup-carla-offroad-env.sh ..."
wsl -d $distro -- bash -lc "sed -i 's/\r`$//' $sh; chmod +x $sh; export CARLA_ROOT=`${CARLA_ROOT:-`$HOME/CARLA}; bash $sh $argStr"
if ($LASTEXITCODE -ne 0) { throw "setup failed ($LASTEXITCODE)" }

Write-Host ""
Write-Host "Next: .\scripts\start-carla-offroad.ps1"
Write-Host "  1) CARLA server  2) WASD drive + B record rosbag2"
