# Desktop / menu: interactive WSL shell with ROS 2 sourced
param([switch]$NoPause)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
. (Join-Path $Root "scripts\Invoke-WslRos2.ps1")

$d = Get-MLopsWslDistro
$w = ConvertTo-WslPath $Root
$c = Get-MLopsCarlaRootHint $Root
$aw = Get-MLopsAutowareRoot $Root
$cd = "cd '$w';"
if ($aw) {
    $waw = ConvertTo-WslPath $aw
    $cd = "export AUTOWARE_E2E_ROOT='$waw'; cd '$waw' || cd '$w';"
}
Start-WslRos2Window -Title "ros2-shell" -Distro $d -CarlaRootHint $c -NeedCarlaEgg `
    -BashCommand "$cd echo 'ROS2 shell ready. try: ros2 topic list'; exec bash"
Write-Host "ROS2 シェルを別窓で開きました。"
if (-not $NoPause) { pause }
