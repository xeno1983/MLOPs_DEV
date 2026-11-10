# Windows デスクトップから MLOPS スクリプト / ROS 2 ノードを起動する。
#   .\launcher\MLOPS_ros2.bat
#   powershell -File .\launcher\MLOPS_ros2.ps1
chcp.com 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "docker-compose.yml"))) {
    throw "docker-compose.yml が見つかりません: $Root"
}
Set-Location $Root
$Host.UI.RawUI.WindowTitle = "UGV MLOps ROS2"

. (Join-Path $Root "scripts\Invoke-WslRos2.ps1")

function Pause-Menu {
    Write-Host ""
    Read-Host "Enter でメニューに戻る"
}

function Get-Roots {
    $script:AutowareRoot = Get-MLopsAutowareRoot $Root
    $script:CarlaHint = Get-MLopsCarlaRootHint $Root
    $script:Distro = Get-MLopsWslDistro
    $script:WslMlops = ConvertTo-WslPath $Root
    if ($script:AutowareRoot) {
        $script:WslAutoware = ConvertTo-WslPath $script:AutowareRoot
    } else {
        $script:WslAutoware = ""
    }
}

function Show-Env {
    Get-Roots
    Write-Host "MLOPS      $Root"
    Write-Host "WSL        $Distro"
    Write-Host "Autoware   $(if ($AutowareRoot) { $AutowareRoot } else { '(未設定)' })"
    Write-Host "CARLA_ROOT $CarlaHint"
    Write-Host ""
    Write-Host "ROS 2 / トピック確認..."
    $code = Invoke-WslRos2 -Distro $Distro -BashCommand @'
echo "ROS_DISTRO=${ROS_DISTRO:-?}  AMENT=${AMENT_PREFIX_PATH:+set}"
ros2 --version 2>/dev/null || true
echo "--- topics (timeout 3s) ---"
timeout 3 ros2 topic list 2>/dev/null || echo "(no topics / DDS 未起動)"
'@
    if ($code -ne 0) { Write-Host "exit=$code" }
}

function Start-CarlaServer {
    & (Join-Path $Root "scripts\start-carla.ps1")
}

function Start-HostAgent {
    $agent = Join-Path $Root "scripts\carla-host-agent.ps1"
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $agent
    ) -WindowStyle Normal
    Write-Host "CARLA host agent を別窓で起動しました。"
}

function Start-MiningSim {
    Write-Host "mining_sim を起動します（CARLA :2000 が必要）..."
    & (Join-Path $Root "scripts\carla-launch.ps1") -Component mining_sim
}

function Start-OdomBridge {
    & (Join-Path $Root "launcher\ros2\start-odom-bridge.ps1") -NoPause
}

function Start-BagRecord {
    & (Join-Path $Root "launcher\ros2\start-bag-record.ps1") -NoPause
}

function Start-AutoE2eNode {
    $build = Read-Host "colcon build する? (y/N)"
    if ($build -match '^[yY]') {
        & (Join-Path $Root "launcher\ros2\start-autoe2e-infer.ps1") -Build -NoPause
    } else {
        & (Join-Path $Root "launcher\ros2\start-autoe2e-infer.ps1") -NoPause
    }
}

function Start-Ros2Shell {
    & (Join-Path $Root "launcher\ros2\start-ros2-shell.ps1") -NoPause
}

function Invoke-Ros2TopicList {
    Get-Roots
    Invoke-WslRos2 -Distro $Distro -BashCommand "ros2 topic list" | Out-Null
}

function Start-GpuWorker {
    $bat = Join-Path $Root "launcher\MLOPS_gpu_worker.bat"
    if (-not (Test-Path $bat)) {
        Write-Host "missing $bat — run scripts\setup-gpu-worker.ps1"
        return
    }
    Start-Process $bat
    Write-Host "GPU host worker を起動しました。"
}

function Start-SetupAutoE2e {
    & (Join-Path $Root "scripts\setup-autoe2e.ps1")
}

function Start-Portforward {
    $pf = Join-Path $Root "scripts\carla-portforward.ps1"
    if (-not (Test-Path $pf)) {
        Write-Host "missing $pf"
        return
    }
    Write-Host "管理者権限が必要な場合があります。"
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $pf
    ) -ErrorAction SilentlyContinue
}

