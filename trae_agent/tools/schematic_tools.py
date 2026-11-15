# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""schematic_tools.py — trae-agent 的 PCB 原理图分析自定义工具。

- AnalyzeSchematicTool(name=analyze_schematic):读网表文件 → 解析 → analyzeHardware →
  原子写 <cwd>/hardware/analysis.json,输出 summaryMarkdown + issues(前 4000 字)。
- GeneratePinmapTool(name=generate_pinmap):基于最近 analysis(类级缓存或
  <cwd>/hardware/analysis.json 恢复)→ generatePinmapHeader → 写 out_path(默认 board/board.h)。

移植自 embedded-ai-studio/core/hardware(JS 版,行为一致)。
"""

import json
import os
from pathlib import Path
from typing import override

from trae_agent.hardware import analyze_netlist_text, generate_pinmap_header
from trae_agent.tools.base import Tool, ToolCallArguments, ToolError, ToolExecResult, ToolParameter

# 网表文件大小上限(200KB)与工具输出截断长度。
MAX_NETLIST_BYTES = 200 * 1024
MAX_ANALYZE_OUTPUT_CHARS = 4000
# 最近一次分析结果的持久化位置(相对工具运行时的 cwd)。
ANALYSIS_RELPATH = Path("hardware") / "analysis.json"
# generate_pinmap 输出展示的生成文件前 N 行。
PINMAP_PREVIEW_LINES = 60


class AnalyzeSchematicTool(Tool):
    """分析 PCB 原理图网表(KiCad .net / SPICE / CSV / board.yaml)。

    识别 MCU、成组总线(I2C/SPI/UART/SWD)、GPIO 角色与 LED 极性、电源轨与退耦,
    把完整分析 JSON 存到 <cwd>/hardware/analysis.json(原子写),供 generate_pinmap 复用。
    """

    # 类级别缓存最近一次分析(供 generate_pinmap 免重跑;新进程可从 analysis.json 恢复)。
    last_analysis: dict | None = None

    @override
    def get_name(self) -> str:
        return "analyze_schematic"

    @override
    def get_description(self) -> str:
        return (
            "Analyze a PCB schematic netlist file and extract the hardware facts needed for firmware "
            "development: MCU part/ref, grouped buses (I2C/SPI/UART/SWD with pin assignments), GPIO roles "
            "(LED polarity, buttons, crystals), power rails and decoupling capacitors, plus pin-conflict "
            "issues. Supports KiCad netlists (.net/.kicad_net), SPICE netlists (.cir/.spc/.sp/.spi), "
            "CSV/TSV pin tables (.csv/.tsv) and Embedded AI Studio board descriptions (board.yaml/.byml). "
            "The format is auto-detected from the filename and content unless given explicitly. "
            "The full analysis JSON is saved to <cwd>/hardware/analysis.json so generate_pinmap can "
            "produce board.h without re-running the analysis."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type="string",
                description=(
                    "Path to the netlist file, relative to the current working directory "
                    "(e.g. 'blink.net' or 'hardware/board.yaml'). Allowed: .net/.kicad_net (KiCad), "
                    ".cir/.spc/.sp/.spi (SPICE), .csv/.tsv (CSV pin table), board.yaml/.byml."
                ),
                required=True,
            ),
            ToolParameter(
                name="format",
                type="string",
                description="Explicit netlist format. Omit to auto-detect from the filename and content.",
                enum=["kicad", "spice", "csv", "board-yaml"],
                required=False,
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        """读文件(≤200KB)→ 解析 → analyzeHardware → 原子写 JSON → summaryMarkdown 输出。"""
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolError("Invalid path: must be a non-empty string pointing to the netlist file")
        fmt_override = arguments.get("format")
        if fmt_override is not None and not isinstance(fmt_override, str):
            raise ToolError("Invalid format: must be one of 'kicad', 'spice', 'csv', 'board-yaml'")

        cwd = Path.cwd()
        src = Path(raw_path)
        if not src.is_absolute():
            src = cwd / src
        src = src.resolve()
        if not src.exists():
            return ToolExecResult(
                error=f"Netlist file not found: {raw_path} (resolved: {src}). Check the path relative to {cwd}.",
                error_code=-1,
            )
        if not src.is_file():
            return ToolExecResult(
                error=f"Path is not a regular file: {raw_path} (resolved: {src}).", error_code=-1
            )
        size = src.stat().st_size
        if size > MAX_NETLIST_BYTES:
            return ToolExecResult(
                error=f"Netlist file too large: {size} bytes (limit {MAX_NETLIST_BYTES} bytes): {raw_path}.",
                error_code=-1,
            )
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolExecResult(
                error=f"Failed to read netlist file {raw_path}: {e}", error_code=-1
            )

        fmt = fmt_override if fmt_override else None
        analysis = analyze_netlist_text(text, filename=src.name, format=fmt)

        # 原子写分析 JSON:先写临时文件再 os.replace,避免半写状态被 generate_pinmap 读到。
        try:
            analysis_dir = cwd / "hardware"
            analysis_dir.mkdir(parents=True, exist_ok=True)
            tmp = analysis_dir / "analysis.json.tmp"
            tmp.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, analysis_dir / "analysis.json")
        except OSError as e:
            return ToolExecResult(
                error=f"Failed to persist analysis to hardware/analysis.json: {e}", error_code=-1
            )

        AnalyzeSchematicTool.last_analysis = analysis

        # 输出:summaryMarkdown + issues 列表(前 4000 字)。
        output = str(analysis.get("summaryMarkdown") or "")
        issues = analysis.get("issues") or []
        if len(issues) > 0:
            output += "\n\n## Issues\n" + "\n".join(f"- {iss}" for iss in issues)
        output = output[:MAX_ANALYZE_OUTPUT_CHARS]
        return ToolExecResult(output=output)


class GeneratePinmapTool(Tool):
    """基于最近一次原理图分析生成 board.h 引脚映射头文件。

    数据来源:analyze_schematic 的类级缓存;若当前进程没有缓存,则从
    <cwd>/hardware/analysis.json 恢复。两者都没有时报错并提示先运行 analyze_schematic。
    """

    @override
    def get_name(self) -> str:
        return "generate_pinmap"

    @override
    def get_description(self) -> str:
        return (
            "Generate the board.h pin-mapping header from the most recent schematic analysis "
            "(run analyze_schematic first, or have <cwd>/hardware/analysis.json present from a previous "
            "run). Emits C macros (PORT/PIN/AF/DIR/ACTIVE_LOW per signal) for buses and GPIO roles; "
            "macro names are sanitized to [A-Z0-9_]. Firmware code must use these macros instead of "
            "raw pin names like PA5. Writes the header to out_path (default 'board/board.h'), "
            "creating parent directories as needed."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="out_path",
                type="string",
                description="Output path of the generated board.h, relative to the current working directory. "
                "Defaults to 'board/board.h'.",
                required=False,
            ),
        ]

    @staticmethod
    def _load_analysis() -> dict | None:
        """取最近 analysis:类级缓存优先,否则从 <cwd>/hardware/analysis.json 恢复。"""
        if AnalyzeSchematicTool.last_analysis is not None:
            return AnalyzeSchematicTool.last_analysis
        persisted = Path.cwd() / ANALYSIS_RELPATH
        if persisted.is_file():
            try:
                data = json.loads(persisted.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                return None
        return None

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        """最近 analysis → generatePinmapHeader → 写文件(自动建目录)→ 输出前 60 行。"""
        analysis = self._load_analysis()
        if analysis is None:
            return ToolExecResult(
                error=(
                    "No schematic analysis available. Run analyze_schematic on a netlist file first "
                    "(or make sure <cwd>/hardware/analysis.json exists)."
                ),
                error_code=-1,
            )

        out_path_arg = arguments.get("out_path")
        if out_path_arg is not None and not isinstance(out_path_arg, str):
            raise ToolError("Invalid out_path: must be a string")
        out_rel = out_path_arg if out_path_arg else "board/board.h"
        cwd = Path.cwd()
        out = Path(out_rel)
        if not out.is_absolute():
            out = cwd / out
        out = out.resolve()

        header = generate_pinmap_header(analysis, {"guardPrefix": "BOARD"})
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(header, encoding="utf-8")
            os.replace(tmp, out)
        except OSError as e:
            return ToolExecResult(
                error=f"Failed to write pinmap header to {out_rel}: {e}", error_code=-1
            )

        preview_lines = header.splitlines()[:PINMAP_PREVIEW_LINES]
        preview = "\n".join(preview_lines)
        more = (
            ""
            if len(header.splitlines()) <= PINMAP_PREVIEW_LINES
            else f"\n... ({len(header.splitlines())} lines total)"
        )
        return ToolExecResult(output=f"Wrote {out_rel} ({len(header)} chars).\n\n{preview}{more}")
