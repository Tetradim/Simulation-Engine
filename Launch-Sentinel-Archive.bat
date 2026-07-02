@echo off
setlocal
title Sentinel Archive
cd /d "%~dp0"

if not exist "%~dp0Launch-Sentinel-Archive.ps1" (
  echo.
  echo Sentinel Archive could not find Launch-Sentinel-Archive.ps1.
  echo Please extract the full Sentinel Archive folder, or reinstall with SentinelArchive-Setup.
  echo Send this screenshot to Sentinel Archive support if the problem continues.
  pause
  exit /b 2
)

set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL%" (
  where powershell.exe >nul 2>nul
  if errorlevel 1 (
    echo.
    echo PowerShell was not found. Sentinel Archive needs Windows PowerShell to start and repair missing dependencies.
    echo Please send this screenshot to Sentinel Archive support.
    pause
    exit /b 9009
  )
  set "POWERSHELL=powershell.exe"
)

"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-Sentinel-Archive.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Sentinel Archive launcher exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
