# CARLA シミュ起動の準備（WSL に 0.9.15 を展開し .env を書く）
# 例:
#   .\scripts\setup-carla.ps1              # チェック + .env 準備
#   .\scripts\setup-carla.ps1 -Download    # 本体 DL（数 GB〜）
#   .\scripts\setup-carla.ps1 -Download -StartAfter
param(
    [switch]$Download,
    [switch]$StartAfter,
    [string]$CarlaRootWsl = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = "src"

if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    Write-Error "WSL が必要です。管理者で wsl --install を実行してください。"
}

$wslHome = (wsl bash -lc 'printf %s "$HOME"').Trim()
if ([string]::IsNullOrWhiteSpace($CarlaRootWsl)) {
    $CarlaRootWsl = "$wslHome/CARLA"
}
Write-Host "CARLA_ROOT (WSL) = $CarlaRootWsl"

# .env に CARLA 設定を upsert
$envPath = Join-Path $Root ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $Root ".env.example") $envPath
}
$raw = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
if ($null -eq $raw) { $raw = "" }

function Set-EnvKey([string]$text, [string]$key, [string]$value) {
    $line = "$key=$value"
    if ($text -match "(?m)^#?\s*$key\s*=") {
        return [regex]::Replace($text, "(?m)^#?\s*$key\s*=.*$", $line)
    }
    if ($text -notmatch "\n$") { $text += "`n" }
    return $text + $line + "`n"
}

$raw = Set-EnvKey $raw "CARLA_ROOT" $CarlaRootWsl
$raw = Set-EnvKey $raw "CARLA_LAUNCH_MODE" "wsl"
$raw = Set-EnvKey $raw "CARLA_HOST" "127.0.0.1"
$raw = Set-EnvKey $raw "CARLA_PORT" "2000"
$raw = Set-EnvKey $raw "CARLA_QUALITY" "Low"
$raw = Set-EnvKey $raw "CARLA_MINING_EXTRA_ARGS" "--sync"
Set-Content -Path $envPath -Value $raw -Encoding UTF8
$env:CARLA_ROOT = $CarlaRootWsl
$env:CARLA_LAUNCH_MODE = "wsl"
Write-Host "Updated .env CARLA_*"

# Autoware bag 出力先
$aw = "C:/Users/xeno1/Autoware_e2e"
if (Test-Path (Join-Path $Root ".env")) {
    $m = Select-String -Path $envPath -Pattern '^\s*AUTOWARE_E2E_ROOT\s*=\s*(.+)$' | Select-Object -First 1
    if ($m) { $aw = $m.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'") }
}
$bag = Join-Path $aw "datasets\carla_bags"
New-Item -ItemType Directory -Force -Path $bag | Out-Null
Write-Host "bag dir: $bag"

# WSL スクリプトを LF で実行（wslpath は / 区切りが安全）
$shWin = (Join-Path $Root "scripts\setup-carla-wsl.sh") -replace '\\', '/'
$shWsl = (wsl wslpath -a "$shWin").Trim()
if ([string]::IsNullOrWhiteSpace($shWsl)) {
    $shWsl = "/mnt/c/Users/xeno1/MLOPS/scripts/setup-carla-wsl.sh"
}
wsl bash -lc "sed -i 's/\r$//' '$shWsl' 2>/dev/null; chmod +x '$shWsl'"

$arg = ""
if ($Download) { $arg = "--download" }
Write-Host "Running WSL setup ($arg)…"
wsl bash -lc "export CARLA_ROOT='$CarlaRootWsl'; bash '$shWsl' $arg"
$wslExit = $LASTEXITCODE

Write-Host ""
Write-Host "=== prepare checklist ==="
python -m ugv_mlops.cli carla-prepare
if ($LASTEXITCODE -ne 0 -and $wslExit -ne 0) {
    Write-Host "setup 未完了 (exit=$wslExit)。-Download や Mine_01 配置を確認してください。"
}

if ($StartAfter -and $wslExit -eq 0) {
    Write-Host "Starting CARLA stack…"
    & (Join-Path $Root "scripts\carla-launch.ps1") -Component stack -WaitReadySec 90
}

Write-Host ""
Write-Host "次:"
Write-Host "  1. Mine_01 未配置なら Import してから mining_sim"
Write-Host "  2. .\scripts\carla-launch.ps1 -Component server -WaitReadySec 60"
Write-Host "  3. .\scripts\carla-launch.ps1 -Component mining_sim"
Write-Host "  4. ポータル工程1 または host worker 経由でも可"
