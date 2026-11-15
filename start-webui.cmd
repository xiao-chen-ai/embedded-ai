@echo off
chcp 65001 >nul
cd /d %~dp0
set PYTHONIOENCODING=utf-8
uv run python -m webui
pause
