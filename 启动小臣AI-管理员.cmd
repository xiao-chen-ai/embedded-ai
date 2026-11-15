@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PATH=%USERPROFILE%\.local\bin;C:\Windows\system32;C:\Windows;%PATH%"
title Xiaochen-AI (Admin)

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo   Xiaochen-AI starting (admin mode)...
echo   Keep this window open = service running
echo ============================================
echo.
where uv >nul 2>&1
if not %errorlevel%==0 (
    echo Installing uv...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
)
uv sync
start "" http://127.0.0.1:8788
uv run python -m webui --port 8788
pause
