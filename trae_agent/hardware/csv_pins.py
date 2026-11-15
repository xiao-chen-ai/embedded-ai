# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""csv-pins.py — CSV/TSV 引脚表解析 → HardwareModel(自 embedded-ai-studio/core/hardware/csv-pins.js 移植)。

自动嗅探分隔符(, / ; / TAB),表头列为 ref,pin,net,name,value,pin_type,footprint
的任意子集(大小写/空格不敏感,name 兼作 value 别名),处理 BOM,支持引号包裹字段。
"""

import re

from trae_agent.hardware.model import empty_model

# 已知表头列(归一化后比较)。
KNOWN_HEADERS = [
    "ref",
    "pin",
    "net",
    "name",
    "value",
    "pin_type",
    "pintype",
    "pin function",
    "footprint",
]


def _strip_bom(text: object) -> str:
    """去 UTF-8 BOM。"""
    return re.sub(r"^\ufeff", "", str(text if text is not None else ""))


def _count_char(s: str, ch: str) -> int:
    """统计行内某字符出现次数。"""
    return sum(1 for c in s if c == ch)


def _sniff_separator_from_line(line: str) -> str:
    """按单行嗅探分隔符。"""
    counts = [
        (",", _count_char(line, ",")),
        (";", _count_char(line, ";")),
        ("\t", _count_char(line, "\t")),
    ]
    counts.sort(key=lambda x: x[1], reverse=True)
    if counts[0][1] == 0:
        return ","
    return counts[0][0]


def sniff_separator(text: object) -> str:
    """嗅探分隔符:看首行 , / ; / TAB 出现次数,取最多者;没有则默认 ','。"""
    first_line = re.split(r"\r?\n", _strip_bom(text))[0] if _strip_bom(text) else ""
    return _sniff_separator_from_line(first_line)


def parse_csv_line(line: str, sep: str) -> list[str]:
    """解析单条 CSV 物理行,支持 "..." 包裹字段(引号内 "" 为转义)。

    返回 string[];未加引号的行尾分隔符意味着空字段。
    """
    out: list[str] = []
    cur = ""
    in_q = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == '"':
            if in_q and i + 1 < n and line[i + 1] == '"':
                cur += '"'
                i += 1
            else:
                in_q = not in_q
            i += 1
            continue
        if not in_q and c == sep:
            out.append(cur)
            cur = ""
            i += 1
            continue
        cur += c
        i += 1
    out.append(cur)
    return out


def _normalize_header_cell(cell: object) -> str:
    """表头字段名归一化:去引号/空白、小写、去下划线连字符 → 与已知列表比较。"""
    s = str(cell if cell is not None else "").strip()
    s = re.sub(r"^[\"']|[\"']$", "", s)
    s = re.sub(r"[\s_-]+", "", s).lower()
    if s in ("pintype", "pinfunction"):
        return "pin_type"
    return s


def _cell(cells: list[str], idx: int) -> str:
    """按列号取单元格(越界返回 '',去首尾空白)。"""
    return cells[idx].strip() if 0 <= idx < len(cells) else ""


def parse_csv_pins(text: object) -> dict:
    """parseCsvPins(text) -> HardwareModel。

    表头行可为任意子集,但至少要含 ref 与 pin(否则没有引脚事实,给出警告)。
    """
    model = empty_model("csv")
    clean = _strip_bom(text)
    if not clean.strip():
        model["warnings"].append("CSV 文本为空")
        return model
    lines = re.split(r"\r?\n", clean)
    # 找表头行:前几个非空的行中寻找(CSV 无注释;首行不像表头时尝试后续行)
    header_idx = -1
    headers: list[str] | None = None
    for i in range(min(len(lines), 3)):
        if not lines[i].strip():
            continue
        sep = _sniff_separator_from_line(lines[i])
        cells = [_normalize_header_cell(c) for c in parse_csv_line(lines[i], sep)]
        if "ref" in cells and "pin" in cells:
            header_idx = i
            headers = cells
            model["sep"] = sep
            break
        if any(c in KNOWN_HEADERS for c in cells):
            # 疑似表头但缺关键列
            model["warnings"].append(f"第 {i + 1} 行像表头但缺少 ref/pin 必需列")
    if header_idx < 0 or headers is None:
        model["warnings"].append("未找到有效表头行(须含 ref 与 pin 列),解析中止")
        return model
    if "net" not in headers:
        model["warnings"].append("表头缺少 net 列,引脚连接信息将不完整")

    # 列号映射
    col = {
        "ref": headers.index("ref"),
        "pin": headers.index("pin"),
        "net": headers.index("net") if "net" in headers else -1,
        "name": headers.index("name") if "name" in headers else -1,
        "value": headers.index("value") if "value" in headers else -1,
        "pinType": headers.index("pin_type") if "pin_type" in headers else -1,
        "footprint": headers.index("footprint") if "footprint" in headers else -1,
    }
    sep = model.get("sep") or ","
    comp_by_ref: dict[str, dict] = {}
    net_map: dict[str, dict] = {}

    for i in range(header_idx + 1, len(lines)):
        raw_line = lines[i]
        if not raw_line.strip():
            continue
        cells = parse_csv_line(raw_line, sep)
        ref = _cell(cells, col["ref"])
        pin = _cell(cells, col["pin"])
        if not ref:
            model["warnings"].append(f"第 {i + 1} 行 ref 为空,已跳过")
            continue
        if not pin:
            model["warnings"].append(f"第 {i + 1} 行({ref})pin 为空,已跳过")
            continue
        net = _cell(cells, col["net"]) or f"{ref}_{pin}"
        value = _cell(cells, col["value"]) or _cell(cells, col["name"])  # name 兼作 value 别名
        comp = comp_by_ref.get(ref)
        if comp is None:
            comp = {
                "ref": ref,
                "value": "",
                "footprint": (_cell(cells, col["footprint"]) or None)
                if col["footprint"] >= 0
                else None,
                "pins": [],
            }
            if col["pinType"] >= 0 and _cell(cells, col["pinType"]):
                comp["pinType"] = _cell(cells, col["pinType"])
            comp_by_ref[ref] = comp
            model["components"].append(comp)
        if not comp.get("value") and value:
            comp["value"] = value
        if any(p["pin"] == pin for p in comp["pins"]):
            model["warnings"].append(f"第 {i + 1} 行:{ref} 的引脚 {pin} 重复,保留第一条连接")
            continue
        comp["pins"].append({"pin": pin, "net": net})
        # 网络表
        if net not in net_map:
            net_map[net] = {"name": net, "connections": []}
            model["nets"].append(net_map[net])
        net_map[net]["connections"].append({"ref": ref, "pin": pin})
    return model
