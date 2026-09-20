@echo off
chcp 65001 >nul
title QQ Bridge - local relay for browser clipboard
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist "%PY%" (
  echo [ERROR] Python interpreter not found:
  echo   %PY%
  echo Tell WorkBuddy to reinstall the runtime.
  pause
  exit /b 1
)

echo ============================================================
echo   QQ Bridge starting...
echo   Keep this window OPEN while you use the browser extension.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.

"%PY%" qq_bridge.py

echo.
echo QQ Bridge has stopped.
pause >nul
