# Desktop / menu: AutoE2E rosbag2 record (+ odom bridge) via WSL
param(
    [string]$RunId = "",
    [switch]$NoPause
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
. (Join-Path $Root "scripts\Invoke-WslRos2.ps1")

$d = Get-MLopsWslDistro
$w = ConvertTo-WslPath $Root
$c = Get-MLopsCarlaRootHint $Root
$aw = Get-MLopsAutowareRoot $Root
if (-not $RunId) {
    $RunId = Read-Host "bag 名 [mine_autoe2e_001]"
}
if ([string]::IsNullOrWhiteSpace($RunId)) { $RunId = "mine_autoe2e_001" }

$sh = "$w/scripts/carla-autoe2e-record.sh"
Repair-WslScriptLineEndings -Distro $d -WslPaths @($sh, "$w/scripts/carla-odom-bridge.py")
$awEnv = ""
if ($aw) {
    $waw = ConvertTo-WslPath $aw
    $awEnv = "export AUTOWARE_E2E_ROOT='$waw'; "
}
Start-WslRos2Window -Title "ros2-bag-record" -Distro $d -CarlaRootHint $c -NeedCarlaEgg `
    -BashCommand "${awEnv}export CARLA_ROOT=`"`${CARLA_ROOT:-$c}`"; exec bash '$sh' '$RunId'"
Write-Host "録画窓を開きました。Ctrl+C で停止。bag → Autoware_e2e/datasets/carla_bags/$RunId"
if (-not $NoPause) { pause }
