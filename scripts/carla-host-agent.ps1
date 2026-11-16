# ポータル工程1の CARLA 起動依頼（data/work/carla/request.json）をホストで実行する。
# Catalog は Docker 内のため、この agent か GPU host worker が必要。
#   .\scripts\carla-host-agent.ps1
#   .\scripts\carla-host-agent.ps1 -Once
param(
    [switch]$Once,
    [int]$PollSeconds = 2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = "src"
$req = Join-Path $Root "data\work\carla\request.json"

Write-Host "CARLA host agent"
Write-Host "  watching $req"
Write-Host "  stop: Ctrl+C"
Write-Host ""

function Invoke-CarlaRequest {
    $env:CARLA_ROOT = if ($env:CARLA_ROOT) { $env:CARLA_ROOT } else { "/home/xeno1/CARLA" }
    python -c @"
from ugv_mlops.carla_launch import process_host_request, read_host_request, probe_carla, write_host_request
import json
req = read_host_request()
if not req:
    print(json.dumps({'idle': True}))
elif req.get('status') in ('queued', 'pending', None, 'failed', 'running'):
    # running のまま残った依頼も再実行
    if req.get('status') == 'running':
        write_host_request(req.get('action') or 'launch', req.get('components') or ['server'])
    print(json.dumps(process_host_request() or {}, default=str))
else:
    print(json.dumps({'idle': True, 'status': req.get('status'), 'probe': probe_carla()}, default=str))
"@
}

while ($true) {
    if (Test-Path $req) {
        try {
            $raw = Get-Content $req -Raw -ErrorAction SilentlyContinue
            if ($raw -match '"status"\s*:\s*"(queued|pending)"') {
                Write-Host "$(Get-Date -Format HH:mm:ss) request detected — launching…"
                $out = Invoke-CarlaRequest
                Write-Host $out
            }
        } catch {
            Write-Host "error: $_"
        }
    }
    if ($Once) { break }
    Start-Sleep -Seconds $PollSeconds
}
