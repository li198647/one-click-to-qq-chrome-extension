@echo off
rem ============================================================
rem  Re-register the native messaging host.
rem
rem  Double-click this only when the extension says the host is not
rem  registered -- e.g. you moved the bridge folder somewhere else, or
rem  the registry entry got wiped by a cleaner tool. Normally the bridge
rem  registers itself every time it starts, so you never need this file.
rem
rem  ASCII-only on purpose (see the note in qq_host.bat).
rem  Written by WorkBuddy.
rem ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERROR] Python interpreter not found:
  echo   %PY%
  goto :end
)

"%PY%" qq_native_host.py --register

:end
echo.
pause >nul
