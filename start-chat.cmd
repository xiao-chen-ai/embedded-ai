@echo off
rem ============================================================
rem  Trae Embedded Agent 一键启动（交互模式）
rem  窗口里直接输入任务，Ctrl+C 退出
rem ============================================================
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo.
echo   ==============================================
echo    Trae Embedded Agent - 嵌入式 AI 编码系统
echo    模型: 硅基流动 zai-org/GLM-5.3 (trae_config.yaml 可切换)
echo    技能: 39 个已注入 (mp 27 + zh 12)
echo    工具: 网表分析 / pinmap / 技能检索 / 技能脚本 / bash / 编辑器
echo   ==============================================
echo.
uv run trae-cli interactive --config-file trae_config.yaml
pause
