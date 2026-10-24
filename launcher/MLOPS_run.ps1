# The .bat is ASCII so cmd can start it from a Japanese folder.
chcp.com 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "docker-compose.yml"))) {
    throw "docker-compose.yml が見つかりません: $Root"
}
Set-Location $Root
$Host.UI.RawUI.WindowTitle = "UGV MLOps"

$Portal = "http://127.0.0.1:8080"
$Catalog = "http://127.0.0.1:8000"
$MLflow = "http://127.0.0.1:5000"
$LabelStudio = "http://127.0.0.1:8081"
$Xtreme1 = "http://127.0.0.1:8190"
$SaltDistro = "Ubuntu-24.04"
$DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$AutowareRoot = Join-Path $Root "data\autoware_stub"
$Xtreme1Dir = Join-Path $AutowareRoot "Xtreme1"
$SaltBin = Join-Path $AutowareRoot "SALT\bin\SALT"
$SaltSh = "/mnt/c/Users/xeno1/Autoware_e2e/ros2/scripts/launch_salt.sh"

function Read-DotEnvValue([string]$Key) {
    $file = Join-Path $Root ".env"
    if (-not (Test-Path $file)) { return $null }
    foreach ($line in Get-Content -LiteralPath $file -Encoding UTF8) {
        $t = $line.Trim()
        if ($t.StartsWith("#") -or ($t.IndexOf("=") -lt 1)) { continue }
        $eq = $t.IndexOf("=")
        if ($t.Substring(0, $eq).Trim() -eq $Key) {
            return $t.Substring($eq + 1).Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function ConvertTo-WslPath([string]$WinPath) {
    $full = [IO.Path]::GetFullPath($WinPath)
    if ($full -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $rest = ($Matches[2] -replace '\\', '/')
        return "/mnt/$drive/$rest"
    }
    return ($WinPath -replace '\\', '/')
}

function Resolve-AnnotatePaths {
    $fromEnv = Read-DotEnvValue "AUTOWARE_E2E_ROOT"
    if ($fromEnv) {
        $p = $fromEnv.Replace("/", "\")
        if ([IO.Path]::IsPathRooted($p)) { $script:AutowareRoot = $p }
        else { $script:AutowareRoot = Join-Path $Root $p }
    }
    $script:Xtreme1Dir = Join-Path $script:AutowareRoot "Xtreme1"
    $script:SaltBin = Join-Path $script:AutowareRoot "SALT\bin\SALT"
    $script:SaltSh = ConvertTo-WslPath (Join-Path $script:AutowareRoot "ros2\scripts\launch_salt.sh")
}

Resolve-AnnotatePaths

function Pause-Menu {
    Write-Host ""
    Read-Host "Enter でメニューに戻る"
}

function Ensure-Env {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host ".env を .env.example から作りました。機密区画ではパスワードを変えてください。"
    }
    New-Item -ItemType Directory -Force -Path "data/inbox", "data/work", "data/keys", "data/annotate", "data/dvc/datasets", "data/dvc/raw/missions", "data/dvc/parsed" | Out-Null
    Resolve-AnnotatePaths
}

function Test-DockerReady {
    $pipe = "\\.\pipe\dockerDesktopLinuxEngine"
    if (-not (Test-Path $pipe)) { return $false }
    cmd /c "docker info >nul 2>&1"
    return ($LASTEXITCODE -eq 0)
}

function Ensure-Docker {
    if (Test-DockerReady) { return }
    if (-not (Test-Path $DockerDesktop)) {
        throw "Docker Desktop が見つかりません: $DockerDesktop"
    }
    Write-Host "Docker Desktop を起動しています。初回は1〜2分かかることがあります..."
    Start-Process $DockerDesktop
    $tries = 90
    for ($i = 1; $i -le $tries; $i++) {
        Start-Sleep -Seconds 2
        if (Test-DockerReady) {
            Start-Sleep -Seconds 2
            Write-Host "Docker が応答しました。"
            return
        }
        if (($i % 5) -eq 0) {
            Write-Host "  Docker 待ち ($i/$tries)"
        }
    }
    throw "Docker エンジンがまだ立ち上がっていません。Docker Desktop の鯨アイコンが止まるまで待って、もう一度 1 を選んでください。"
}

function Wait-Http([string]$Url, [int]$Tries = 40) {
    for ($i = 0; $i -lt $Tries; $i++) {
        try {
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            return $true
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    return $false
}

function Catalog-Get([string]$Path) {
    return Invoke-RestMethod -Uri ($Catalog + $Path) -TimeoutSec 15
}

function Catalog-Post([string]$Path, $Body = $null) {
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method POST -Uri ($Catalog + $Path) -TimeoutSec 30
    }
    $json = $Body | ConvertTo-Json -Compress
    return Invoke-RestMethod -Method POST -Uri ($Catalog + $Path) -ContentType "application/json; charset=utf-8" -Body $json -TimeoutSec 30
}

function Wait-Job([string]$JobId, [int]$Tries = 90) {
    Write-Host "ジョブ $JobId を待っています..."
    for ($i = 0; $i -lt $Tries; $i++) {
        $job = Catalog-Get "/jobs/$JobId"
        $st = [string]$job.status
        if ($st -eq "completed") {
            Write-Host "完了"
            return $job
        }
        if ($st -eq "failed") {
            Write-Host "失敗: $($job.error)"
            return $job
        }
        Start-Sleep -Seconds 3
    }
    Write-Host "まだ running です。ポータルか GET $Catalog/jobs/$JobId で見てください。"
    return $null
}

function Start-Stack([switch]$Full) {
    Ensure-Env
    Ensure-Docker
    if ($Full) {
        Write-Host "拡張プロファイルで起動します..."
        cmd /c "docker compose --profile full up -d"
    } else {
        Write-Host "中核スタックを起動します..."
        cmd /c "docker compose up -d"
    }
    if ($LASTEXITCODE -ne 0) { throw "docker compose up に失敗しました。" }
    Write-Host "Catalog の準備待ち..."
    if (Wait-Http "$Catalog/health" 50) {
        Write-Host "起動しました。 $Portal"
        Start-Process $Portal
    } else {
        Write-Host "コンテナは上げましたが Catalog がまだ応答しません。少し待って 2 を選んでください。"
        docker compose ps
    }
    Start-Xtreme1
}

function Show-Status {
    try {
        $st = Catalog-Get "/status"
        Write-Host ($st | ConvertTo-Json -Depth 6)
    } catch {
        Write-Host "Catalog に繋がりません。先に 1 で起動してください。"
        Write-Host $_
        docker compose ps
        return
    }
    try {
        $pipe = Catalog-Get "/pipeline"
        Write-Host ""
        Write-Host "次: $($pipe.hint)"
    } catch { }
    try {
        $an = Catalog-Get "/annotate"
        Write-Host ""
        Write-Host "アノテ Label Studio: $($an.labelstudio.url)"
        Write-Host "SALT: $($an.salt.ok)  $($an.salt.path)"
        Write-Host "Xtreme1: $($an.xtreme1.ok)  $($an.xtreme1.url)"
    } catch { }
    try {
        $dvc = Catalog-Get "/dvc"
        Write-Host ""
        Write-Host "DVC initialized=$($dvc.initialized) installed=$($dvc.dvc_installed)"
        foreach ($m in @($dvc.missions)) {
            Write-Host "  $($m.id)  $($m.stage)  images=$($m.images) lidar=$($m.lidar_keyframes)"
        }
    } catch { }
}

function Run-Demo {
    Write-Host ""
    Write-Host "  1  知覚分類デモ"
    Write-Host "  2  E2E デモ"
    Write-Host "  3  YOLOX デモ（初回は数分）"
    $pick = Read-Host "番号 [1]"
    if ([string]::IsNullOrWhiteSpace($pick)) { $pick = "1" }
    $path = switch ($pick) {
        "2" { "/demo-e2e" }
        "3" { "/demo-yolox" }
        default { "/demo" }
    }
    try {
        $res = Catalog-Post $path
        $jobId = $res.job_id
        if (-not $jobId) {
            Write-Host ($res | ConvertTo-Json -Depth 6)
            return
        }
        $job = Wait-Job $jobId
        if ($job) { Write-Host ($job | ConvertTo-Json -Depth 6) }
        Start-Process $Portal
    } catch {
        Write-Host "デモを投入できません。スタックが起動しているか確認してください。"
        Write-Host $_
    }
}

function Start-Xtreme1 {
    Resolve-AnnotatePaths
    $compose = Join-Path $Xtreme1Dir "docker-compose.yml"
    if (-not (Test-Path $compose)) {
        Write-Host "Xtreme1 がありません: $Xtreme1Dir"
        return $false
    }
    Ensure-Docker
    Write-Host "Xtreme1 を起動します（Hub へは取りに行きません）..."
    cmd /c "cd /d `"$Xtreme1Dir`" && docker compose up -d --pull never"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Xtreme1 の compose に失敗しました。ログ:"
        cmd /c "cd /d `"$Xtreme1Dir`" && docker compose ps -a"
        return $false
    }
    if (Wait-Http $Xtreme1 40) {
        Write-Host "Xtreme1 起動: $Xtreme1"
        Start-Process $Xtreme1
        return $true
    }
    Write-Host "コンテナは上げましたが $Xtreme1 がまだ応答しません。少し待ってブラウザで開いてください。"
    return $false
}

function Stop-Xtreme1 {
    Resolve-AnnotatePaths
    if (-not (Test-Path (Join-Path $Xtreme1Dir "docker-compose.yml"))) {
        Write-Host "Xtreme1 がありません: $Xtreme1Dir"
        return
    }
    cmd /c "cd /d `"$Xtreme1Dir`" && docker compose stop"
    Write-Host "Xtreme1 を止めました。"
}

function Start-Salt {
    Resolve-AnnotatePaths
    if (-not (Test-Path $SaltBin)) {
        Write-Host "SALT の実行ファイルがありません: $SaltBin"
        Write-Host ".env の AUTOWARE_E2E_ROOT を Autoware_e2e の実体に合わせてください。"
        return
    }
    $names = @(wsl.exe -l -q)
    $have = $false
    foreach ($n in $names) {
        $clean = ($n -replace "`0", "").Trim()
        if ($clean -eq $SaltDistro) { $have = $true }
    }
    if (-not $have) {
        Write-Host "WSL ディストロ $SaltDistro がありません。wsl -l -v を確認してください。"
        return
    }
    $bat = Join-Path $PSScriptRoot "start_salt.bat"
    $env:SALT_DISTRO = $SaltDistro
    $env:SALT_SH = $SaltSh
    Write-Host "SALT を別窓で起動します。その窓を閉じると SALT も終わります。"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$bat`""
}

function Show-Annotate {
    Write-Host ""
    Write-Host "  アノテーション（ingest しただけでは教師は付きません）"
    Write-Host "  1  Label Studio を開く"
    Write-Host "  2  ミッションの画像を annotate フォルダへ出す"
    Write-Host "  3  SALT を起動する（点群・WSL）"
    Write-Host "  4  Xtreme1 を起動する"
    Write-Host "  5  Xtreme1 を止める"
    Write-Host "  6  SALT と Xtreme1 を両方起動"
    Write-Host "  7  アノテ状態"
    $pick = Read-Host "番号"
    switch ($pick) {
        "1" { Start-Process $LabelStudio }
        "2" {
            $mid = Read-Host "mission_id"
            if ([string]::IsNullOrWhiteSpace($mid)) { Write-Host "キャンセル"; return }
            $kind = Read-Host "kind camera/lidar [camera]"
            if ([string]::IsNullOrWhiteSpace($kind)) { $kind = "camera" }
            $res = Catalog-Post "/annotate/init" @{ mission_id = $mid; kind = $kind; limit = 200 }
            Write-Host ($res | ConvertTo-Json -Depth 5)
            if ($res.job_id) { Wait-Job $res.job_id | Out-Null }
        }
        "3" { Start-Salt }
        "4" { Start-Xtreme1 | Out-Null }
        "5" { Stop-Xtreme1 }
        "6" {
            Start-Xtreme1 | Out-Null
            Start-Salt
        }
        "7" {
            try { Write-Host ((Catalog-Get "/annotate") | ConvertTo-Json -Depth 6) }
            catch { Write-Host $_ }
        }
        default { Write-Host "キャンセル" }
    }
}

function Show-Dvc {
    Write-Host ""
    Write-Host "  bag: 取込 → 追跡 → パース → 保管（Garage s3://dvc）"
    Write-Host "  1  状態 / ミッション一覧"
    Write-Host "  2  取込（ディスク上の bag パス）"
    Write-Host "  3  パース（画像・点群.bin・GNSS）"
    Write-Host "  4  追跡（DVC add）"
    Write-Host "  5  保管（push）"
    Write-Host "  6  ミッションを publish（旧）"
    $pick = Read-Host "番号"
    switch ($pick) {
        "1" {
            try { Write-Host ((Catalog-Get "/missions") | ConvertTo-Json -Depth 6) }
            catch { Write-Host $_ }
        }
        "2" {
            $src = Read-Host "bag またはミッションフォルダのパス"
            if ([string]::IsNullOrWhiteSpace($src)) { return }
            $mid = Read-Host "mission_id（空ならフォルダ名）"
            $body = @{ source_path = $src }
            if (-not [string]::IsNullOrWhiteSpace($mid)) { $body.mission_id = $mid }
            Write-Host ((Catalog-Post "/missions/import" $body) | ConvertTo-Json -Depth 5)
        }
        "3" {
            $mid = Read-Host "mission_id"
            if ([string]::IsNullOrWhiteSpace($mid)) { return }
            $res = Catalog-Post "/missions/$mid/parse"
            Write-Host ($res | ConvertTo-Json -Depth 5)
            if ($res.job_id) { Wait-Job $res.job_id | Out-Null }
        }
        "4" {
            $mid = Read-Host "mission_id"
            if ([string]::IsNullOrWhiteSpace($mid)) { return }
            $res = Catalog-Post "/missions/$mid/track"
            Write-Host ($res | ConvertTo-Json -Depth 5)
            if ($res.job_id) { Wait-Job $res.job_id | Out-Null }
        }
        "5" {
            $mid = Read-Host "mission_id"
            if ([string]::IsNullOrWhiteSpace($mid)) { return }
            $res = Catalog-Post "/missions/$mid/store"
            Write-Host ($res | ConvertTo-Json -Depth 5)
            if ($res.job_id) { Wait-Job $res.job_id | Out-Null }
        }
        "6" {
            $mid = Read-Host "mission_id"
            if ([string]::IsNullOrWhiteSpace($mid)) { return }
            $res = Catalog-Post "/dvc/publish" @{ mission_id = $mid; push = $true }
            Write-Host ($res | ConvertTo-Json -Depth 5)
            if ($res.job_id) { Wait-Job $res.job_id | Out-Null }
        }
        default { Write-Host "キャンセル" }
    }
}

while ($true) {
    Write-Host ""
    Write-Host " UGV MLOps"
    Write-Host " $Root"
    Write-Host ""
    Write-Host "  1  スタック起動（中核: bag → アノテ → データセット → MLflow）"
    Write-Host "  2  ポータルを開く          $Portal"
    Write-Host "  3  状態（Catalog / アノテ / DVC）"
    Write-Host "  4  デモ（分類 / E2E / YOLOX）"
    Write-Host "  5  アノテーション（Label Studio / SALT / Xtreme1）"
    Write-Host "  6  DVC"
    Write-Host "  7  拡張起動（LakeFS / Gitea / Grafana）"
    Write-Host "  8  スタック停止（データは残る）"
    Write-Host "  9  終了"
    Write-Host ""
    $choice = Read-Host "番号"
    try {
        switch ($choice) {
            "1" { Start-Stack; Pause-Menu }
            "2" { Start-Process $Portal; Start-Process "$Catalog/docs"; Pause-Menu }
            "3" { Show-Status; Pause-Menu }
            "4" { Run-Demo; Pause-Menu }
            "5" { Show-Annotate; Pause-Menu }
            "6" { Show-Dvc; Pause-Menu }
            "7" { Start-Stack -Full; Pause-Menu }
            "8" {
                docker compose --profile full down
                Stop-Xtreme1
                Write-Host "停止しました。ボリュームは残しています。"
                Pause-Menu
            }
            "9" { exit 0 }
            default { Write-Host "1〜9 を入力してください。" }
        }
    } catch {
        Write-Host $_
        Pause-Menu
    }
}
