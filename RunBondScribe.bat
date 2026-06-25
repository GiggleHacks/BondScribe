@echo off
REM ============================================================
REM  RunBondScribe — the one file you double-click.
REM  It checks for everything BondScribe needs, installs anything
REM  missing the first time, then starts the app. Run it every time.
REM ============================================================
cd /d "%~dp0"
title BondScribe
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo BondScribe exited with an error. See bondscribe-setup.log
  pause
)
