# Desktop / menu: CARLA → ROS2 Odometry bridge (WSL)
param([switch]$NoPause)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
. (Join-Path $Root "scripts\Invoke-WslRos2.ps1")

$d = Get-MLopsWslDistro
$w = ConvertTo-WslPath $Root
$c = Get-MLopsCarlaRootHint $Root
$py = "$w/scripts/carla-odom-bridge.py"
Repair-WslScriptLineEndings -Distro $d -WslPaths @($py)
Start-WslRos2Window -Title "carla-odom-bridge" -Distro $d -CarlaRootHint $c -NeedCarlaEgg `
    -BashCommand "exec python3 '$py' --host 127.0.0.1 --port 2000"
Write-Host "別窓で odom bridge を起動しました。この窓は閉じて構いません。"
if (-not $NoPause) { pause }
