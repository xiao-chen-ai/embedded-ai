# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""common.py — hardware 包内部共享的小工具函数(自 embedded-ai-studio/core/hardware/common.js 移植)。

约束:仅依赖标准库、不依赖包外模块,保持零依赖;
全部为纯函数,不访问网络与文件系统。
行为与 JS 版保持一致(网络名归一化/电源轨判定/工程后缀取值/引脚名解析)。
"""

import re
from dataclasses import dataclass

# 地类网络的精确集合(GND/AGND/DGND/VSS/VSSA),VSS* 另按前缀匹配。
_GROUND_EXACT = {"GND", "AGND", "DGND", "VSS", "VSSA"}


def normalize_net_name(name: object) -> str:
    """网络名归一化:仅用于“匹配比较”(电源轨/协议关键字等),模型中保留原名。

    规则:去首尾空白 + 转大写 + '-' → '_'。
    """
    return str(name if name is not None else "").strip().upper().replace("-", "_")


def to_macro_ident(name: object) -> str:
    """任意字符串 → 宏/标识符安全名:非法字符 → '_',再转大写。

    例如 "led-status (blue)" → "LED_STATUS__BLUE_"。
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name if name is not None else "").strip()).upper()


def is_ground_net_name(name: object) -> bool:
    """地类网络:GND / AGND / DGND / VSS / VSSA / VSS*(前缀匹配,如 VSS_1)。"""
    n = normalize_net_name(name)
    if not n:
        return False
    return n in _GROUND_EXACT or n.startswith("VSS")


def is_rail_net_name(name: object) -> bool:
    """电源轨网络(含地类):在地类集合中,或 VCC / 3V3 / +3V3 / VDD* / 5V / +5V / VBUS / VIN,
    或带数字的 VCC_/VDD_ 前缀。
    """
    n = normalize_net_name(name)
    if not n:
        return False
    if is_ground_net_name(n):
        return True
    if n in ("VCC", "3V3", "+3V3", "VDD", "5V", "+5V", "VBUS", "VIN"):
        return True
    if n.startswith("VDD"):  # VDDA / VDDIO / VDD_5V 等
        return True
    # VCC_1 / VDD3V3 等(带数字的 VCC_/VDD_ 前缀)
    return bool(re.match(r"^VCC_?\d", n) or re.match(r"^VDD_?\d", n))


def is_positive_rail_name(name: object) -> bool:
    """正电源轨(是电源轨但不是地):如 3V3 / 5V / VDD / VBUS。"""
    return is_rail_net_name(name) and not is_ground_net_name(name)


def is_auto_net_name(name: object) -> bool:
    """自动生成的网络名(KiCad 风格 Net-(Ref-Pad2) / Unconnected-(…) 等)。

    不参与协议功能推断,但参与连接表。
    """
    n = str(name if name is not None else "").strip()
    if not n:
        return False
    return bool(
        re.match(r"Net[-_(]", n, re.I)
        or re.fullmatch(r"N\$\d+", n, re.I)
        or re.match(r"Unconnected[-_]", n, re.I)
        or re.match(r"no.?connect", n, re.I)
    )


def auto_net_suffix(name: object) -> str:
    """提取自动网络名的主体,作 signal 名兜底:Net-(D1-Pad2) → D1_PAD2。"""
    raw = str(name if name is not None else "").strip()
    m = re.match(r"Net[-_(](.*)[)]$", raw, re.I)
    inner = m.group(1) if m else raw
    return re.sub(r"^_+|_+$", "", to_macro_ident(inner)) or "AUTO"


def parse_engineering_value(raw: object) -> float | None:
    """工程后缀取值解析(k/M/g/meg/m/u/n/p/f/t):

    k=1e3  M=1e6(工程记法兆)  g=1e9  meg=1e6  m=1e-3(小写毫)  u=1e-6  n=1e-9  p=1e-12  f=1e-15
    大小写规则:大写 M=兆、小写 m=毫、meg 一律兆;k/g/u/n/p/f/t 大小写不敏感。
    兼容 4k7 夹心记法、10R 电阻记法与带单位后缀(100nF/10kOhm)。
    无法解析为数字时返回 None。
    """
    s = str(raw if raw is not None else "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d+)([Kk])(\d+)", s)  # 4k7 夹心记法
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * 1e3
    m = re.fullmatch(r"(\d+)([Rr])(\d*)", s)  # 4R7 / 10R 电阻记法
    if m:
        return float(m.group(1)) if m.group(3) == "" else float(f"{m.group(1)}.{m.group(3)}")
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+))([A-Za-z]*)", s)
    if not m:
        return None
    num = float(m.group(1))
    tail = m.group(2)
    if not tail:
        return num
    lower = tail.lower()
    if lower.startswith("meg"):
        return num * 1e6
    if tail[0] == "M":
        return num * 1e6  # 大写 M = 兆(工程记法)
    if tail[0] == "m":
        return num * 1e-3  # 小写 m = 毫
    suffixes: tuple[tuple[str, float], ...] = (
        ("t", 1e12),
        ("g", 1e9),
        ("k", 1e3),
        ("u", 1e-6),
        ("n", 1e-9),
        ("p", 1e-12),
        ("f", 1e-15),
    )
    for suf, mult in suffixes:
        if lower.startswith(suf):
            return num * mult
    return num  # 纯单位后缀(F/Ohm/V 等)取数字值


@dataclass
class McuPinName:
    """MCU 引脚名的结构化解析结果(gpio = P[A-K]n 端口脚;numeric = 纯数字脚)。"""

    kind: str  # 'gpio' | 'numeric'
    port: str | None
    letter: str | None
    num: int


def parse_mcu_pin_name(pin: object) -> McuPinName | None:
    """MCU 引脚名 → 结构化引脚描述。

    - STM32 风格 "P[A-K]n":PB9 → { kind:'gpio', port:'PB', letter:'B', num:9 }
    - 纯数字引脚 "9" → { kind:'numeric', port:None, num:9 }
    - 其他命名(VDD / VSS / NRST / BOOT0 / OSC_IN 等)→ None(非端口型引脚)
    """
    raw = str(pin if pin is not None else "").strip()
    if not raw:
        return None
    up = raw.upper()
    m = re.fullmatch(r"P([A-K])(\d{1,2})", up)
    if m:
        return McuPinName("gpio", f"P{m.group(1)}", m.group(1), int(m.group(2)))
    m = re.fullmatch(r"(\d{1,3})", raw)
    if m:
        return McuPinName("numeric", None, None, int(m.group(1)))
    return None


def pin_order_class(pin: object) -> int:
    """引脚名排序级:0 = P[A-K]n 风格,1 = 纯数字,2 = 其他命名。"""
    pp = parse_mcu_pin_name(pin)
    if pp is None:
        return 2
    return 0 if pp.kind == "gpio" else 1


def pin_sort_key(pin: object) -> tuple:
    """引脚表排序键:P[A-K]n 端口引脚在前(端口字母序 + 引脚号),纯数字引脚次之,
    其他命名殿后(按字母序,保持稳定)。
    """
    pp = parse_mcu_pin_name(pin)
    name = str(pin if pin is not None else "")
    if pp is None:
        return (2, 0, 0, name)
    if pp.kind == "gpio":
        return (0, ord(pp.letter or "A"), pp.num, name)
    return (1, 0, pp.num, name)
