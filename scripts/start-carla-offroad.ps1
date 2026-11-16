# CARLA: Town01 + Tesla Cybertruck + 多センサ録画
#   .\scripts\start-carla-offroad.ps1
#   .\scripts\start-carla-offroad.ps1 -Map Town01
#   .\scripts\start-carla-offroad.ps1 -SkipServer          # 既に CARLA 起動済み
# センサ: RGB×6 / LiDAR×3 / GNSS / IMU（scripts/carla_offroad_collect.py）
param(
    [string]$Map = "Town01",
    [switch]$SkipServer,
    [switch]$NoSync
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$distro = "Ubuntu"
wsl -d $distro -- echo ok *> $null
if ($LASTEXITCODE -ne 0) { $distro = "Ubuntu-24.04" }

$envDir = "/home/xeno1/.venv-carla915"
$collect = "/mnt/c/Users/xeno1/MLOPS/scripts/carla_offroad_collect.py"
$launchSh = "/mnt/c/Users/xeno1/MLOPS/scripts/_launch_collect.sh"

function Test-Port([string]$HostName, [int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1500, $false)
        if ($ok -and $c.Connected) { $c.EndConnect($iar); $c.Close(); return $true }
        try { $c.Close() } catch {}
        return $false
    } catch { return $false }
}

if (-not $SkipServer) {
    if (-not (Test-Port "127.0.0.1" 2000)) {
        Write-Host "Starting CARLA server..."
        & (Join-Path $Root "scripts\start-carla.ps1") -Mode window -NoGuiHint
    } else {
        Write-Host "CARLA already listening on :2000"
    }
}

# wait ready
$ready = $false
for ($i = 1; $i -le 40; $i++) {
    if (Test-Port "127.0.0.1" 2000) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw "CARLA :2000 not ready" }

Write-Host ""
Write-Host "Map=$Map  Vehicle=vehicle.tesla.cybertruck"
Write-Host "Sensors: RGB x6, LiDAR x3, GNSS, IMU"
Write-Host "Opening drive+record window (WASD / B=record / ESC=quit)..."
Write-Host "Bags -> Autoware_e2e/datasets/carla_bags/<run_id>/"
Write-Host "Docs: https://carla.readthedocs.io/en/latest/map_town01/"
Write-Host ""

wsl -d $distro -- bash -lc "sed -i 's/\r`$//' $launchSh $collect; chmod +x $launchSh" | Out-Null
$envArgs = if ($NoSync) { "NOSYNC=1 " } else { "" }
Start-Process -FilePath "wsl.exe" -ArgumentList @(
    "-d", $distro, "--", "bash", "-lc", "${envArgs}bash $launchSh $Map"
) -WindowStyle Normal

Write-Host "Collector started in another window."
Write-Host "After recording: portal CARLA import, or"
Write-Host "  python -m ugv_mlops.cli carla-import datasets/carla_bags/<run_id>"
