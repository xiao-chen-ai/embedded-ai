# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""peripherals.py — analyzeHardware(HardwareModel) -> Analysis(自 embedded-ai-studio/core/hardware/peripherals.js 移植)。

从统一模型推断 MCU、成组识别总线(I2C/SPI/UART/SWD)、GPIO 角色与极性、
电源轨摘要、引脚冲突,并生成 summaryMarkdown(给 LLM 的中文硬件上下文)。

纯函数:不访问网络/文件系统,畸形输入尽量返回带 issues 的结果而不抛出。
模型/分析结果沿用 JS 版的 camelCase 键名,保证 JSON 契约一致。
"""

import re

from trae_agent.hardware.common import (
    auto_net_suffix,
    is_auto_net_name,
    is_ground_net_name,
    is_positive_rail_name,
    is_rail_net_name,
    normalize_net_name,
    parse_mcu_pin_name,
)

# MCU 型号正则(ARCHITECTURE 第 5 节)。
MCU_PART_RE = re.compile(
    r"(STM32|GD32|CH32|NRF|ESP32|ESP8266|ATMEGA|ATTINY|RP2040|MSP430|HC32|HT32)[A-Za-z0-9]*", re.I
)


def _mcu_part_token(value: object) -> str:
    """取 value 中匹配 MCU 正则的首个 token(大写);不匹配返回 None。"""
    m = MCU_PART_RE.search(str(value if value is not None else ""))
    return m.group(0).upper() if m else None


# 传感器/特殊外设库:value 匹配 → 需要的总线与描述。
SENSOR_LIB: list[dict] = [
    {
        "re": re.compile(r"BMP280|BMP388|BME280|BME680", re.I),
        "bus": "i2c",
        "part": "气压/温湿度传感器",
    },
    {
        "re": re.compile(r"MPU6050|MPU6500|MPU9250|ICM-?20[0-9]{3}|LSM6|LSM9", re.I),
        "bus": "i2c",
        "part": "六轴/九轴 IMU",
    },
    {"re": re.compile(r"DS3231|DS1307|PCF8563", re.I), "bus": "i2c", "part": "RTC 实时时钟"},
    {
        "re": re.compile(r"SHT[23][0-9]|HTU21|SI7021|AHT[12]0", re.I),
        "bus": "i2c",
        "part": "温湿度传感器",
    },
    {"re": re.compile(r"AT24C|24LC|24AA", re.I), "bus": "i2c", "part": "EEPROM"},
    {
        "re": re.compile(r"ADS1[01]15|PCF8591|INA219|INA226", re.I),
        "bus": "i2c",
        "part": "ADC/电流采样芯片",
    },
    {"re": re.compile(r"MAX7219|IS31FL", re.I), "bus": "i2c", "part": "LED 驱动"},
    {"re": re.compile(r"SSD1306|SH1106", re.I), "bus": "i2c", "part": "OLED 显示屏(I2C)"},
    {"re": re.compile(r"ST7735|ILI9341|ST7789", re.I), "bus": "spi", "part": "TFT 显示屏"},
    {"re": re.compile(r"W25Q|GD25|MX25|AT25|IS25", re.I), "bus": "spi", "part": "SPI Flash"},
    {"re": re.compile(r"SDCARD|SD_CARD|MICROSD|TF-?CARD", re.I), "bus": "spi", "part": "SD 卡插槽"},
    {"re": re.compile(r"MFRC522|RC522", re.I), "bus": "spi", "part": "RFID 读卡器"},
    {"re": re.compile(r"MAX31855|MAX6675", re.I), "bus": "spi", "part": "热电偶转换芯片"},
    {"re": re.compile(r"ENC28J60|W5500|WIZNET", re.I), "bus": "spi", "part": "以太网控制器"},
]


# ---------------- 器件角色判别(纯谓词) ----------------


def is_crystal_component(comp: dict) -> bool:
    """晶振/谐振器判别。"""
    t = str(comp.get("type") or "").lower()
    if t in ("crystal", "xtal", "resonator"):
        return True
    v = str(comp.get("value") or "").strip()
    if re.search(r"(?:^|[^A-Za-z])(?:CRYSTAL|XTAL|RESONATOR)(?:[^A-Za-z]|$)", v, re.I):
        return True
    return bool(re.match(r"^\d+(\.\d+)?\s*(MHZ|KHZ)\b", v, re.I))


def is_led_component(comp: dict) -> bool:
    """LED 判别。"""
    t = str(comp.get("type") or "").lower()
    if t in ("led", "diode-led"):
        return True
    v = str(comp.get("value") or "").strip()
    if re.match(r"^LED\b|LED[-_ ]", v, re.I) or re.match(r"^LED", v, re.I):
        return True
    return bool(
        re.fullmatch(r"(D|LED)\d*", str(comp.get("ref") or ""), re.I) and re.search(r"LED", v, re.I)
    )


def is_button_component(comp: dict) -> bool:
    """按键/开关判别(注意 SWD 调试口位号不算按键)。"""
    t = str(comp.get("type") or "").lower()
    if t in ("button", "switch", "pushbutton", "tact"):
        return True
    s = f"{comp.get('value') or ''}"
    if re.search(r"\b(button|switch|sw[-_ ]?push|tact|pushbutton|key)\b", s, re.I):
        return True
    return bool(
        re.fullmatch(r"SW\d+", str(comp.get("ref") or ""), re.I)
        and not re.search(r"SWD", str(comp.get("ref") or ""))
    )


def is_regulator_component(comp: dict) -> bool:
    """稳压器判别。"""
    t = str(comp.get("type") or "").lower()
    if t in ("regulator", "ldo"):
        return True
    return bool(
        re.search(
            r"\b(LDO|REGULATOR|AMS1117|LM1117|78L05|LT\d{4}|TPS[0-9]{4}|AP2112|MCP17\d{2}|NCP\d{3,4})\b",
            str(comp.get("value") or ""),
            re.I,
        )
    )


def is_usb_component(comp: dict) -> bool:
    """USB 连接器判别。"""
    t = str(comp.get("type") or "").lower()
    if t in ("usb", "usb-connector"):
        return True
    return bool(
        re.search(r"\b(USB|TYPE-?C|USB-?C|MINIUSB|MICROUSB)\b", str(comp.get("value") or ""), re.I)
    )


def is_capacitor_component(comp: dict) -> bool:
    """电容判别(无 type 时按 C<数字> 位号兜底)。"""
    t = str(comp.get("type") or "").lower()
    if t in ("c", "capacitor", "cap"):
        return True
    if comp.get("type") is None:
        return bool(re.fullmatch(r"C\d+", str(comp.get("ref") or ""), re.I))
    return False


def led_semantics(comp: dict) -> dict:
    """LED 引脚语义:{ anodePin, anodeNet, kathodePin, kathodeNet }。"""
    pins = comp.get("pins") or []
    anode = next(
        (p for p in pins if re.fullmatch(r"(anode|a|\+|1)", str(p.get("pin") or ""), re.I)), None
    )
    kathode = next(
        (
            p
            for p in pins
            if re.fullmatch(r"(cathode|kathode|k|-|2)", str(p.get("pin") or ""), re.I)
        ),
        None,
    )
    return {
        "anodePin": anode.get("pin") if anode else None,
        "anodeNet": anode.get("net") if anode else None,
        "kathodePin": kathode.get("pin") if kathode else None,
        "kathodeNet": kathode.get("net") if kathode else None,
    }


# ---------------- MCU 识别 ----------------


def identify_mcu(model: dict | None) -> dict | None:
    """MCU 识别:

    1) board-yaml 的 model.mcu(ref/part) 直接生效;
    2) components 的 value 匹配 MCU 正则者中引脚最多者;
    3) 兜底:引脚最多的非无源 U* 器件。
    """
    comps = (model or {}).get("components") or []

    def ref_of(c: dict) -> str:
        return str(c.get("ref") or "")

    mcu = (model or {}).get("mcu") or None
    if mcu and mcu.get("ref"):
        comp = next((c for c in comps if ref_of(c) == str(mcu["ref"])), None)
        part = mcu.get("part") or (comp.get("value") if comp else "") or ""
        if comp is not None:
            return {
                "ref": comp["ref"],
                "part": part or "unknown",
                "pinCount": len(comp.get("pins") or []),
                "matched": True,
            }
        # 按 part token 与 components 的 value 匹配(不匹配时 token 为 None → 跳过,与 JS 语义一致且防崩)
        part_token = _mcu_part_token(part) if part else None
        by_part = None
        if part and part_token is not None:
            by_part = next(
                (
                    c
                    for c in comps
                    if _mcu_part_token(c.get("value")) is not None
                    and _mcu_part_token(c.get("value")) == part_token
                ),
                None,
            )
        if by_part is not None:
            return {
                "ref": by_part["ref"],
                "part": part,
                "pinCount": len(by_part.get("pins") or []),
                "matched": True,
            }
        if part:
            return {
                "ref": str(mcu["ref"]),
                "part": part,
                "pinCount": 0,
                "matched": True,
                "note": f"mcu.ref={mcu['ref']} 不在 components 列表中,按声明使用",
            }
    matched = [
        c
        for c in comps
        if MCU_PART_RE.search(str(c.get("value") or ""))
        and not re.fullmatch(r"(R|C|L|D)\d*", ref_of(c), re.I)
    ]
    matched.sort(key=lambda c: -(len(c.get("pins") or [])))
    if len(matched) > 0:
        part = _mcu_part_token(matched[0].get("value"))
        return {
            "ref": matched[0]["ref"],
            "part": part,
            "pinCount": len(matched[0].get("pins") or []),
            "matched": True,
        }
    candidates = [
        c
        for c in comps
        if re.fullmatch(r"U[A-Za-z0-9]*", ref_of(c), re.I) and len(c.get("pins") or []) >= 2
    ]
    if len(candidates) == 0:
        return None
    candidates.sort(key=lambda c: -(len(c.get("pins") or [])))
    return {
        "ref": candidates[0]["ref"],
        "part": str(candidates[0].get("value") or candidates[0].get("ref") or "unknown").upper(),
        "pinCount": len(candidates[0].get("pins") or []),
        "matched": False,
        "note": "MCU 型号未匹配已知列表,以引脚最多的 U* 器件兜底(型号可能不准)",
    }


# ---------------- 网络 → 协议功能 ----------------


def net_function(net_name: object) -> dict | None:
    """归一化后的裸网络名 → 协议功能(不做成组判定,自动命名网络不参与)。"""
    raw = str(net_name if net_name is not None else "")
    n = normalize_net_name(raw)
    if not n or is_auto_net_name(raw):
        return None
    if re.fullmatch(r"SDA|SDI|I2C\d*_?SDA|.*_SDA", n):
        return {"kind": "i2c", "signal": "sda"}
    if re.fullmatch(r"SCL|I2C\d*_?SCL|.*_SCL", n):
        return {"kind": "i2c", "signal": "scl"}
    if re.fullmatch(r"MOSI|SPI\d*_?MOSI|.*_MOSI", n):
        return {"kind": "spi", "signal": "mosi"}
    if re.fullmatch(r"MISO|SPI\d*_?MISO|.*_MISO", n):
        return {"kind": "spi", "signal": "miso"}
    if re.fullmatch(r"SCK|SPI\d*_?SCK|.*_SCK", n):
        return {"kind": "spi", "signal": "sck"}
    if re.fullmatch(r"CS|NSS|CSN|SPI\d*_?CS|.*_CS|.*_NSS", n):
        return {"kind": "spi", "signal": "cs"}
    if re.fullmatch(r"TXD?|TX\d+|UART\d*_?TX|.*_TX", n):
        return {"kind": "uart", "signal": "tx"}
    if re.fullmatch(r"RXD?|RX\d+|UART\d*_?RX|.*_RX", n):
        return {"kind": "uart", "signal": "rx"}
    if re.fullmatch(r"SWDIO|SWD_?DIO", n):
        return {"kind": "swd", "signal": "swdio"}
    if re.fullmatch(r"SWCLK|SWD_?CLK", n):
        return {"kind": "swd", "signal": "swclk"}
    if re.fullmatch(r"NRST|NRESET|RST#?|RESET", n):
        return {"kind": "reset", "signal": "nrst"}
    if re.fullmatch(r"BOOT0|EN|ENABLE", n):
        return {"kind": "control", "signal": "boot0"}
    return None


def signal_name(net_name: object) -> str:
    """净化 net 名 → 信号宏名(自动名 Net-(D1-Pad2) → D1_PAD2)。"""
    if is_auto_net_name(net_name):
        return auto_net_suffix(net_name)
    n = normalize_net_name(net_name)
    n = re.sub(r"[^A-Z0-9_]", "_", n)
    n = re.sub(r"_+", "_", n)
    n = re.sub(r"^_+|_+$", "", n)
    return n or "SIG"


# ---------------- 角色汇总 ----------------


def detect_roles(model: dict | None, mcu_ref: str | None) -> dict:
    """一次扫描分类所有器件角色(排除 MCU 自身)。"""
    roles: dict[str, list] = {
        "leds": [],
        "buttons": [],
        "crystals": [],
        "sensors": [],
        "regulators": [],
        "usbs": [],
        "passives": [],
    }
    for comp in (model or {}).get("components") or []:
        if mcu_ref is not None and str(comp.get("ref")) == str(mcu_ref):
            continue
        if is_led_component(comp):
            roles["leds"].append(comp)
        elif is_button_component(comp):
            roles["buttons"].append(comp)
        if is_crystal_component(comp):
            roles["crystals"].append(comp)
        sensor = next((s for s in SENSOR_LIB if s["re"].search(str(comp.get("value") or ""))), None)
        if sensor is not None:
            roles["sensors"].append({"comp": comp, "sensor": sensor})
        if is_regulator_component(comp):
            roles["regulators"].append(comp)
        if is_usb_component(comp):
            roles["usbs"].append(comp)
        if re.fullmatch(r"[RCL][A-Za-z0-9]*", str(comp.get("ref") or "")) or str(
            comp.get("type") or ""
        ) in ("R", "C", "L"):
            roles["passives"].append(comp)
    return roles


# ---------------- 自然排序辅助(等价 JS localeCompare numeric) ----------------


def _natural_key(s: str) -> list:
    """自然排序键:文本段按字典序、数字段按数值(等价 JS 的 localeCompare(..., { numeric: true }))。"""
    parts = re.split(r"(\d+)", s)
    return [int(p) if i % 2 == 1 and p else p for i, p in enumerate(parts)]


# ---------------- 主分析 ----------------


def analyze_hardware(model: dict | None) -> dict:
    """analyzeHardware(model) -> Analysis。"""
    analysis: dict = {
        "mcu": None,
        "buses": [],
        "gpio": [],
        "power": {"rails": [], "decouplingCount": 0},
        "pinmap": [],
        "issues": [],
        "summaryMarkdown": "",
    }
    if not model or not isinstance(model.get("components"), list):
        analysis["issues"].append("硬件模型为空或结构不合法")
        analysis["summaryMarkdown"] = build_summary(model, analysis)
        return analysis

    # ---- 状态 ----
    issues = analysis["issues"]
    bus_pins: set[str] = set()  # 已被总线占用的 MCU 引脚
    gpio_pins: set[str] = set()  # 已被 gpio 角色占用的 MCU 引脚
    pin_claim: dict[str, str] = {}  # 引脚 → 首次占用描述(冲突检测)

    def claim_pin(pin: object, label: str, kind: str) -> None:
        """占用 MCU 引脚;重复占用不同功能 → 记冲突 issue。kind:'bus'|'gpio'。"""
        if pin is None:
            return
        key = str(pin)
        prev = pin_claim.get(key)
        if prev is not None and prev != label:
            issues.append(
                f"MCU 引脚冲突:{analysis['mcu']['ref']} 的 {pin} 同时被 {prev} 与 {label} 占用(请检查网表连线或网络命名)"
            )
        elif prev is None:
            pin_claim[key] = label
        if kind == "bus":
            bus_pins.add(key)
        else:
            gpio_pins.add(key)

    # ---- MCU ----
    mcu = identify_mcu(model)
    analysis["mcu"] = mcu
    if mcu is None:
        issues.append("未找到 MCU(value/part 无已知型号,也没有可作兜底的 U* 器件),无法做外设推断")
        analysis["summaryMarkdown"] = build_summary(model, analysis)
        return analysis

    # ---- 索引 ----
    net_by_norm: dict[str, dict] = {}
    for net in model.get("nets") or []:
        k = normalize_net_name(net.get("name"))
        if k not in net_by_norm:
            net_by_norm[k] = net

    def find_net(name: str) -> dict | None:
        return net_by_norm.get(normalize_net_name(name))

    comp_by_ref = {str(c.get("ref")): c for c in model["components"]}

    # MCU 引脚表(pin → net),同名引脚只取第一个(过滤重复电源脚)。
    mcu_pin_list: list[dict] = []
    seen_mcu_pin_names: set[str] = set()
    mcu_ref_str = str(mcu["ref"])
    for net in model.get("nets") or []:
        for conn in net.get("connections") or []:
            if str(conn.get("ref")) != mcu_ref_str:
                continue
            if conn.get("pin") in seen_mcu_pin_names:
                continue
            seen_mcu_pin_names.add(conn.get("pin"))
            mcu_pin_list.append({"pin": conn.get("pin"), "net": net.get("name")})

    def find_mcu_port_pin_on_net(net_name: str) -> str | None:
        """指定网络上是否有 MCU 的端口型引脚;返回引脚名或 None。"""
        net = find_net(net_name)
        if net is None:
            return None
        for conn in net.get("connections") or []:
            if str(conn.get("ref")) != mcu_ref_str:
                continue
            pp = parse_mcu_pin_name(conn.get("pin"))
            if pp is not None and pp.kind == "gpio":
                return conn.get("pin")
        return None

    def net_peers(net_name: str) -> list[dict]:
        """同一网络上的非 MCU 器件(去重)。"""
        net = find_net(net_name)
        if net is None:
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for c in net.get("connections") or []:
            r = str(c.get("ref"))
            if r == mcu_ref_str or r in seen:
                continue
            seen.add(r)
            out.append(c)
        return out

    def devices_on_bus(net_names: list[str]) -> list[dict]:
        """总线上的设备(非 MCU、非基础无源器件)。"""
        devices: list[dict] = []
        seen: set[str] = set()
        for net_name in net_names:
            for peer in net_peers(net_name):
                comp = comp_by_ref.get(str(peer.get("ref")))
                if comp is None or peer.get("ref") in seen:
                    continue
                looks_passive = re.fullmatch(
                    r"[RCLD]\d*", str(comp.get("ref") or ""), re.I
                ) and not any(s["re"].search(str(comp.get("value") or "")) for s in SENSOR_LIB)
                if looks_passive:
                    continue
                seen.add(peer.get("ref"))
                devices.append(
                    {
                        "ref": comp["ref"],
                        "part": str(comp.get("value") or comp.get("type") or comp.get("ref")),
                    }
                )
        return devices

    def peers_overlap(net_names_a: list[str], net_names_b: list[str]) -> bool:
        """两组网络是否共享非 MCU 器件(用于 CS 归属)。"""
        refs_a: set[str] = set()
        for n in net_names_a:
            for p in net_peers(n):
                refs_a.add(str(p.get("ref")))
        for n in net_names_b:
            for p in net_peers(n):
                if str(p.get("ref")) in refs_a:
                    return True
        return False

    # ---- 电源轨 ----
    rail_count: dict[str, int] = {}
    for net in model.get("nets") or []:
        if not is_rail_net_name(net.get("name")):
            continue
        rail_count[normalize_net_name(net.get("name"))] = len(net.get("connections") or [])
    rails = [
        {"name": (net_by_norm.get(k) or {}).get("name") or k, "connections": v}
        for k, v in rail_count.items()
    ]
    rails.sort(key=lambda r: (-r["connections"], r["name"]))
    analysis["power"]["rails"] = rails
    if len(analysis["power"]["rails"]) == 0:
        issues.append("未识别到电源轨(网表中没有 GND/VCC/3V3 等电源网络)")

    # ---- 退耦电容(0.1u/100n 级,两端分别接正电源轨与地) ----
    roles = detect_roles(model, mcu.get("ref"))
    decoupling_count = 0
    for comp in model["components"]:
        if not is_capacitor_component(comp):
            continue
        v = str(comp.get("value") or "").strip()
        nets_of_pins = [
            p.get("net")
            for p in (comp.get("pins") or [])
            if p.get("net") is not None and p.get("net") != ""
        ]
        has_rail = any(is_positive_rail_name(n) for n in nets_of_pins)
        has_gnd = any(is_ground_net_name(n) for n in nets_of_pins)
        small = bool(re.fullmatch(r"(100n|100nf|0\.1u|0\.1uf|0\.1)", v, re.I))
        bulk = bool(
            re.fullmatch(r"(1u|1uf|4\.7u|4\.7uf|10u|10uf|22u|22uf|47u|47uf|100u|100uf)", v, re.I)
        )
        if has_rail and has_gnd and (small or bulk):
            decoupling_count += 1
    analysis["power"]["decouplingCount"] = decoupling_count
    if decoupling_count == 0 and any(is_capacitor_component(c) for c in model["components"]):
        issues.append("未发现退耦电容(电源轨与地之间的 0.1u/100n 电容),建议每个电源引脚就近加退耦")

    # ---- 协议信号采集:MCU 引脚所在网络名 → 功能;自动命名网络回退看对端引脚名 ----
    protocol_signals: list[dict] = []
    for mp in mcu_pin_list:
        fn = net_function(mp["net"])
        if fn is not None:
            protocol_signals.append({"net": mp["net"], "pin": mp["pin"], **fn, "auto": False})
            continue
        if is_auto_net_name(mp["net"]):
            for peer in net_peers(mp["net"]):
                pf = net_function(str(peer.get("pin")))
                if pf is not None and pf["kind"] in ("i2c", "spi", "uart", "swd"):
                    protocol_signals.append(
                        {
                            "net": mp["net"],
                            "pin": mp["pin"],
                            **pf,
                            "auto": True,
                            "fromPeer": f"{peer.get('ref')}.{peer.get('pin')}",
                        }
                    )
                    break

    # ---- 成组总线判定 ----
    buses: list[dict] = []
    af_instance = {"i2c": 0, "spi": 0, "uart": 0}

    def sig(kind: str, s: str) -> list[dict]:
        return [x for x in protocol_signals if x["kind"] == kind and x["signal"] == s]

    # I2C: SDA + SCL
    sdas = sig("i2c", "sda")
    scls = sig("i2c", "scl")
    pairs = min(len(sdas), len(scls))
    for i in range(pairs):
        inst = min(af_instance["i2c"] + 1, 2)
        af_instance["i2c"] += 1
        note = (
            "I2C 外设编号为推断值(多组 I2C 时请按芯片数据手册核对)"
            if inst > 1
            else "I2C 成组识别(SDA+SCL 同在 MCU 上),外设编号为推断值,请按芯片数据手册核对"
        )
        bus = {
            "type": "i2c",
            "pins": {"sda": sdas[i]["pin"], "scl": scls[i]["pin"]},
            "devices": devices_on_bus([sdas[i]["net"], scls[i]["net"]]),
            "note": note,
            "_signals": [sdas[i], scls[i]],
            "_af": f"I2C{inst}",
        }
        if sdas[i].get("auto") or scls[i].get("auto"):
            bus["note"] = "该总线经由对端器件引脚名推断(网络为自动命名),AF 为推断值"
        buses.append(bus)
        claim_pin(sdas[i]["pin"], f"i2c{inst}_sda", "bus")
        claim_pin(scls[i]["pin"], f"i2c{inst}_scl", "bus")
    for s in sdas[pairs:] + scls[pairs:]:
        issues.append(
            f'网络 "{s["net"]}" 匹配 I2C {s["signal"].upper()} 关键字但未成组(缺少对应的 {"SCL" if s["signal"] == "sda" else "SDA"}),按 GPIO 处理'
        )

    # SPI: SCK + MOSI (+MISO), CS 归属同设备组
    scks = sig("spi", "sck")
    mosis = sig("spi", "mosi")
    misos = sig("spi", "miso")
    css = sig("spi", "cs")
    taken: set[str] = set()
    for i in range(min(len(scks), len(mosis))):
        inst = min(af_instance["spi"] + 1, 2)
        af_instance["spi"] += 1
        bus_nets = [scks[i]["net"], mosis[i]["net"]]
        bus = {
            "type": "spi",
            "pins": {"sck": scks[i]["pin"], "mosi": mosis[i]["pin"]},
            "devices": [],
            "note": "SPI 成组识别(SCK+MOSI,MISO 可选),外设编号为推断值",
            "_signals": [scks[i], mosis[i]],
            "_af": f"SPI{inst}",
        }
        if i < len(misos):
            bus["pins"]["miso"] = misos[i]["pin"]
            bus["_signals"].append(misos[i])
            bus_nets.append(misos[i]["net"])
        my_cs = next(
            (c for c in css if c["pin"] not in taken and peers_overlap(bus_nets, [c["net"]])), None
        )
        if my_cs is not None:
            taken.add(my_cs["pin"])
            bus["pins"]["cs"] = my_cs["pin"]
            bus["_signals"].append(my_cs)
            bus_nets.append(my_cs["net"])
        else:
            bus["note"] += ";未识别到片选线(CS)"
        bus["devices"] = devices_on_bus(bus_nets)
        buses.append(bus)
        for s in bus["_signals"]:
            claim_pin(s["pin"], f"spi{inst}_{s['signal']}", "bus")
    bus_spi_pins: set[str] = set()
    for b in buses:
        if b["type"] == "spi":
            bus_spi_pins.update(str(v) for v in (b.get("pins") or {}).values())
    for s in [
        x for x in protocol_signals if x["kind"] == "spi" and str(x["pin"]) not in bus_spi_pins
    ]:
        issues.append(
            f'网络 "{s["net"]}" 匹配 SPI {s["signal"].upper()} 关键字但未成组(缺少 SCK/MOSI),按 GPIO 处理'
        )

    # UART: TX + RX
    txs = sig("uart", "tx")
    rxs = sig("uart", "rx")
    pairs = min(len(txs), len(rxs))
    for i in range(pairs):
        inst = min(af_instance["uart"] + 1, 3)
        af_instance["uart"] += 1
        bus = {
            "type": "uart",
            "pins": {"tx": txs[i]["pin"], "rx": rxs[i]["pin"]},
            "devices": devices_on_bus([txs[i]["net"], rxs[i]["net"]]),
            "note": "UART 成组识别(TX+RX),外设编号为推断值,请按芯片实际 USART 编号核对",
            "_signals": [txs[i], rxs[i]],
            "_af": f"USART{inst}",
        }
        buses.append(bus)
        claim_pin(txs[i]["pin"], f"uart{inst}_tx", "bus")
        claim_pin(rxs[i]["pin"], f"uart{inst}_rx", "bus")
    for s in txs[pairs:] + rxs[pairs:]:
        issues.append(
            f'网络 "{s["net"]}" 匹配 UART {s["signal"].upper()} 关键字但未成组(缺少对应的 {"RX" if s["signal"] == "tx" else "TX"}),按 GPIO 处理'
        )

    # SWD: SWDIO + SWCLK
    dios = sig("swd", "swdio")
    clks = sig("swd", "swclk")
    pairs = min(len(dios), len(clks))
    for i in range(pairs):
        bus = {
            "type": "swd",
            "pins": {"swdio": dios[i]["pin"], "swclk": clks[i]["pin"]},
            "devices": [{"ref": "SWD", "part": "调试接口"}],
            "note": "SWD 调试口(SWDIO+SWCLK 成组)",
            "_signals": [dios[i], clks[i]],
            "_af": "SWD",
        }
        buses.append(bus)
        claim_pin(dios[i]["pin"], "swd_swdio", "bus")
        claim_pin(clks[i]["pin"], "swd_swclk", "bus")
    for s in dios[pairs:] + clks[pairs:]:
        issues.append(
            f'网络 "{s["net"]}" 匹配 SWD {s["signal"].upper()} 关键字但未成组,按 GPIO 处理'
        )

    bus_direction = {
        "sda": "alternate",
        "scl": "alternate",
        "sck": "out",
        "mosi": "out",
        "miso": "in",
        "cs": "out",
        "tx": "out",
        "rx": "in",
        "swdio": "alternate",
        "swclk": "out",
    }

    # ---- GPIO 角色判定 ----
    gpio_signals: list[dict] = []

    # 按键(先于 LED 等,便于冲突信息先落)
    for btn in roles["buttons"]:
        chosen: dict | None = None
        for p in btn.get("pins") or []:
            if p.get("net") is None or p.get("net") == "" or is_rail_net_name(p.get("net")):
                continue
            mp = find_mcu_port_pin_on_net(p.get("net"))
            if mp is not None:
                chosen = {"pin": mp, "net": p.get("net")}
                break
        if chosen is None:
            continue
        if chosen["pin"] in bus_pins:
            issues.append(
                f"MCU {mcu['ref']} 的 {chosen['pin']} 同时出现在总线与按键网络({chosen['net']}),按冲突处理,按键未生成 GPIO"
            )
            continue
        other_net = next(
            (
                p.get("net")
                for p in (btn.get("pins") or [])
                if p.get("net") is not None
                and normalize_net_name(p.get("net")) != normalize_net_name(chosen["net"])
            ),
            None,
        )
        is_reset = any(
            re.fullmatch(r"(NRST|NRESET|RST#?|RESET)", str(p.get("net") or "").strip(), re.I)
            for p in (btn.get("pins") or [])
        )
        gpio_signals.append(
            {
                "ref": mcu["ref"],
                "pin": chosen["pin"],
                "net": chosen["net"],
                "role": "reset-button" if is_reset else "button",
                "direction": "in",
                "activeLow": is_ground_net_name(other_net or ""),
                "deviceRef": btn.get("ref"),
                "label": f"{'复位按键' if is_reset else '按键'}({btn.get('value') or btn.get('ref')})",
            }
        )
        claim_pin(
            chosen["pin"], f"{'reset-button' if is_reset else 'button'}({btn.get('ref')})", "gpio"
        )

    # LED:极性由 anode/kathode 与电源轨关系判定
    for led in roles["leds"]:
        semantics = led_semantics(led)
        anode_net = semantics["anodeNet"]
        kathode_net = semantics["kathodeNet"]
        mcu_pin: str | None = None
        active_low: bool | None = None
        net_name: str | None = None
        if anode_net is not None and kathode_net is not None:
            a_rail = is_positive_rail_name(anode_net)
            k_gnd = is_ground_net_name(kathode_net)
            if a_rail and not is_ground_net_name(kathode_net):
                mcu_pin = find_mcu_port_pin_on_net(kathode_net)
                active_low = True  # 阳极接正电源,MCU 引脚拉低灌电流点亮
                net_name = kathode_net
            elif k_gnd and not a_rail:
                mcu_pin = find_mcu_port_pin_on_net(anode_net)
                active_low = False  # 阴极接地,MCU 引脚拉高点亮
                net_name = anode_net
            elif a_rail and k_gnd:
                mcu_pin = None  # 两端都接固定电平:常亮电源灯,与 MCU 无关
            else:
                pk = find_mcu_port_pin_on_net(kathode_net)
                pa = find_mcu_port_pin_on_net(anode_net)
                if pk is not None and pa is None:
                    mcu_pin = pk
                    net_name = kathode_net
                    active_low = None
                elif pa is not None and pk is None:
                    mcu_pin = pa
                    net_name = anode_net
                    active_low = None
                elif pa is not None and pk is not None:
                    mcu_pin = pa
                    net_name = anode_net
                    active_low = None
                    issues.append(
                        f"LED {led.get('ref')} 两端都接到 MCU 引脚({pa}/{pk}),极性无法判定,已按 {pa} 处理"
                    )
        elif kathode_net is not None:
            p = find_mcu_port_pin_on_net(kathode_net)
            if p is not None:
                mcu_pin = p
                net_name = kathode_net
        elif anode_net is not None:
            p = find_mcu_port_pin_on_net(anode_net)
            if p is not None:
                mcu_pin = p
                net_name = anode_net
        else:
            # 引脚名不规范(A/K 之外):尝试任一非电源轨引脚
            for p in led.get("pins") or []:
                if p.get("net") is None or is_rail_net_name(p.get("net")):
                    continue
                mp = find_mcu_port_pin_on_net(p.get("net"))
                if mp is not None:
                    mcu_pin = mp
                    net_name = p.get("net")
                    active_low = None
                    break
        if mcu_pin is None:
            continue
        if mcu_pin in bus_pins:
            issues.append(
                f"MCU {mcu['ref']} 的 {mcu_pin} 同时出现在总线与 LED({led.get('ref')})网络({net_name}),按冲突处理,LED 未生成 GPIO"
            )
            continue
        gpio_signals.append(
            {
                "ref": mcu["ref"],
                "pin": mcu_pin,
                "net": net_name,
                "role": "led",
                "direction": "out",
                "activeLow": active_low,
                "deviceRef": led.get("ref"),
                "label": f"LED({led.get('value') or led.get('ref')})",
            }
        )
        claim_pin(mcu_pin, f"led({led.get('ref')})", "gpio")

    # 晶振:不占用 gpio 角色,进 pinmap(标记 crystal)
    crystal_pins: list[dict] = []
    for xtal in roles["crystals"]:
        for p in xtal.get("pins") or []:
            mp = find_mcu_port_pin_on_net(p.get("net")) if p.get("net") is not None else None
            if mp is not None:
                crystal_pins.append(
                    {
                        "pin": mp,
                        "net": p.get("net"),
                        "deviceRef": xtal.get("ref"),
                        "value": xtal.get("value"),
                    }
                )
                claim_pin(mp, f"crystal({xtal.get('ref')})", "gpio")

    # reset / boot0 / en 控制线 → gpio(direction in)
    for s in [x for x in protocol_signals if x["kind"] in ("reset", "control")]:
        if s["pin"] in bus_pins or s["pin"] in gpio_pins:
            continue
        gpio_signals.append(
            {
                "ref": mcu["ref"],
                "pin": s["pin"],
                "net": s["net"],
                "role": "reset" if s["signal"] == "nrst" else "control",
                "direction": "in",
                "activeLow": s["signal"] == "nrst",
                "label": "复位输入(低有效)" if s["signal"] == "nrst" else "启动/使能控制",
            }
        )
        claim_pin(s["pin"], s["signal"], "gpio")

    # 未成组的协议线 → gpio 回退(note 保留原因)
    for s in protocol_signals:
        if s["kind"] not in ("i2c", "spi", "uart", "swd"):
            continue
        if s["pin"] in bus_pins or s["pin"] in gpio_pins:
            continue
        gpio_signals.append(
            {
                "ref": mcu["ref"],
                "pin": s["pin"],
                "net": s["net"],
                "role": "gpio",
                "direction": "in",
                "activeLow": False,
                "label": f"{s['kind'].upper()} {s['signal'].upper()}(未成组)",
                "note": f'网络 "{s["net"]}" 匹配 {s["kind"].upper()} 协议关键字但未成组,已按 GPIO 处理',
            }
        )
        claim_pin(s["pin"], f"{s['kind']}:{s['signal']}", "gpio")

    # 传感器不在任何总线上 → issue 提示
    for entry in roles["sensors"]:
        comp = entry["comp"]
        sensor = entry["sensor"]
        on_bus = any(
            str(d.get("ref")) == str(comp.get("ref"))
            for b in buses
            for d in (b.get("devices") or [])
        )
        if not on_bus:
            issues.append(
                f"传感器 {comp.get('ref')}({comp.get('value')})需要 {sensor['bus'].upper()} 总线,但未出现在任何已识别总线(请检查网络命名或连线)"
            )

    # ---- 汇总 gpio / buses ----
    gpio_signals.sort(key=lambda g: _natural_key(str(g["pin"])))
    analysis["gpio"] = gpio_signals
    bus_order = {"i2c": 0, "spi": 1, "uart": 2, "swd": 3}
    buses.sort(key=lambda b: bus_order.get(b["type"], 9))
    analysis["buses"] = buses

    # ---- pinmap ----
    pinmap: list[dict] = []
    seen_signal_names: dict[str, int] = {}

    def add_pinmap_entry(
        signal: object,
        pin: object,
        af: str | None,
        direction: str | None,
        net_name: object,
        note: str | None,
        is_bus: bool,
        active_low: bool = False,
        role: str | None = None,
        label: str | None = None,
    ) -> None:
        pp = parse_mcu_pin_name(pin)
        if pp is None or pp.kind != "gpio":
            return  # 只有 P[A-K]n 端口引脚进入 pinmap(NRST/VDD 等专用脚不进)
        name = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", str(signal or "")).upper()) or "SIG"
        n = seen_signal_names.get(name, 0) + 1
        seen_signal_names[name] = n
        if n > 1:
            name = f"{name}_{n}"
        pinmap.append(
            {
                "signal": name,
                "port": pp.port,
                "num": pp.num,
                "af": af or "GPIO",
                "direction": direction or "out",
                "net": net_name,
                "role": role,
                "activeLow": bool(active_low),
                "label": label,
                "note": note,
                "isBus": bool(is_bus),
            }
        )

    # 总线引脚(信号名 = 净化后的 net 名)
    for bus in buses:
        for s in bus.get("_signals") or []:
            add_pinmap_entry(
                signal_name(s["net"]),
                s["pin"],
                bus.get("_af") or "GPIO",
                bus_direction.get(s["signal"], "alternate"),
                s["net"],
                bus.get("note"),
                True,
                False,
                f"bus-{bus['type']}",
                f"{bus['type'].upper()} {s['signal'].upper()}",
            )
    # gpio 角色(信号名 = 净化后的 net 名)
    for g in analysis["gpio"]:
        add_pinmap_entry(
            signal_name(g["net"]),
            g["pin"],
            "GPIO",
            g["direction"],
            g["net"],
            g.get("note"),
            False,
            g.get("activeLow") is True,
            g["role"],
            g.get("label"),
        )
    # 晶振引脚
    for c in crystal_pins:
        add_pinmap_entry(
            signal_name(c["net"]),
            c["pin"],
            "GPIO",
            "alternate",
            c["net"],
            f"晶振 {c.get('value') or c.get('deviceRef')} 引脚(通常为 OSC 专用功能)",
            False,
            False,
            "crystal",
            f"晶振({c.get('deviceRef')})",
        )
    pinmap.sort(key=lambda p: f"{p['port']}{p['num']:03d}")
    analysis["pinmap"] = pinmap

    # ---- 器件完整性与提示 ----
    for comp in model["components"]:
        empty_pins = [
            p for p in (comp.get("pins") or []) if p.get("net") is None or p.get("net") == ""
        ]
        if len(empty_pins) > 0 and len(comp.get("pins") or []) > 0:
            issues.append(
                f"器件 {comp.get('ref')} 有 {len(empty_pins)} 个未连接引脚(可能 N.C. 或网表不完整)"
            )
        # 无源器件只出现在一个网络:另一端可能悬空/未纳入网表(R/C/L 正常应有两端)
        if re.fullmatch(r"[RCL]\d+", str(comp.get("ref") or "")) or str(comp.get("type") or "") in (
            "R",
            "C",
            "L",
        ):
            distinct_nets = {
                p.get("net")
                for p in (comp.get("pins") or [])
                if p.get("net") is not None and p.get("net") != ""
            }
            if len(distinct_nets) == 1:
                issues.append(
                    f"无源器件 {comp.get('ref')} 只出现在 1 个网络中(另一端可能未连接或未纳入网表)"
                )
    for w in (model.get("warnings") or [])[:30]:
        issues.append(f"[warning] {w}")

    analysis["summaryMarkdown"] = build_summary(model, analysis)
    return analysis


# ---------------- summaryMarkdown ----------------


def build_summary(model: dict | None, analysis: dict) -> str:
    """summaryMarkdown:板名/MCU/总线/GPIO/电源/网络连接表/问题(与 hardwareContextPrompt 同源)。"""
    lines: list[str] = []
    lines.append("# 硬件分析结果")
    board_name = (model or {}).get("boardName") or "(未命名板卡)"
    lines.append(f"- 板卡:{board_name}")
    if analysis.get("mcu"):
        lines.append(
            f"- MCU:{analysis['mcu']['ref']} / {analysis['mcu']['part']}({analysis['mcu']['pinCount']} 引脚)"
        )
        if analysis["mcu"].get("note"):
            lines.append(f"  - 注意:{analysis['mcu']['note']}")
    else:
        lines.append("- MCU:未识别")
    lines.append("")
    lines.append("## 总线")
    if len(analysis.get("buses") or []) == 0:
        lines.append("(无)")
    else:
        lines.append("| 类型 | 引脚 | 外挂设备 | 备注 |")
        lines.append("|---|---|---|---|")
        for b in analysis["buses"]:
            pins = " ".join(f"{k.upper()}={v}" for k, v in (b.get("pins") or {}).items())
            devices = (
                ", ".join(f"{d['ref']}({d['part']})" for d in (b.get("devices") or [])) or "(无)"
            )
            lines.append(f"| {b['type'].upper()} | {pins} | {devices} | {b.get('note') or ''} |")
    lines.append("")
    lines.append("## GPIO")
    if len(analysis.get("gpio") or []) == 0:
        lines.append("(无)")
    else:
        lines.append("| MCU 引脚 | 网络 | 角色 | 方向 | 极性 | 器件 |")
        lines.append("|---|---|---|---|---|---|")
        for g in analysis["gpio"]:
            pol = (
                "低电平有效"
                if g.get("activeLow") is True
                else ("高电平有效" if g.get("activeLow") is False else "未定")
            )
            who = (
                f"{g.get('deviceRef')}({g.get('label') or ''})"
                if g.get("deviceRef")
                else (g.get("label") or "")
            )
            lines.append(
                f"| {g['ref']}.{g['pin']} | {g['net']} | {g['role']} | {g['direction']} | {pol} | {who} |"
            )
    lines.append("")
    lines.append("## 电源")
    rails = (analysis.get("power") or {}).get("rails") or []
    if len(rails) == 0:
        lines.append("- 电源轨:未识别")
    else:
        lines.append(
            "- 电源轨:" + "、".join(f"{r['name']}({r['connections']} 连接)" for r in rails)
        )
    lines.append(
        f"- 退耦电容:{(analysis.get('power') or {}).get('decouplingCount', 0)} 个(0.1u/100n 级)"
    )
    if model:
        roles2 = detect_roles(model, (analysis.get("mcu") or {}).get("ref"))
    else:
        roles2 = {"regulators": [], "usbs": [], "crystals": []}
    if len(roles2["regulators"]) > 0:
        lines.append(
            "- 电源器件:"
            + "、".join(
                f"{r['ref']}({r.get('value') or r.get('type')})" for r in roles2["regulators"]
            )
        )
    if len(roles2["usbs"]) > 0:
        lines.append(
            "- USB 接口:"
            + "、".join(f"{r['ref']}({r.get('value') or r.get('type')})" for r in roles2["usbs"])
        )
    if len(roles2["crystals"]) > 0:
        lines.append(
            "- 晶振:"
            + "、".join(
                f"{r['ref']}({r.get('value') or r.get('type')})" for r in roles2["crystals"]
            )
        )
    lines.append("")
    lines.append("## 网络连接表")
    nets = (model or {}).get("nets") or []
    if len(nets) == 0:
        lines.append("(无)")
    else:
        for net in nets:
            conns = ", ".join(f"{c['ref']}.{c['pin']}" for c in (net.get("connections") or []))
            lines.append(f"- `{net['name']}`: {conns or '(悬空)'}")
    lines.append("")
    lines.append("## 问题与提示")
    if len(analysis.get("issues") or []) == 0:
        lines.append("(无)")
    else:
        for iss in analysis["issues"]:
            lines.append(f"- {iss}")
    return "\n".join(lines)
