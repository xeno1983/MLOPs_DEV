# Shared helpers: run ROS 2 / bash scripts from Windows via WSL.
# Dot-source from other launchers:
#   . "$PSScriptRoot\Invoke-WslRos2.ps1"
#   Invoke-WslRos2 -ScriptPath "scripts\carla-odom-bridge.py" -Args @("--host","127.0.0.1")
#   Start-WslRos2Window -Title "odom" -BashCommand "python3 ..."

function ConvertTo-WslPath([string]$WinPath) {
    $full = [IO.Path]::GetFullPath($WinPath)
    if ($full -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $rest = ($Matches[2] -replace '\\', '/')
        return "/mnt/$drive/$rest"
    }
    return ($WinPath -replace '\\', '/')
}

function Test-WslDistroName([string]$Name) {
    if (-not $Name) { return $false }
    & wsl.exe -d $Name -- echo ok *> $null
    return ($LASTEXITCODE -eq 0)
}

function Get-MLopsWslDistro([string]$Preferred = "") {
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    if ($env:MLOPS_WSL_DISTRO) { $candidates += $env:MLOPS_WSL_DISTRO }
    $candidates += @("Ubuntu-24.04", "Ubuntu", "Ubuntu-22.04")
    try {
        $lxss = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss"
        if (Test-Path $lxss) {
            Get-ChildItem $lxss | ForEach-Object {
                $n = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DistributionName
                if ($n) { $candidates += $n }
            }
        }
    } catch { }
    $seen = @{}
    foreach ($name in $candidates) {
        if (-not $name -or $seen.ContainsKey($name)) { continue }
        if ($name -match "docker-desktop") { continue }
        $seen[$name] = $true
        if (Test-WslDistroName $name) { return $name }
    }
    throw "WSL Ubuntu 系ディストリがありません。wsl --install -d Ubuntu-24.04"
}

function Read-MLopsDotEnv([string]$Root, [string]$Key) {
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

function Get-MLopsAutowareRoot([string]$Root) {
    $fromEnv = $env:AUTOWARE_E2E_ROOT
    if (-not $fromEnv) { $fromEnv = Read-MLopsDotEnv $Root "AUTOWARE_E2E_ROOT" }
    if ($fromEnv) {
        $p = $fromEnv.Replace("/", "\")
        if ([IO.Path]::IsPathRooted($p)) { return $p }
        return (Join-Path $Root $p)
    }
    $fallback = "C:\Users\xeno1\Autoware_e2e"
    if (Test-Path $fallback) { return $fallback }
    return $null
}

function Get-MLopsCarlaRootHint([string]$Root) {
    $v = $env:CARLA_ROOT
    if (-not $v) { $v = Read-MLopsDotEnv $Root "CARLA_ROOT" }
    if ($v) { return $v }
    return "~/CARLA"
}

function Get-Ros2SourceSnippet {
    return @'
if [ -z "${AMENT_PREFIX_PATH:-}" ]; then
  for setup in /opt/ros/jazzy/setup.bash /opt/ros/humble/setup.bash; do
    if [ -f "$setup" ]; then
      set +u
      # shellcheck disable=SC1090
      source "$setup"
      set -u
      break
    fi
  done
fi
if [ -z "${AMENT_PREFIX_PATH:-}" ]; then
  echo "ROS 2 setup.bash が見つかりません（jazzy/humble）。" >&2
  exit 2
fi
'@
}

function Get-CarlaEggSnippet([string]$CarlaRootHint) {
    # CarlaRootHint may be WSL path (~/CARLA) or Windows path.
    $hint = $CarlaRootHint
    if ($hint -match '^[A-Za-z]:\\') {
        $hint = ConvertTo-WslPath $hint
    }
    $hintEsc = $hint.Replace("'", "'\''")
    return @"
CARLA_ROOT=`${CARLA_ROOT:-$hintEsc}
EGG=`$(ls -1 "`${CARLA_ROOT}/PythonAPI/carla/dist/carla-"*.egg 2>/dev/null | head -1 || true)
if [ -n "`$EGG" ]; then
  export PYTHONPATH="`$EGG`${PYTHONPATH:+:`$PYTHONPATH}"
fi
"@
}

function Repair-WslScriptLineEndings {
    param(
        [Parameter(Mandatory)][string]$Distro,
        [Parameter(Mandatory)][string[]]$WslPaths
    )
    foreach ($p in $WslPaths) {
        if (-not $p) { continue }
        & wsl.exe -d $Distro -- sed -i "s/\r`$//" $p 2>$null
        & wsl.exe -d $Distro -- chmod +x $p 2>$null
    }
}

function Build-WslRos2Bash {
    param(
        [Parameter(Mandatory)][string]$InnerCommand,
        [string]$CarlaRootHint = "",
        [switch]$NeedCarlaEgg,
        [switch]$Interactive
    )
    $parts = New-Object System.Collections.Generic.List[string]
    $parts.Add('export DISPLAY="${DISPLAY:-:0}"')
    $parts.Add('export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"')
    $parts.Add((Get-Ros2SourceSnippet))
    if ($NeedCarlaEgg) {
        $parts.Add((Get-CarlaEggSnippet $CarlaRootHint))
    }
    $parts.Add($InnerCommand)
    if ($Interactive) {
        $parts.Add('exec bash')
    }
    return ($parts -join "`n")
}

function ConvertTo-WslBashB64([string]$Body) {
    $unix = ($Body -replace "`r`n", "`n" -replace "`r", "`n")
    $bytes = [Text.Encoding]::UTF8.GetBytes($unix)
    return [Convert]::ToBase64String($bytes)
}

function Invoke-WslRos2 {
    param(
        [Parameter(Mandatory)][string]$BashCommand,
        [string]$Distro = "",
        [string]$CarlaRootHint = "",
        [switch]$NeedCarlaEgg,
        [switch]$Interactive
    )
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "wsl.exe が見つかりません。"
    }
    $d = Get-MLopsWslDistro $Distro
    $body = Build-WslRos2Bash -InnerCommand $BashCommand -CarlaRootHint $CarlaRootHint `
        -NeedCarlaEgg:$NeedCarlaEgg -Interactive:$Interactive
    # base64 avoids PowerShell / wsl quoting eating $vars and newlines
    $b64 = ConvertTo-WslBashB64 $body
    & wsl.exe -d $d -- bash -lc "echo $b64 | base64 -d | bash" | ForEach-Object { [Console]::WriteLine($_) }
    $code = $LASTEXITCODE
    return $code
}

function Start-WslRos2Window {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$BashCommand,
        [string]$Distro = "",
        [string]$CarlaRootHint = "",
        [switch]$NeedCarlaEgg
    )
    $d = Get-MLopsWslDistro $Distro
    $body = Build-WslRos2Bash -InnerCommand $BashCommand -CarlaRootHint $CarlaRootHint `
        -NeedCarlaEgg:$NeedCarlaEgg
    $b64 = ConvertTo-WslBashB64 $body
    $wslCmd = "echo $b64 | base64 -d | bash"
    Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $d, "--", "bash", "-lc", $wslCmd) `
        -WindowStyle Normal
    Write-Host "別ウィンドウで起動: $Title (WSL $d)"
}
