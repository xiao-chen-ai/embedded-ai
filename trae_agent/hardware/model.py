# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""model.py — 统一硬件模型(HardwareModel)与格式检测(自 embedded-ai-studio/core/hardware/model.js 移植)。

四种解析器都输出 HardwareModel 字典;peripherals.py 只消费统一模型。
"""

import re

from trae_agent.hardware.common import is_auto_net_name, normalize_net_name


def detect_format(text: object, filename: str | None = None) -> str | None:
    """detectFormat(text, filename=None)。

    文件扩展名优先:
      .net/.kicad_net → 'kicad';.cir/.spc/.sp/.spi → 'spice';.csv/.tsv → 'csv';board.yaml/.byml → 'board-yaml'
    否则按内容嗅探;都不匹配返回 None。纯函数,不读文件。
    """
    t = str(text if text is not None else "")
    if filename:
        base = str(filename).replace("\\", "/").split("/")[-1] or ""
        lower = base.lower()
        if lower.endswith(".kicad_net") or lower.endswith(".net"):
            return "kicad"
        if lower.endswith((".cir", ".spc", ".sp", ".spi")):
            return "spice"
        if lower.endswith((".csv", ".tsv")):
            return "csv"
        if lower == "board.yaml" or lower.endswith(".byml") or lower.endswith("/board.yaml"):
            return "board-yaml"
    return sniff_content(t)


def sniff_content(text: object) -> str | None:
    """内容嗅探(仅在文件名无结果时调用)。"""
    t = str(text if text is not None else "")
    trimmed = re.sub(r"^\ufeff", "", t).strip()
    if not trimmed:
        return None
    # kicad:以 (export 或 (sym 开头(部分工具输出的网表以 sym 为头)
    if re.match(r"^\(export\b", trimmed, re.I) or re.match(r"^\(sym\b", trimmed, re.I):
        return "kicad"
    # board-yaml:首个非注释行以 board: 或 mcu: 开头
    all_lines = re.split(r"\r?\n", trimmed)
    first_line = all_lines[0].strip()
    if first_line.startswith("#"):
        # 跳过注释行找第一条实质内容行
        first_real = ""
        for ln in all_lines:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            first_real = s
            break
        if re.match(r"^(board|mcu)\s*:", first_real, re.I):
            return "board-yaml"
    elif re.match(r"^(board|mcu)\s*:", first_line, re.I):
        return "board-yaml"
    # spice:首个非空行是 * / ; 注释,或 .subckt/.include/.option/.title 控制行,
    # 或有 ≥2 行「字母+数字开头」的网表行(如 R1 N001 N002 10k)
    non_empty = [ln.strip() for ln in all_lines if ln.strip()]
    first = non_empty[0] if non_empty else ""
    if re.match(r"^[*;]", first):
        # CSV 首行是表头不会以 ; 开头,行首 ; 是 SPICE 注释;仍防御性检查后续行是否像 CSV 表头
        rest_header = non_empty[1] if len(non_empty) > 1 else ""
        if not re.search(r"(ref|pin|net)", rest_header, re.I):
            return "spice"
    if re.match(r"^\.(subckt|include|lib|option|title|param|model|control|end)\b", first, re.I):
        return "spice"

    # 「字母+数字开头」的网表行(首 token 形如 R1/C5/Q12,≥2 行且含 ≥2 空白分隔字段)
    def is_net_row(row: str) -> bool:
        return bool(re.match(r"^[A-Za-z]+\d\S*\s+\S+\s+", row)) and not row.startswith(".")

    net_rows = [row for row in non_empty if is_net_row(row)]
    if len(net_rows) >= 2 and re.match(r"^[A-Za-z]", first):
        return "spice"
    # csv:首行含 ≥2 个逗号/分号/TAB 且像表头(含 ref/pin/net 字样)
    seps = len(re.findall(r"[,;\t]", first_line))
    if seps >= 2 and re.search(r"(ref|pin|net)", first_line, re.I):
        return "csv"
    return None


def empty_model(fmt: str | None, board_name: str | None = None) -> dict:
    """创建空模型。"""
    return {
        "format": fmt,
        "boardName": board_name or None,
        "components": [],
        "nets": [],
        "warnings": [],
    }


def rebuild_nets_from_components(model: dict) -> dict:
    """从 components 反向构建 nets 索引(供只声明了 components 的场景)。合并时按 ref+pin 去重。"""
    net_map: dict[str, dict] = {}
    for net in model.get("nets") or []:
        net_map[net["name"]] = net
    for comp in model.get("components") or []:
        for p in comp.get("pins") or []:
            if not p.get("net"):
                continue
            net = net_map.get(p["net"])
            if net is None:
                net = {"name": p["net"], "connections": []}
                net_map[p["net"]] = net
                model["nets"].append(net)
            dup = any(c["ref"] == comp["ref"] and c["pin"] == p["pin"] for c in net["connections"])
            if not dup:
                net["connections"].append({"ref": comp["ref"], "pin": p["pin"]})
    return model


def net_connections(model: dict, net_name: str) -> list[dict]:
    """按网络名取连接(归一化匹配)。找不到返回 []。"""
    target = normalize_net_name(net_name)
    for n in model.get("nets") or []:
        if normalize_net_name(n["name"]) == target:
            return n.get("connections") or []
    return []


def net_is_auto(model: dict, net_name: str) -> bool:
    """该网络名是否「自动生成」(不参与协议推断)。"""
    target = normalize_net_name(net_name)
    for n in model.get("nets") or []:
        if normalize_net_name(n["name"]) == target:
            return is_auto_net_name(n["name"])
    return is_auto_net_name(net_name)


def find_component(model: dict, ref: str) -> dict | None:
    """按 ref 找器件;找不到返回 None。"""
    for c in model.get("components") or []:
        if c.get("ref") == ref:
            return c
    return None


def component_pin_count(comp: dict) -> int:
    """器件引脚计数(用于 MCU 兜底选择)。"""
    return len(comp.get("pins") or [])
