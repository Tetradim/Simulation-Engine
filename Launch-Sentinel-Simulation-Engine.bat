@echo off
setlocal
title Sentinel Simulation Engine
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-Sentinel-Simulation-Engine.ps1"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Sentinel Simulation Engine launcher exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
