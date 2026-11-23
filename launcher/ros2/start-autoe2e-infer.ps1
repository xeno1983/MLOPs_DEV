# Desktop / menu: Autoware AutoE2E ROS 2 infer node (via its Windows→WSL launcher)
param(
    [switch]$Build,
    [switch]$NoPause
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
. (Join-Path $Root "scripts\Invoke-WslRos2.ps1")

$aw = Get-MLopsAutowareRoot $Root
if (-not $aw) {
    Write-Error "AUTOWARE_E2E_ROOT が未設定です。.env を確認してください。"
    exit 1
}
$ps1 = Join-Path $aw "ros2\scripts\run_offroad_autoe2e.ps1"
if (-not (Test-Path $ps1)) {
    Write-Error "見つかりません: $ps1"
    exit 1
}
$args = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $ps1)
$rig = Join-Path $aw "config\rosbag2_autoe2e\rig.carla.yaml"
if (Test-Path $rig) { $args += @("-Rig", $rig) }
if ($Build) { $args += "-Build" }
Start-Process powershell -ArgumentList $args -WindowStyle Normal
Write-Host "AutoE2E ROS2 ノードを別窓で起動しました。"
if (-not $NoPause) { pause }
