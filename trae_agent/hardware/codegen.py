# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""codegen.py — 分析结果 → board.h 头文件 / 给 LLM 的硬件上下文(自 embedded-ai-studio/core/hardware/codegen.js 移植)。

约束:宏名仅 [A-Z0-9_],输出须通过 C 预处理语法风格检查。
"""

import re
from datetime import datetime

from trae_agent.hardware.common import to_macro_ident


def _safe_macro(name: object, fallback: str | None = None) -> str:
    """合法宏名:大写字母/数字/下划线,连续下划线压一,不吃数字开头。"""
    s = re.sub(r"\s+", "", to_macro_ident(name))
    s = re.sub(r"_+", "_", re.sub(r"^_+", "", s))
    if not re.match(r"^[A-Z_]", s):
        s = f"_{s}"
    if not re.fullmatch(r"[A-Z0-9_]+", s) or s == "_":
        s = fallback or "SIG"
    return s


def _now_stamp() -> str:
    """本地时间戳(无毫秒,适合写进文件头)。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _comment_safe(s: object) -> str:
    """注释内文本安全化:终止符 + 换行。"""
    return re.sub(r"\r?\n", " ", str(s if s is not None else "").replace("*/", "* /"))


def generate_pinmap_header(analysis: dict | None, opts: dict | None = None) -> str:
    """generatePinmapHeader(analysis, {guardPrefix='BOARD', boardDefine=''}) -> string。

    完整可编译风格 board.h:
      文件头注释(生成时间/板名/MCU/网表来源格式/警告数) → 守卫 → MCU/板名宏 →
      方向宏 → GPIO 信号 4 行宏组(PORT/PIN/AF/DIR/ACTIVE_LOW) →
      总线段(每总线设备列表注释 + 引脚宏组) → 电源摘要 → 分析提示 → 收尾。
    """
    o = opts or {}
    guard_prefix = _safe_macro(o.get("guardPrefix") or "BOARD", "BOARD")
    guard = f"{guard_prefix}_H"
    board_define = o.get("boardDefine") if isinstance(o.get("boardDefine"), str) else ""
    a = analysis or {
        "mcu": None,
        "buses": [],
        "gpio": [],
        "power": {"rails": []},
        "pinmap": [],
        "issues": [],
    }

    board_name = board_define or (a.get("model") or {}).get("boardName") or a.get("boardName") or ""
    source_format = a.get("sourceFormat") or (a.get("model") or {}).get("format") or "unknown"
    if a.get("model") is not None and (a.get("model") or {}).get("warnings") is not None:
        parse_warnings = len(a["model"]["warnings"])
    else:
        pw = a.get("parseWarnings")
        parse_warnings = len(pw) if pw is not None else 0
    all_signal_issues = len(a.get("issues") or [])

    lines: list[str] = []
    lines.append("/* ============================================================")
    lines.append(" * board.h — 引脚映射(由 PCB 网表分析自动生成,请勿手改)")
    lines.append(" * ------------------------------------------------------------")
    lines.append(f" * 生成时间   : {_now_stamp()}")
    lines.append(f" * 板名       : {_comment_safe(board_name or '(未命名)')}")
    if a.get("mcu"):
        lines.append(
            f" * MCU        : {_comment_safe(a['mcu'].get('part'))}(位号 {_comment_safe(a['mcu'].get('ref'))},{a['mcu'].get('pinCount')} 引脚)"
        )
    lines.append(f" * 网表来源   : {source_format} 格式网表")
    lines.append(f" * 警告数量   : {parse_warnings}(解析)+ {all_signal_issues}(分析)")
    lines.append(" * 使用约定   : 引脚约束来自真实网表分析;生成固件代码时必须使用本文件")
    lines.append(" *              的宏,不得直接写 PA5、PB9 这类裸引脚名。")
    lines.append(" * ============================================================ */")
    lines.append("")
    lines.append(f"#ifndef {guard}")
    lines.append(f"#define {guard}")
    lines.append("")
    lines.append(
        f'#define {guard_prefix}_MCU_PART "{(str(a["mcu"].get("part")).replace(chr(34), "") if a.get("mcu") else "UNKNOWN")}"'
    )
    lines.append(
        f'#define {guard_prefix}_BOARD_NAME "{board_name.replace(chr(34), "") or "unnamed"}"'
    )
    lines.append("")
    lines.append("/* 引脚方向宏(供 *_DIR 宏引用) */")
    lines.append(f"#define {guard_prefix}_PIN_OUTPUT 0")
    lines.append(f"#define {guard_prefix}_PIN_INPUT  1")
    lines.append(f"#define {guard_prefix}_PIN_AF     2")
    lines.append("")
    lines.append("/* 使用的 GPIO 端口头(按目标平台调整包含路径) */")
    ports = sorted({p.get("port") for p in (a.get("pinmap") or []) if p.get("port")})
    if len(ports) > 0:
        lines.append(f"/* 端口: {', '.join(ports)} → GPIOA..GPIO{ports[-1].replace('P', '')} */")
        lines.append('#include "stm32/gpio.h"')
    else:
        lines.append("/* (分析未涉及端口引脚) */")
    lines.append("")

    # ---- GPIO/晶振等非总线信号段 ----
    gpio_entries = [p for p in (a.get("pinmap") or []) if not p.get("isBus")]
    if len(gpio_entries) == 0:
        lines.append("/* (分析结果中没有非总线 GPIO 信号) */")
        lines.append("")
    used_names: dict[str, int] = {}

    def dedup_name(sig: str) -> str:
        """信号名去重:重名追加 _2/_3。"""
        n = used_names.get(sig, 0) + 1
        used_names[sig] = n
        return f"{sig}_{n}" if n > 1 else sig

    def dir_macro(d: str | None) -> str:
        if d == "in":
            return f"{guard_prefix}_PIN_INPUT"
        if d in ("af", "alternate"):
            return f"{guard_prefix}_PIN_AF"
        return f"{guard_prefix}_PIN_OUTPUT"

    for p in gpio_entries:
        sig = dedup_name(_safe_macro(p.get("signal"), "SIG"))
        if p.get("label"):
            lines.append(f"/* {_comment_safe(p['label'])} */")
        if p.get("note"):
            lines.append(f"/* {_comment_safe(p['note'])} */")
        lines.append(
            f'#define {guard_prefix}_{sig}_PORT       GPIO{re.sub(r"^P", "", str(p["port"]))} /* {p["port"]}{p["num"]}: 网络 "{_comment_safe(p.get("net"))}" */'
        )
        lines.append(f"#define {guard_prefix}_{sig}_PIN        {p['num']}")
        lines.append(f"#define {guard_prefix}_{sig}_AF         {p.get('af') or 'GPIO'}")
        lines.append(f"#define {guard_prefix}_{sig}_DIR        {dir_macro(p.get('direction'))}")
        lines.append(f"#define {guard_prefix}_{sig}_ACTIVE_LOW {1 if p.get('activeLow') else 0}")
        lines.append("")

    # ---- 总线段 ----
    buses = a.get("buses") or []
    if len(buses) == 0:
        lines.append("/* (未识别到 I2C/SPI/UART/SWD 总线) */")
        lines.append("")
    else:
        lines.append("/* ========================= 总线 ========================= */")
        bus_index: dict[str, int] = {"i2c": 0, "spi": 0, "uart": 0, "swd": 0}
        for bus in buses:
            bus_index[bus["type"]] = bus_index.get(bus["type"], 0) + 1
            devices = ", ".join(f"{d['ref']}({d['part']})" for d in (bus.get("devices") or []))
            lines.append(
                f"/* ---- {bus['type'].upper()} 总线{f' — 挂载: {devices}' if devices else ''} ---- */"
            )
            if bus.get("note"):
                lines.append(f"/* {_comment_safe(bus['note'])} */")
            for name, pin in (bus.get("pins") or {}).items():
                if pin is None:
                    continue
                # pinmap 条目按契约只有 port/num(无原始 pin 字段),这里解析后按端口+序号匹配
                pm = re.fullmatch(r"P([A-K])(\d{1,2})", str(pin), re.I)
                if not pm:
                    continue
                port = f"P{pm.group(1).upper()}"
                num = int(pm.group(2))
                entry = next(
                    (
                        x
                        for x in (a.get("pinmap") or [])
                        if x.get("isBus") is True and x.get("port") == port and x.get("num") == num
                    ),
                    None,
                )
                if entry is None:
                    entry = next(
                        (
                            x
                            for x in (a.get("pinmap") or [])
                            if x.get("port") == port and x.get("num") == num
                        ),
                        None,
                    )
                sig = dedup_name(
                    _safe_macro(
                        (entry or {}).get("signal") or f"{bus['type'].upper()}_{name.upper()}",
                        "BUS",
                    )
                )
                inst = (
                    f" /* 推断为 {bus['type'].upper()}#{bus_index[bus['type']]} */"
                    if bus_index[bus["type"]] > 1
                    else ""
                )
                lines.append(
                    f"#define {guard_prefix}_{sig}_PORT       GPIO{pm.group(1).upper()} /* {pin}: {bus['type'].upper()} {name.upper()}{inst} */"
                )
                lines.append(f"#define {guard_prefix}_{sig}_PIN        {num}")
                lines.append(
                    f"#define {guard_prefix}_{sig}_AF         {(entry or {}).get('af') or bus['type'].upper()}"
                )
                lines.append(
                    f"#define {guard_prefix}_{sig}_DIR        {dir_macro((entry or {}).get('direction') or 'alternate')}"
                )
                lines.append(f"#define {guard_prefix}_{sig}_ACTIVE_LOW 0")
            lines.append("")

    # ---- 电源摘要 ----
    lines.append("/* ========================= 电源 ========================= */")
    rails = (a.get("power") or {}).get("rails") or []
    if len(rails) == 0:
        lines.append("/* 未识别到电源轨 */")
    else:
        lines.append(
            "/* 电源轨: "
            + ", ".join(f"{r['name']}({r['connections']} 连接)" for r in rails)
            + " */"
        )
    lines.append(
        f"/* 退耦电容: {(a.get('power') or {}).get('decouplingCount', 0)} 个(0.1u/100n 级) */"
    )
    lines.append("")

    # ---- 分析提示 ----
    issues = a.get("issues") or []
    if len(issues) > 0:
        lines.append("/* ============== 分析提示(写代码前请阅读) ============== */")
        for iss in issues[:60]:
            lines.append(f"/* - {_comment_safe(iss)} */")
        lines.append("")

    lines.append(f"#endif /* {guard} */")
    lines.append("")
    lines.append("/* Generated by Embedded AI Studio */")
    lines.append("")
    return "\n".join(lines)


