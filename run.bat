@echo off
REM Double-click launcher for Auto-Clipper.
REM Bypasses PowerShell execution policy for this single script run.

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
pause
