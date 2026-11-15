# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""hardware 包 — 硬件分析模块(trae_agent.hardware)的统一出口,自 embedded-ai-studio/core/hardware/index.js 移植。

Agent 工具层(analyze_schematic / generate_pinmap)从此进入:
四种网表格式(KiCad / SPICE / CSV / board.yaml)→ 统一 HardwareModel → 外设分析 → board.h 生成。
"""

import re

from trae_agent.hardware.board_yaml import parse_board_yaml
from trae_agent.hardware.codegen import generate_pinmap_header, hardware_context_prompt
from trae_agent.hardware.csv_pins import parse_csv_pins
from trae_agent.hardware.kicad_netlist import parse_kicad_netlist
from trae_agent.hardware.model import (
    detect_format,
    empty_model,
    find_component,
    rebuild_nets_from_components,
)
from trae_agent.hardware.peripherals import analyze_hardware, identify_mcu
from trae_agent.hardware.spice_netlist import parse_spice_netlist

__all__ = [
    "detect_format",
    "parse_kicad_netlist",
    "parse_spice_netlist",
    "parse_csv_pins",
    "parse_board_yaml",
    "analyze_hardware",
    "identify_mcu",
    "generate_pinmap_header",
    "hardware_context_prompt",
    "empty_model",
    "rebuild_nets_from_components",
    "find_component",
    "parse_netlist",
    "analyze_netlist_text",
]


def parse_netlist(
    text: str, filename: str | None = None, format_override: str | None = None
) -> dict:
    """一键:文本 → HardwareModel(format 未给定则自动探测)。

    无法识别格式时返回 format=None 的空模型 + 中文警告,不抛异常。
    """
    fmt = format_override or detect_format(text, filename)
    if not fmt:
        m = empty_model(None)
        m["warnings"].append("无法识别网表格式(不是 KiCad/SPICE/CSV/board.yaml)")
        return m
    if fmt == "kicad":
        return parse_kicad_netlist(text)
    if fmt == "spice":
        return parse_spice_netlist(text)
    if fmt == "csv":
        return parse_csv_pins(text)
    if fmt == "board-yaml":
        return parse_board_yaml(text)
    m = empty_model(None)
    m["warnings"].append(f"不支持的格式:{fmt}")
    return m


def analyze_netlist_text(
    text: str,
    filename: str | None = None,
    format: str | None = None,
    board_name: str | None = None,
) -> dict:
    """全流程:文本 → HardwareModel → Analysis。

    boardName 可传入覆盖(KiCad 网表本身不含板名;缺省时用文件名去后缀)。
    结果附 sourceFormat 与 model,便于调用方直接生成 pinmap 或落盘。
    """
    model = parse_netlist(text, filename, format)
    if board_name:
        model["boardName"] = board_name
    elif not model.get("boardName") and filename:
        base = str(filename).replace("\\", "/").split("/")[-1]
        model["boardName"] = (re.sub(r"\.[^.]+$", "", base) or None) if base else None
    analysis = analyze_hardware(model)
    analysis["sourceFormat"] = model.get("format")
    analysis["model"] = model
    return analysis
