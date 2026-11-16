# Windows から CARLA を起動し、到達確認する
#   .\scripts\start-carla.ps1
param(
    [ValidateSet("window", "offscreen", "vulkan")]
    [string]$Mode = "window",
    [switch]$NoGuiHint
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$runSh = "/mnt/c/Users/xeno1/MLOPS/scripts/run-carla-wsl.sh"

Write-Host "Starting CARLA in WSL (MODE=$Mode)..."
wsl -d Ubuntu -- bash -lc "sed -i 's/\r`$//' $runSh; chmod +x $runSh; export DISPLAY=:0; CARLA_MODE=$Mode bash $runSh"
if ($LASTEXITCODE -ne 0) {
    Write-Host "launch script reported failure — checking anyway..."
}

$wslIp = ((wsl -d Ubuntu -- hostname -I).Trim() -split '\s+')[0]
Start-Sleep -Seconds 2

function Test-Port([string]$HostName, [int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000, $false)
        if ($ok -and $c.Connected) { $c.EndConnect($iar); $c.Close(); return $true }
        try { $c.Close() } catch {}
        return $false
    } catch { return $false }
}

$localOk = Test-Port "127.0.0.1" 2000
$wslOk = Test-Port $wslIp 2000
$running = (wsl -d Ubuntu -- bash -lc "pgrep -c -f CarlaUE4-Linux-Shipping || true").Trim()

Write-Host ""
Write-Host "CARLA process count: $running"
Write-Host "WSL IP:              $wslIp"
Write-Host "127.0.0.1:2000     -> $localOk"
Write-Host "${wslIp}:2000 -> $wslOk"
Write-Host ""

if (-not $wslOk -and -not $localOk) {
    Write-Host "FAILED: port 2000 is not accepting connections."
    wsl -d Ubuntu -- bash -lc "tail -n 40 /tmp/carla-ue4.log | strings"
    exit 1
}

if (-not $NoGuiHint) {
    Write-Host "画面について:"
    Write-Host "  - CarlaUE4 の 3D ウィンドウは WSLg 経由で Windows に出ます（タスクバーも確認）。"
    Write-Host "  - ブラウザで http://127.0.0.1:2000 を開いても画面は出ません（RPC ポート）。"
    Write-Host "  - 操作 UI が欲しければ mining_sim を別起動します。"
}

if (-not $localOk -and $wslOk) {
    Write-Host ""
    Write-Host "localhost 転送が効いていません。クライアントは次を使ってください:"
    Write-Host "  host=$wslIp  port=2000"
    Write-Host "恒久対策（管理者 PowerShell）:"
    Write-Host "  netsh interface portproxy add v4tov4 listenport=2000 listenaddress=127.0.0.1 connectport=2000 connectaddress=$wslIp"
    Write-Host "  netsh interface portproxy add v4tov4 listenport=2001 listenaddress=127.0.0.1 connectport=2001 connectaddress=$wslIp"
    Write-Host "または %USERPROFILE%\.wslconfig に networkingMode=mirrored を書き wsl --shutdown"
}

exit 0
