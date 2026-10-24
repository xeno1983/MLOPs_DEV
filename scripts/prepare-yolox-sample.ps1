# Download COCO128 + YOLOX-Nano weights into data/inbox and data/models.
# The worker image has the downloader; host Python is not required.
Set-Location $PSScriptRoot\..
docker compose exec worker python -m ugv_mlops.cli prepare-yolox --max-images 48
Write-Output "inbox: data\inbox\M-COCO128"
Write-Output "weights: data\models\yolox_nano.pth"
