# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""spice-netlist.py — SPICE 网表解析 → HardwareModel(自 embedded-ai-studio/core/hardware/spice-netlist.js 移植)。

支持:`*`/`;` 行注释、`+` 续行、.subckt/.ends 嵌套展平、
工程后缀取值(k/M/meg/u/n/p/f/g)、首字母器件类型、K 耦合电感等。
忽略 .include/.lib/.model/.param/.option/.control 等控制行(记中文警告)。
"""

import re

from trae_agent.hardware.common import parse_engineering_value
from trae_agent.hardware.model import empty_model

# 首字母 → 器件类型映射(归一化)。
TYPE_MAP: dict[str, str] = {
    "r": "R",
    "c": "C",
    "l": "L",
    "d": "diode",
    "q": "bjt",
    "j": "jfet",
    "m": "mosfet",
    "u": "ic",
    "v": "source",
    "i": "current",
    "x": "subckt",
    "s": "switch",
    "k": "coupled",
}


def _split_fields(line: str) -> list[str]:
    """按空白拆分行内字段,支持 "..." 包裹(字段内容可含空格)。"""
    out: list[str] = []
    cur = ""
    in_q = False
    for ch in line:
        if ch == '"':
            in_q = not in_q
            continue
        if not in_q and ch in (" ", "\t"):
            if cur:
                out.append(cur)
            cur = ""
            continue
        cur += ch
    if cur:
        out.append(cur)
    return out


def logical_lines(raw_text: object) -> list[str]:
    """物理行数组 → 逻辑行:`+` 续行拼接到前一逻辑行,

    `*`/`;` 注释行丢弃(注释太常见,不记警告)。纯函数。
    """
    text = re.sub(r"\r\n?", "\n", str(raw_text if raw_text is not None else ""))
    lines = text.split("\n")
    out: list[str] = []
    buf: str | None = None
    for raw in lines:
        trimmed = raw.strip()
        if not trimmed:
            continue  # 空行:跳过(续行要求紧跟上一行,这里宽松处理)
        if buf is not None and trimmed.startswith("+"):
            buf += " " + trimmed[1:].strip()
            continue
        if buf is not None:
            out.append(buf)
            buf = None
        if trimmed.startswith("*") or trimmed.startswith(";"):
            continue
        line = _strip_inline_comment(trimmed)
        if not line:
            continue
        buf = line
    if buf is not None:
        out.append(buf)
    return out


def _strip_inline_comment(line: str) -> str:
    """截断行内 ; 注释(引号感知)。"""
    in_q = False
    for i, c in enumerate(line):
        if c == '"':
            in_q = not in_q
        elif c == ";" and not in_q:
            return line[:i].strip()
    return line.strip()


def parse_spice_netlist(text: object) -> dict:
    """parseSpiceNetlist(text) -> HardwareModel。

    - subckt 内器件 ref 加 `<父名>.` 前缀后展平到顶层。
    - X 器件:value = 子电路名(节点列后、最后一个非 key=value 字段)。
    - .include 等控制行忽略并记警告;.subckt 定义信息也记入警告。
    """
    model = empty_model("spice")
    raw = str(text if text is not None else "")
    if not raw.strip():
        model["warnings"].append("网表文本为空")
        return model
    lines = logical_lines(raw)
    # 第一遍:按 .subckt/.ends 分块(支持嵌套)
    blocks: list[dict] = []
    subckt_stack: list[dict] = []
    for line in lines:
        if re.match(r"^\.subckt\b", line, re.I):
            fields = _split_fields(line)
            subckt_stack.append(
                {
                    "name": fields[1] if len(fields) > 1 else f"SUB{len(subckt_stack) + 1}",
                    "prefix": fields[2:],
                    "comps": [],
                    "warnings": [],
                }
            )
            continue
        if re.match(r"^\.ends?\b", line, re.I):
            blk = subckt_stack.pop() if subckt_stack else None
            if blk is None:
                model["warnings"].append(".ends 没有与之匹配的 .subckt,已忽略")
            else:
                blocks.append(blk)
            continue
        if len(subckt_stack) > 0:
            subckt_stack[-1]["comps"].append(line)
            continue
        if line.startswith(".") or line.startswith("@"):
            _handle_control_line(line, model)
            continue
        blocks.append({"name": None, "comps": [line]})
    if len(subckt_stack) > 0:
        for blk in subckt_stack:
            model["warnings"].append(
                f".subckt {blk['name']} 未用 .ends 闭合(可能被截断),已尽力展开"
            )
            blocks.append(blk)

    # 第二遍:每个块展开成器件
    subckt_info: dict[str, dict] = {}
    for blk in blocks:
        if blk["name"] is not None:
            subckt_info[str(blk["name"]).lower()] = {
                "name": blk["name"],
                "count": len(blk["comps"]),
            }
        for line in blk["comps"]:
            ref_prefix = f"{blk['name']}." if blk["name"] is not None else ""
            _parse_device_line(line, ref_prefix, model)
    for info in subckt_info.values():
        model["warnings"].append(
            f"检测到子电路定义 {info['name']}(内部 {info['count']} 个器件),已展平进模型,ref 前缀 {info['name']}."
        )

    _build_nets(model)
    return model


KNOWN_CONTROL_LINES = [
    ".include",
    ".lib",
    ".model",
    ".param",
    ".option",
    ".control",
    ".endc",
    ".end",
    ".options",
    ".print",
    ".plot",
    ".probe",
    ".dc",
    ".ac",
    ".tran",
    ".op",
    ".nodeset",
    ".ic",
    ".global",
    ".save",
    ".width",
    ".alias",
    ".four",
    ".noise",
    ".sens",
    ".tf",
    ".pss",
    ".step",
    ".temp",
    ".title",
]


def _handle_control_line(line: str, model: dict) -> None:
    """处理 .xxx 控制行:忽略但记录(subckt 名称由第二遍记录)。"""
    parts = re.split(r"\s+", line)
    cmd = (parts[0] if parts else "").lower()
    if cmd in (".include", ".lib"):
        model["warnings"].append(f"{line.strip()} — 外部文件引用已忽略(离线分析,只解析单文件内容)")
        return
    if cmd == ".title":
        return  # title 行太常见,不警告
    if cmd not in KNOWN_CONTROL_LINES:
        model["warnings"].append(f"无法识别的控制行,已忽略:{line.strip()[:80]}")


def _parse_device_line(line: str, ref_prefix: str, model: dict) -> None:
    """解析单条器件行。"""
    fields = _split_fields(line)
    if len(fields) == 0:
        return
    first = fields[0]
    letter = first[0].lower()
    comp_type = TYPE_MAP.get(letter)
    if comp_type is None:
        model["warnings"].append(f"无法识别的器件行(首字母 {letter}):{line.strip()[:80]}")
        return
    ref = ref_prefix + first
    # 已存在同 ref 器件时保留第一个(重复 ref 视为网表错误,给警告)
    if any(c.get("ref") == ref for c in model["components"]):
        model["warnings"].append(f"重复的引用名 {ref},保留第一个")
        return
    # 节点列与取值列
    nodes: list[str] = []
    value_parts: list[str] = []
    rest = fields[1:]
    if comp_type == "subckt":
        # X<name> n1 n2 ... subcktName [key=value 参数…]
        # 惯例:节点列之后、最后一个非 key=value 的字段是子电路名
        name_idx = -1
        for i in range(len(rest) - 1, -1, -1):
            if not re.match(r"^[A-Za-z0-9_]*=", rest[i]) and rest[i] != "=":
                name_idx = i
                break
        if name_idx == -1 and len(rest) > 0:
            name_idx = len(rest) - 1
        if name_idx >= 0:
            value_parts = [rest[name_idx]]
            nodes = rest[:name_idx] + [
                f for f in rest[name_idx + 1 :] if not re.match(r"^[A-Za-z0-9_]*=", f)
            ]
    elif comp_type == "coupled":
        # K<name> L1 L2 [0.99] [model]:L1/L2 是电感 ref(按节点处理),其余是取值
        # 简化:第一个纯数字字段为耦合系数,其前为电感 ref
        num_idx = -1
        for i, f in enumerate(rest):
            if re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+|[a-zA-Z]*)?", f):
                num_idx = i
                break
        if num_idx >= 1:
            nodes = rest[:num_idx]
            value_parts = [rest[num_idx]]
        else:
            nodes = rest
            value_parts = []
    else:
        # 通用:节点 = 中间列,取值 = 末列(R/C/L/D/Q…;多出的模型字段并入 value)
        if len(rest) == 0:
            nodes = []
            value_parts = []
        else:
            # 经典网表如 `R1 N001 N002 10k`:末字段为取值,之前均为节点;
            # Q/M/J/D/U 等多参器件(如 Q1 c b e MODNAME):第一个非节点的名称字段是模型名
            if letter in ("q", "m", "j", "d", "u"):
                idx = -1
                for i, f in enumerate(rest):
                    if re.match(r"^[A-Za-z_\"']", f):
                        idx = i
                        break
                if idx >= 0:
                    nodes = rest[:idx]
                    value_parts = [rest[idx]]
                else:
                    nodes = rest
                    value_parts = []
            else:
                nodes = rest[:-1]
                value_parts = [rest[-1]]
    raw_value = " ".join(f for f in value_parts if f)
    comp: dict = {
        "ref": ref,
        "value": raw_value,
        "type": comp_type,  # 归一化器件类型(R/C/L/diode/bjt/jfet/mosfet/ic/source/current/subckt/switch/coupled)
        "pins": [],
    }
    num_val = parse_engineering_value(raw_value)
    if num_val is not None and comp_type != "subckt":
        comp["valueNumeric"] = num_val
    for nd in nodes:
        comp["pins"].append({"pin": nd, "net": nd})
    model["components"].append(comp)


def _build_nets(model: dict) -> None:
    """从 components[].pins 构建 net[]:每个节点名即一个网络。"""
    net_map: dict[str, dict] = {}
    order: list[str] = []
    for comp in model["components"]:
        for p in comp.get("pins") or []:
            key = p["net"]
            if key not in net_map:
                net_map[key] = {"name": key, "connections": []}
                order.append(key)
            net_map[key]["connections"].append({"ref": comp["ref"], "pin": p["pin"]})
    model["nets"] = [net_map[k] for k in order]
