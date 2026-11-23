# CARLA / Autoware rosbag2 → MLOPS data/inbox
# Usage:
#   .\scripts\import-carla-bag.ps1 -Source "datasets/carla_bags/mine_001"
#   .\scripts\import-carla-bag.ps1 -Source "C:\Users\...\Autoware_e2e\datasets\carla_bags\mine_001" -MissionId "M-CARLA-mine-001"

param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$MissionId = "",
    [string]$Catalog = "http://127.0.0.1:8000"
)

$body = @{ source = $Source }
if ($MissionId) { $body.mission_id = $MissionId }

$started = Invoke-RestMethod -Method POST "$Catalog/carla/import" `
    -ContentType "application/json" `
    -Body ($body | ConvertTo-Json)
Write-Host ($started | ConvertTo-Json -Depth 5)
if (-not $started.job_id) { exit 0 }

$id = $started.job_id
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 2
    $job = Invoke-RestMethod "$Catalog/jobs/$id"
    Write-Host ("job {0}: {1}" -f $id, $job.status)
    if ($job.status -eq "completed" -or $job.status -eq "failed") {
        Write-Host ($job | ConvertTo-Json -Depth 8)
        if ($job.status -ne "completed") { exit 1 }
        exit 0
    }
}
Write-Host "timeout waiting for job $id"
exit 1
