# デスクトップ「起動スクリプト\MLOPS」に bat を置く
#   .\scripts\install-desktop-launchers.ps1
param(
    [string]$DesktopRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $DesktopRoot) {
    $candidates = @(
        (Join-Path $env:USERPROFILE "Desktop\起動スクリプト\MLOPS"),
        (Join-Path $env:USERPROFILE "OneDrive\Desktop\起動スクリプト\MLOPS")
    )
    foreach ($c in $candidates) {
        $parent = Split-Path $c -Parent
        if ((Test-Path $parent) -or (Test-Path (Split-Path $parent -Parent))) {
            $DesktopRoot = $c
            break
        }
    }
    if (-not $DesktopRoot) {
        $DesktopRoot = Join-Path $env:USERPROFILE "Desktop\起動スクリプト\MLOPS"
    }
}

New-Item -ItemType Directory -Force -Path $DesktopRoot | Out-Null

function Write-CallBat([string]$Name, [string]$Target) {
    $dest = Join-Path $DesktopRoot $Name
    if ($Target.EndsWith(".bat", [StringComparison]::OrdinalIgnoreCase)) {
        @"
@echo off
cd /d "$Root"
call "$Target" %*
"@ | Set-Content -LiteralPath $dest -Encoding ASCII
    } else {
        @"
@echo off
chcp 65001 >nul
cd /d "$Root"
powershell -NoProfile -ExecutionPolicy Bypass -File "$Target" %*
"@ | Set-Content -LiteralPath $dest -Encoding ASCII
    }
    Write-Host "wrote $dest"
}

Write-CallBat "MLOPS起動.bat" (Join-Path $Root "launcher\MLOPS_run.bat")
Write-CallBat "MLOPS_ROS2.bat" (Join-Path $Root "launcher\MLOPS_ros2.bat")
Write-CallBat "MLOPS_GPUワーカー.bat" (Join-Path $Root "launcher\MLOPS_gpu_worker.bat")
Write-CallBat "ROS2_odomブリッジ.bat" (Join-Path $Root "launcher\ros2\start-odom-bridge.ps1")
Write-CallBat "ROS2_bag録画.bat" (Join-Path $Root "launcher\ros2\start-bag-record.ps1")
Write-CallBat "ROS2_シェル.bat" (Join-Path $Root "launcher\ros2\start-ros2-shell.ps1")
Write-CallBat "ROS2_AutoE2E推論.bat" (Join-Path $Root "launcher\ros2\start-autoe2e-infer.ps1")
Write-CallBat "CARLA起動.bat" (Join-Path $Root "scripts\start-carla.ps1")

Write-Host ""
Write-Host "デスクトップ起動先: $DesktopRoot"
