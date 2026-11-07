@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0MLOPS_gpu_worker.ps1"
if errorlevel 1 (
    echo.
    echo The GPU worker stopped. This window stays open so you can read the message above.
    pause
)
