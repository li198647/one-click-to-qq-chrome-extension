@echo off
rem ============================================================
rem  Native messaging host entry for the "one click to QQ" extension.
rem
rem  Chrome cannot run local programs directly, so the extension talks
rem  to this file instead. Chrome wraps any non-.exe host with cmd.exe
rem  and speaks to it over stdin/stdout using a 4-byte little-endian
rem  length prefix followed by UTF-8 JSON.
rem
rem  TWO RULES, both easy to break by accident:
rem   1. Never print anything to stdout. One stray byte (an echo, a
rem      banner, a prompt) corrupts the protocol and the extension sees
rem      a broken response. Errors go to log\native_host.log instead.
rem   2. Keep this file ASCII-only. cmd.exe reads batch files using the
rem      OEM code page (936 = GBK on this machine), so any non-ASCII
rem      text here would come out garbled.
rem
rem  Chrome also appends its own arguments (e.g. --parent-window=0).
rem  They are deliberately ignored: this file does not forward "%*".
rem  Written by WorkBuddy.
rem ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  if not exist "%~dp0log" mkdir "%~dp0log"
  echo [ERROR] python not found: %PY% 1>>"%~dp0log\native_host.log" 2>&1
  exit /b 1
)

"%PY%" qq_native_host.py
exit /b %ERRORLEVEL%