function Run-CustomScript {
    Get-Roots
    Write-Host "例: scripts/diag-carla-wsl.sh"
    Write-Host "例: ros2 run demo_nodes_cpp talker"
    $cmd = Read-Host "WSL 上で実行するコマンド"
    if ([string]::IsNullOrWhiteSpace($cmd)) { return }
    $egg = Read-Host "CARLA egg を PYTHONPATH に入れる? (y/N)"
    $need = ($egg -match '^[yY]')
    Start-WslRos2Window -Title "custom" -Distro $Distro -CarlaRootHint $CarlaHint `
        -NeedCarlaEgg:$need -BashCommand "cd '$WslMlops'; $cmd; echo; echo exit=`$?; exec bash"
}

function Install-DesktopShortcuts {
    $installer = Join-Path $Root "scripts\install-desktop-launchers.ps1"
    & $installer
}

function Start-VehicleAutoE2e {
    $env:PYTHONPATH = "src"
    Write-Host ""
    Write-Host "  1  inspect（トピック確認）"
    Write-Host "  2  prepare（rig + GNSS→odom）"
    Write-Host "  3  build（scene 切出し → dataset）"
    $pick = Read-Host "番号"
    $mid = Read-Host "mission_id"
    if ([string]::IsNullOrWhiteSpace($mid)) { Write-Host "キャンセル"; return }
    switch ($pick) {
        "1" { python -m ugv_mlops.cli autoe2e-inspect -m $mid }
        "2" { python -m ugv_mlops.cli autoe2e-prepare -m $mid }
        "3" { python -m ugv_mlops.cli autoe2e-build -m $mid }
        default { Write-Host "キャンセル" }
    }
}

Get-Roots

while ($true) {
    Write-Host ""
    Write-Host " UGV MLOps — スクリプト / ROS 2（Windows → WSL）"
    Write-Host " $Root"
    Write-Host " WSL: $Distro"
    Write-Host ""
    Write-Host "  1  環境確認（ROS / topic list）"
    Write-Host "  2  CARLA 起動（WSL）"
    Write-Host "  3  CARLA host agent"
    Write-Host "  4  mining_sim 起動"
    Write-Host "  5  odom bridge（ROS2 ノード）"
    Write-Host "  6  AutoE2E bag 録画（odom+ros2 bag record）"
    Write-Host "  7  AutoE2E 推論ノード（Autoware ROS2）"
    Write-Host "  8  ROS2 シェル（別窓）"
    Write-Host "  9  ros2 topic list"
    Write-Host "  g  GPU host worker"
    Write-Host "  a  setup-autoe2e.ps1"
    Write-Host "  v  自前 bag → AutoE2E（inspect / prepare）"
    Write-Host "  p  CARLA portforward（管理者）"
    Write-Host "  x  任意コマンドを WSL+ROS2 で実行"
    Write-Host "  d  デスクトップにショートカットを置く"
    Write-Host "  0  終了"
    Write-Host ""
    $choice = Read-Host "番号"
    try {
        switch ($choice) {
            "1" { Show-Env; Pause-Menu }
            "2" { Start-CarlaServer; Pause-Menu }
            "3" { Start-HostAgent; Pause-Menu }
            "4" { Start-MiningSim; Pause-Menu }
            "5" { Start-OdomBridge; Pause-Menu }
            "6" { Start-BagRecord; Pause-Menu }
            "7" { Start-AutoE2eNode; Pause-Menu }
            "8" { Start-Ros2Shell; Pause-Menu }
            "9" { Invoke-Ros2TopicList; Pause-Menu }
            "g" { Start-GpuWorker; Pause-Menu }
            "G" { Start-GpuWorker; Pause-Menu }
            "a" { Start-SetupAutoE2e; Pause-Menu }
            "A" { Start-SetupAutoE2e; Pause-Menu }
            "v" { Start-VehicleAutoE2e; Pause-Menu }
            "V" { Start-VehicleAutoE2e; Pause-Menu }
            "p" { Start-Portforward; Pause-Menu }
            "P" { Start-Portforward; Pause-Menu }
            "x" { Run-CustomScript; Pause-Menu }
            "X" { Run-CustomScript; Pause-Menu }
            "d" { Install-DesktopShortcuts; Pause-Menu }
            "D" { Install-DesktopShortcuts; Pause-Menu }
            "0" { exit 0 }
            default { Write-Host "番号を選び直してください。" }
        }
    } catch {
        Write-Host $_
        Pause-Menu
    }
    Get-Roots
}
