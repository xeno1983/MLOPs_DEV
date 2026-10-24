@echo off
REM ASCII helper. Called from MLOPS_run.ps1. Keep this file free of non-ASCII.
if "%SALT_DISTRO%"=="" set "SALT_DISTRO=Ubuntu-24.04"
if "%SALT_SH%"=="" set "SALT_SH=/mnt/c/Users/xeno1/Autoware_e2e/ros2/scripts/launch_salt.sh"
title SALT
echo Starting SALT in WSL %SALT_DISTRO%
echo Close this window to stop SALT.
echo.
wsl.exe -d %SALT_DISTRO% -- bash -lc "export DISPLAY=${DISPLAY:-:0}; exec bash %SALT_SH%"
echo.
echo SALT exited. errorlevel=%ERRORLEVEL%
pause