def hardware_context_prompt(analysis: dict | None) -> str:
    """hardwareContextPrompt(analysis) -> string。

    注入 agent system prompt 的中文硬件上下文:摘要 + 硬约束指令 + 引脚宏速查。
    """
    a = analysis or {
        "mcu": None,
        "buses": [],
        "gpio": [],
        "pinmap": [],
        "issues": [],
        "power": {"rails": [], "decouplingCount": 0},
        "model": None,
    }
    lines: list[str] = []
    lines.append("# 硬件上下文(来自 PCB 网表分析)")
    lines.append("")
    lines.append(a.get("summaryMarkdown") or "(分析结果为空)")
    lines.append("")
    lines.append("## 写固件代码时必须遵守的引脚约束")
    lines.append("")
    lines.append(
        f"1. 下述所有引脚事实来自网表分析,生成代码时**必须使用 board.h 中的宏**(如 `{_board_macro_example(a)}`),不得直接写 PA5、PB9 这类裸引脚名。"
    )
    lines.append(
        "2. 总线引脚必须按分析出的外设与引脚配置(如 I2C 的 SDA/SCL、SPI 的 SCK/MOSI/MISO/CS、UART 的 TX/RX),不得凭空选择引脚。"
    )
    lines.append("3. 输出器件注意 ACTIVE_LOW 宏:值为 1 的 LED 等器件是低电平有效(拉低点亮/使能)。")
    lines.append("4. 上拉/下拉需求参考网络连接表(如按键网络是否有上拉电阻)。")
    lines.append("5. 「问题与提示」中列出的冲突/未识别连接,涉及相关引脚时先向用户确认再写代码。")
    lines.append("")
    lines.append("## 引脚宏速查")
    lines.append("")
    pm = a.get("pinmap") or []
    if len(pm) == 0:
        lines.append("(无)")
    else:
        lines.append("| 信号宏 | 引脚 | AF | 方向 | 低有效 |")
        lines.append("|---|---|---|---|---|")
        for p in pm:
            lines.append(
                f"| BOARD_{p['signal']} | {p['port']}{p['num']} | {p['af']} | {p['direction']} | {'是' if p.get('activeLow') else '否'} |"
            )
    return "\n".join(lines)


def _board_macro_example(a: dict) -> str:
    pm = a.get("pinmap") or []
    if len(pm) > 0:
        return f"BOARD_{pm[0]['signal']}_PIN"
    return "BOARD_<SIGNAL>_PIN"
