# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""Keil MDK 命令行编译/烧录工具（专用，绕过 bash 的 120s 超时与 UV4 单实例死锁）。

为什么不用 bash 跑 UV4：
1. UV4.exe 是 GUI 子系统程序：无控制台管道下 stdout 不产出，bash 会话里的命令
   会一直等 UV4 退出；UV4 单实例语义（已有 IDE/命令行实例在跑时）还会把请求
   转给既有实例并挂起 —— 两边互相等 = 死锁。
2. bash 工具有 120s 硬超时，烧录慢一点就掐断，AI 只能 sleep 轮询日志空转。

本工具直接 Popen + wait(timeout)：启动前确保没有残留 UV4（结束之），运行中
用 wall-clock 超时（build 180s / flash 180s），结果只读 -o 日志文件。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path
from typing import override

from trae_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter

_BUILD_TIMEOUT_S = 180
_FLASH_TIMEOUT_S = 180


def _is_admin() -> bool:
    """当前进程是否管理员（决定杀软行为防护的信任级别）。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _uv4_path(arguments: ToolCallArguments) -> str:
    """从参数取 UV4 路径（前端配置的 tool_paths 会注入 Agent 上下文与提示词）。"""
    p = str(arguments.get("uv4_path", "") or "").strip().strip('"')
    return p


def _kill_stale_uv4() -> str:
    """结束残留的 UV4 进程（单实例程序：不清理会死锁）。返回清理报告。"""
    killed: list[int] = []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UV4.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001
        return ""
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith('"UV4.exe"'):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pid = int(parts[1])
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10
                )
                killed.append(pid)
            except Exception:  # noqa: BLE001
                continue
    return f"（已清理 {len(killed)} 个残留 UV4 进程: {killed}）" if killed else ""


class KeilBuildFlashTool(Tool):
    """UV4 命令行编译（-b）与烧录（-f），带回日志与超时保护。"""

    def __init__(self, model_provider: str | None = None):
        super().__init__(model_provider)

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "keil_build_flash"

    @override
    def get_description(self) -> str:
        return (
            "Build or flash a Keil MDK project via the UV4 command line. This is the ONLY "
            "supported way to build/flash Keil projects — do NOT run UV4 through the bash "
            "tool (it deadlocks). action='build' compiles, action='flash' downloads to the "
            "board using the debugger configured in the project (.uvoptx). Reads the log "
            "file and reports errors/warnings line by line."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="action",
                type="string",
                description="'build' to compile, or 'flash' to program the board.",
                enum=["build", "flash"],
                required=True,
            ),
            ToolParameter(
                name="project",
                type="string",
                description="Absolute path to the .uvprojx project file.",
                required=True,
            ),
            ToolParameter(
                name="uv4_path",
                type="string",
                description=(
                    "Absolute path to UV4.exe (from [Configured tool paths], "
                    "e.g. E:/Keil_v5/UV4/UV4.exe)."
                ),
                required=False,
            ),
            ToolParameter(
                name="log",
                type="string",
                description="Absolute path of the output log file. Default: <OBJ dir of project>/keil_runcmd_log.txt",
                required=False,
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        import asyncio

        action = str(arguments.get("action", "")).strip().lower()
        project_raw = str(arguments.get("project", "")).strip().strip('"')
        uv4 = _uv4_path(arguments)
        if action not in ("build", "flash"):
            return ToolExecResult(error="action 必须是 build 或 flash", error_code=-1)
        if not project_raw:
            return ToolExecResult(error="缺少 project（.uvprojx 绝对路径）", error_code=-1)
        proj = Path(project_raw)
        if not proj.is_file():
            return ToolExecResult(error=f"工程文件不存在: {project_raw}", error_code=-1)
        if not uv4:
            return ToolExecResult(
                error="缺少 uv4_path（取 [Configured tool paths] 里的 Keil 路径，如 E:/Keil_v5/UV4/UV4.exe）",
                error_code=-1,
            )
        if not Path(uv4).is_file():
            return ToolExecResult(error=f"UV4 不存在: {uv4}", error_code=-1)

        log_raw = str(arguments.get("log", "") or "").strip().strip('"')
        if log_raw:
            log = Path(log_raw)
        else:
            obj_dir = proj.parent.parent / "OBJ"
            obj_dir.mkdir(parents=True, exist_ok=True)
            log = obj_dir / "keil_runcmd_log.txt"

        flag = "-b" if action == "build" else "-f"
        timeout_s = _BUILD_TIMEOUT_S if action == "build" else _FLASH_TIMEOUT_S
        # 防杀软竞态：完整产物校验。编译后 axf/hex 缺失（第三方杀软拦 armcc 临时文件的
        # 典型症状：日志说 0 错误但产物不落地）时自动清 OBJ 重试一次。
        cleanup = _kill_stale_uv4()

        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                [uv4, flag, str(proj), "-j0", "-o", str(log)],
                capture_output=True,
                timeout=timeout_s,
            )

        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return ToolExecResult(
                error=(
                    f"UV4 {action} 超过 {timeout_s}s 未完成，已终止 {cleanup}。"
                    "可能原因: 调试器未连接（flash 时）/ 许可证弹窗 / 工程弹窗 / 杀软拖慢。"
                    "请检查板子与调试器连接后重试；或让用户在 Keil 里手动打开一次工程消除首次弹窗。"
                ),
                error_code=-1,
            )
        except FileNotFoundError:
            return ToolExecResult(error=f"无法启动 UV4: {uv4}", error_code=-1)

        code = proc.returncode
        log_text = ""
        try:
            log_text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            try:
                log_text = log.read_text(encoding="gbk", errors="replace")
            except OSError:
                log_text = ""
        tail = log_text.strip().splitlines()[-25:]
        tail_text = "\n".join(tail)

        # === 产物校验与自动重试（杀软竞态防护） ===
        # 编译成功但 OBJ 里没有任何 .axf/.hex → armcc 临时文件被第三方杀软（如
        # 联想电脑管家/火绒）拦截的典型症状。清空 OBJ 目录后重试一次。
        if action == "build" and code in (0, 1):
            obj_dir = proj.parent.parent / "OBJ"
            has_artifact = any(obj_dir.glob("*.axf")) or any(obj_dir.glob("*.hex"))
            if not has_artifact:
                import shutil as _shl

                for f in obj_dir.iterdir():
                    with contextlib.suppress(OSError):
                        (f.unlink() if f.is_file() else _shl.rmtree(f, ignore_errors=True))
                cleanup2 = _kill_stale_uv4()
                with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError):
                    await asyncio.to_thread(_run)
                try:
                    log_text = log.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    try:
                        log_text = log.read_text(encoding="gbk", errors="replace")
                    except OSError:
                        log_text = ""
                tail = log_text.strip().splitlines()[-25:]
                tail_text = "\n".join(tail)
                has_artifact = any(obj_dir.glob("*.axf")) or any(obj_dir.glob("*.hex"))
                if not has_artifact:
                    admin_note = (
                        ""
                        if _is_admin()
                        else " 提示：当前服务未以管理员运行（用「启动小臣AI-管理员.cmd」启动可大幅降低安全软件误拦）。"
                    )
                    return ToolExecResult(
                        error=(
                            "编译日志显示成功，但 OBJ 目录没有生成 .axf/.hex —— "
                            "本机的第三方安全软件（联想电脑管家/火绒的实时防护）正在拦截编译器"
                            "（armcc/armlink）写固件产物。"
                            + admin_note
                            + "请让用户在联想电脑管家的「设置 → 信任区」添加 E:\\Keil_v5 与工程目录，"
                            "或用「启动小臣AI-管理员.cmd」重启服务后重试。"
                        ),
                        error_code=-1,
                    )
                cleanup += "（产物缺失，已清 OBJ 重试成功）" + cleanup2

        verdict = {
            0: "成功（0 错误）",
            1: "成功（有警告）",
        }.get(code, f"失败（退出码 {code}）")
        banner = "编译" if action == "build" else "烧录"
        out = f"Keil {banner} {verdict} {cleanup}\n--- 日志尾部 ---\n{tail_text}"
        # 退出码 0/1 都算成功（1=有警告）；>=2 为错误但作为工具结果返回给模型处理
        return ToolExecResult(output=out)
