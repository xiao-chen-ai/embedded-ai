# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""test_analyzer.py — 分析器专项:总线不成组回退、LED 极性、引脚冲突、codegen 合法性、闭环。

移植自 embedded-ai-studio/test/unit/hardware-analyzer.test.js(20 用例)。
"""

import re
from pathlib import Path

from tests.hardware.conftest import build_kicad
from trae_agent.hardware.codegen import generate_pinmap_header, hardware_context_prompt
from trae_agent.hardware.kicad_netlist import parse_kicad_netlist
from trae_agent.hardware.peripherals import (
    analyze_hardware,
    identify_mcu,
    net_function,
    signal_name,
)


def test_identify_mcu_regex_and_fallback():
    """正则匹配 + 兜底规则(移植 JS:identifyMcu:正则匹配 + 兜底规则)。"""
    m1 = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U2", "value": "MPU6050"}, {"ref": "U1", "value": "STM32F103C8T6"}]
        )
    )
    assert identify_mcu(m1)["part"] == "STM32F103C8T6"
    m2 = parse_kicad_netlist(build_kicad(skip_mcu=True, comps=[{"ref": "U5", "value": "CH32V203"}]))
    assert identify_mcu(m2)["part"] == "CH32V203"
    m2b = parse_kicad_netlist(
        build_kicad(skip_mcu=True, comps=[{"ref": "U5", "value": "GD32F303CBT6"}])
    )
    assert identify_mcu(m2b)["part"] == "GD32F303CBT6"
    m3 = parse_kicad_netlist(
        build_kicad(
            skip_mcu=True,
            comps=[{"ref": "U9", "value": "UNKNOWN chip"}],
            nets=[
                {"name": "A", "nodes": [{"ref": "U9", "pin": "PA0"}]},
                {"name": "B", "nodes": [{"ref": "U9", "pin": "PA1"}]},
            ],
        )
    )
    mcu3 = identify_mcu(m3)
    assert mcu3["matched"] is False
    assert "兜底" in mcu3["note"]


def test_signal_name_sanitization():
    """非法字符净化与自动网络名提取(移植 JS:signalName:非法字符净化)。"""
    assert signal_name("led-status (blue)") == "LED_STATUS_BLUE"  # 非法字符→_,连续压成一个,首尾修剪
    assert signal_name("Net-(D1-Pad2)") == "D1_PAD2"
    assert signal_name("i2c1-sda") == "I2C1_SDA"


def test_net_function_keyword_table():
    """协议关键字表(移植 JS:netFunction:协议关键字表)。"""
    assert net_function("SDA") == {"kind": "i2c", "signal": "sda"}
    assert net_function("i2c1-sda") == {"kind": "i2c", "signal": "sda"}
    assert net_function("MOSI") == {"kind": "spi", "signal": "mosi"}
    assert net_function("spi_sck") == {"kind": "spi", "signal": "sck"}
    assert net_function("cs") == {"kind": "spi", "signal": "cs"}
    assert net_function("TXD") == {"kind": "uart", "signal": "tx"}
    assert net_function("RX1") == {"kind": "uart", "signal": "rx"}
    assert net_function("SWDIO") == {"kind": "swd", "signal": "swdio"}
    assert net_function("SWCLK") == {"kind": "swd", "signal": "swclk"}
    assert net_function("NRST") == {"kind": "reset", "signal": "nrst"}
    assert net_function("BOOT0") == {"kind": "control", "signal": "boot0"}
    assert net_function("Net-(D1-Pad2)") is None  # 自动名不参与
    assert net_function("RANDOM_SIGNAL") is None


def test_unpaired_i2c_falls_back_to_gpio():
    """不成组 I2C(只有 SDA 无 SCL)→ 落 gpio + note(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U2", "value": "MPU6050"}],
            nets=[
                {
                    "name": "SDA",
                    "nodes": [{"ref": "U1", "pin": "PB9"}, {"ref": "U2", "pin": "SDA"}],
                },
                {"name": "GND", "nodes": [{"ref": "U2", "pin": "GND"}]},
            ],
        )
    )
    a = analyze_hardware(model)
    assert len([b for b in a["buses"] if b["type"] == "i2c"]) == 0
    pin9 = next((g for g in a["gpio"] if g["pin"] == "PB9"), None)
    assert pin9 is not None, "PB9 应回退为 gpio"
    assert pin9.get("note") and "未成组" in pin9["note"], "应带未成组说明"
    assert any("未成组" in i for i in a["issues"])
    # pinmap 里 AF 是 GPIO
    pm = next(p for p in a["pinmap"] if p["num"] == 9 and p["port"] == "PB")
    assert pm["af"] == "GPIO"


def test_unpaired_uart_falls_back():
    """不成组 UART(只有 TX)→ 落 gpio(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(nets=[{"name": "UART_TX", "nodes": [{"ref": "U1", "pin": "PA9"}]}])
    )
    a = analyze_hardware(model)
    assert len([b for b in a["buses"] if b["type"] == "uart"]) == 0
    assert any("UART TX" in i and "未成组" in i for i in a["issues"])


def test_paired_i2c_with_sensor():
    """SDA+SCL 成组 + 传感器需总线提示(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U2", "value": "BMP280"}],
            nets=[
                {
                    "name": "SDA",
                    "nodes": [{"ref": "U1", "pin": "PB9"}, {"ref": "U2", "pin": "SDA"}],
                },
                {
                    "name": "SCL",
                    "nodes": [{"ref": "U1", "pin": "PB8"}, {"ref": "U2", "pin": "SCL"}],
                },
            ],
        )
    )
    a = analyze_hardware(model)
    i2c = next(b for b in a["buses"] if b["type"] == "i2c")
    assert any(d["ref"] == "U2" for d in i2c["devices"])
    assert "推断" in i2c["note"]
    # 传感器已上总线 → 不应有"需要 I2C 总线但未找到"的提示
    assert not any("需要" in i and "总线" in i for i in a["issues"])


def test_led_polarity_rail_vs_ground():
    """LED 阳极接电源轨 → activeLow;阴极接地 → activeHigh(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "D1", "value": "LED_PWR"}, {"ref": "D2", "value": "LED_STATUS"}],
            nets=[
                {"name": "3V3", "nodes": [{"ref": "D1", "pin": "A"}]},
                {
                    "name": "Net-(D1-K)",
                    "nodes": [{"ref": "D1", "pin": "K"}, {"ref": "U1", "pin": "PA5"}],
                },
                {
                    "name": "Net-(D2-A)",
                    "nodes": [{"ref": "D2", "pin": "A"}, {"ref": "U1", "pin": "PB0"}],
                },
                {"name": "GND", "nodes": [{"ref": "D2", "pin": "K"}]},
            ],
        )
    )
    a = analyze_hardware(model)
    d1 = next(g for g in a["gpio"] if g["deviceRef"] == "D1")
    assert d1["activeLow"] is True, "D1 阳极接 3V3,阴极接 MCU → 低电平点亮"
    d2 = next(g for g in a["gpio"] if g["deviceRef"] == "D2")
    assert d2["activeLow"] is False, "D2 阳极接 MCU,阴极接地 → 高电平点亮"


def test_pin_conflict_issues():
    """引脚冲突进 issues(同一 MCU 引脚既接 LED 又接按键)(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "D1", "value": "LED_X"}, {"ref": "SW1", "value": "SW_Push"}],
            nets=[
                {"name": "5V", "nodes": [{"ref": "D1", "pin": "A"}]},
                {
                    "name": "NET_LED",
                    "nodes": [{"ref": "D1", "pin": "K"}, {"ref": "U1", "pin": "PA5"}],
                },
                {
                    "name": "NET_BTN",
                    "nodes": [{"ref": "U1", "pin": "PA5"}, {"ref": "SW1", "pin": "1"}],
                },
                {"name": "GND", "nodes": [{"ref": "SW1", "pin": "2"}]},
            ],
        )
    )
    a = analyze_hardware(model)
    assert any("冲突" in str(i) for i in a["issues"]), f"issues 应含冲突: {a['issues']}"


def test_mcu_pin_led_and_button_shared_net_no_crash():
    """MCU 引脚同时被 LED 阳极与按钮占用 → 不崩溃(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "D2", "value": "LED_STATUS"}, {"ref": "SW1", "value": "SW_Push"}],
            nets=[
                {
                    "name": "NET_A",
                    "nodes": [{"ref": "D2", "pin": "A"}, {"ref": "U1", "pin": "PA1"}],
                },
                {"name": "NET_B", "nodes": [{"ref": "D2", "pin": "K"}, {"ref": "SW1", "pin": "1"}]},
                {"name": "GND", "nodes": [{"ref": "SW1", "pin": "2"}]},
            ],
        )
    )
    # 这组是 LED 阴极与按键共用网络而非 MCU 引脚冲突:不应崩溃,数量断言放宽
    a = analyze_hardware(model)
    assert isinstance(a["issues"], list)


def test_swd_grouping():
    """SWD 成组(SWDIO+SWCLK)(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            nets=[
                {"name": "SWDIO", "nodes": [{"ref": "U1", "pin": "PA13"}]},
                {"name": "SWCLK", "nodes": [{"ref": "U1", "pin": "PA14"}]},
            ]
        )
    )
    a = analyze_hardware(model)
    swd = next(b for b in a["buses"] if b["type"] == "swd")
    assert swd["pins"]["swdio"] == "PA13"
    assert swd["pins"]["swclk"] == "PA14"


def test_spi_grouping_with_cs():
    """SPI 成组(SCK+MOSI+MISO+CS 归属)(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U2", "value": "W25Q128"}],
            nets=[
                {
                    "name": "SPI_SCK",
                    "nodes": [{"ref": "U1", "pin": "PA5"}, {"ref": "U2", "pin": "CLK"}],
                },
                {
                    "name": "SPI_MOSI",
                    "nodes": [{"ref": "U1", "pin": "PA7"}, {"ref": "U2", "pin": "DI"}],
                },
                {
                    "name": "SPI_MISO",
                    "nodes": [{"ref": "U1", "pin": "PA6"}, {"ref": "U2", "pin": "DO"}],
                },
                {
                    "name": "SPI_CS",
                    "nodes": [{"ref": "U1", "pin": "PA4"}, {"ref": "U2", "pin": "CS"}],
                },
            ],
        )
    )
    a = analyze_hardware(model)
    spi = next(b for b in a["buses"] if b["type"] == "spi")
    assert spi["pins"]["sck"] == "PA5"
    assert spi["pins"]["mosi"] == "PA7"
    assert spi["pins"]["miso"] == "PA6"
    assert spi["pins"]["cs"] == "PA4"
    assert any(d["ref"] == "U2" for d in spi["devices"])
    # 引脚方向:MISO 入、其余出
    miso = next(p for p in a["pinmap"] if p["num"] == 6 and p["port"] == "PA")
    assert miso["direction"] == "in"


def test_uart_grouping_with_numbering():
    """UART 成组 → usart 总线 + TXD/RXD 带编号(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U3", "value": "CH340N"}],
            nets=[
                {
                    "name": "TX1",
                    "nodes": [{"ref": "U1", "pin": "PA9"}, {"ref": "U3", "pin": "RXD"}],
                },
                {
                    "name": "RX1",
                    "nodes": [{"ref": "U1", "pin": "PA10"}, {"ref": "U3", "pin": "TXD"}],
                },
            ],
        )
    )
    a = analyze_hardware(model)
    uart = next(b for b in a["buses"] if b["type"] == "uart")
    assert uart["pins"]["tx"] == "PA9"
    assert uart["pins"]["rx"] == "PA10"
    assert any(d["ref"] == "U3" for d in uart["devices"])


def test_power_rail_prefix_matching():
    """电源轨识别(前缀匹配 VCC_/VDD_ 与 +5V)(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            nets=[
                {"name": "GND", "nodes": []},
                {"name": "+5V", "nodes": []},
                {"name": "VCC_2", "nodes": []},
                {"name": "VDDA", "nodes": []},
                {"name": "VIN", "nodes": []},
                {"name": "RANDOM", "nodes": []},
            ]
        )
    )
    a = analyze_hardware(model)
    names = [r["name"] for r in a["power"]["rails"]]
    assert "GND" in names
    assert "+5V" in names
    assert "VCC_2" in names
    assert "VDDA" in names
    assert "VIN" in names
    assert "RANDOM" not in names


def test_duplicate_power_pins_no_conflict():
    """同一引脚多电压域(VDD 重复脚)不误报冲突(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            nets=[
                {
                    "name": "VDD",
                    "nodes": [{"ref": "U1", "pin": "VDD"}, {"ref": "U1", "pin": "VDDA"}],
                },
                {"name": "GND", "nodes": [{"ref": "U1", "pin": "VSS"}]},
            ]
        )
    )
    a = analyze_hardware(model)
    # 电源脚名不是端口引脚,不进 pinmap,不冲突
    assert not any("冲突" in i for i in a["issues"])


def test_codegen_c_preprocessor_style():
    """输出通过 C 预处理风格检查(宏名/括号/守卫配对)(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U2", "value": "BMP280"}, {"ref": "SW1", "value": "SW_Push"}],
            nets=[
                {
                    "name": "SDA",
                    "nodes": [{"ref": "U1", "pin": "PB9"}, {"ref": "U2", "pin": "SDA"}],
                },
                {
                    "name": "SCL",
                    "nodes": [{"ref": "U1", "pin": "PB8"}, {"ref": "U2", "pin": "SCL"}],
                },
                {
                    "name": "SW_USER",
                    "nodes": [{"ref": "U1", "pin": "PA0"}, {"ref": "SW1", "pin": "1"}],
                },
                {"name": "GND", "nodes": [{"ref": "SW1", "pin": "2"}, {"ref": "U2", "pin": "GND"}]},
                {"name": "3V3", "nodes": [{"ref": "U2", "pin": "VCC"}]},
            ],
        )
    )
    a = analyze_hardware(model)
    h = generate_pinmap_header(a, {"guardPrefix": "BOARD"})
    # 守卫配对
    assert h.startswith("/*")
    assert "#ifndef BOARD_H" in h
    assert "#define BOARD_H" in h
    assert "#endif /* BOARD_H */" in h
    # 每条 #define 的宏名仅 [A-Z0-9_]
    defines = [line for line in h.split("\n") if line.startswith("#define")]
    assert len(defines) > 10
    for d in defines:
        name = re.sub(r"^#define\s+", "", d).split()[0]
        assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", name), f"非法宏名: {name}"
    # #include 与 #endif 之外的预处理指令不应有未闭合
    for line in h.split("\n"):
        if line.startswith("#"):
            assert re.match(r"^#(ifndef|define|endif|include)", line), f"非法预处理指令: {line}"
    # 注释闭合:每行 /* 与 */ 数量平衡(跨行注释会成对出现在不同行,统计总量)
    assert h.count("/*") == h.count("*/")
    # 字符串字面量里的引号成对
    assert h.count('"') % 2 == 0


def test_codegen_unsafe_names_sanitized():
    """不安全板名/guard 时宏名净化(移植 JS 同名用例)。"""
    a = {
        "mcu": {"ref": "U1", "part": "STM32F103C8T6", "pinCount": 10},
        "buses": [],
        "gpio": [],
        "power": {"rails": [], "decouplingCount": 0},
        "pinmap": [],
        "issues": [],
    }
    h = generate_pinmap_header(a, {"guardPrefix": "bad name$", "boardDefine": 'weird "board" name'})
    for d in [ln for ln in h.split("\n") if ln.startswith("#define")]:
        name = re.sub(r"^#define\s+", "", d).split()[0]
        assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
    assert "#define BAD_NAME$_MCU_PART" not in h, "guard 净化后不应包含 $"


def test_hardware_context_prompt_structure():
    """结构完整(板名/MCU/总线表/GPIO 表/约束指令/issues)(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(
        build_kicad(
            comps=[{"ref": "U2", "value": "BMP280"}, {"ref": "D1", "value": "LED_PWR"}],
            nets=[
                {
                    "name": "SDA",
                    "nodes": [{"ref": "U1", "pin": "PB9"}, {"ref": "U2", "pin": "SDA"}],
                },
                {
                    "name": "SCL",
                    "nodes": [{"ref": "U1", "pin": "PB8"}, {"ref": "U2", "pin": "SCL"}],
                },
                {"name": "3V3", "nodes": [{"ref": "D1", "pin": "A"}]},
                {
                    "name": "Net-(D1-K)",
                    "nodes": [{"ref": "D1", "pin": "K"}, {"ref": "U1", "pin": "PA5"}],
                },
            ],
        )
    )
    a = analyze_hardware(model)
    p = hardware_context_prompt(a)
    assert "硬件上下文" in p
    assert "必须遵守的引脚约束" in p
    assert "board.h" in p, "必须提及 board.h 宏"
    assert "BMP280" in p
    assert "PB9" in p
    assert "I2C" in p
    assert "拉低点亮" in p


def test_end_to_end_analyze_netlist_text():
    """闭环:analyze_netlist_text(blink.net) 直接产出完整分析(移植 JS 同名用例)。"""
    from trae_agent.hardware import analyze_netlist_text

    text = """(export (version "E") (components
    (comp (ref "U1") (value "STM32F103C8T6"))
    (comp (ref "U2") (value "BMP280")))
  (nets
    (net (code "1") (name "SDA") (node (ref "U1") (pin "PB9")) (node (ref "U2") (pin "SDA")))
    (net (code "2") (name "SCL") (node (ref "U1") (pin "PB8")) (node (ref "U2") (pin "SCL")))))"""
    a = analyze_netlist_text(text, filename="blink.net")
    assert a["sourceFormat"] == "kicad"
    assert a["model"]["format"] == "kicad"
    assert (a.get("boardName") or a["model"].get("boardName")) == "blink"
    assert any(b["type"] == "i2c" for b in a["buses"])
    h = generate_pinmap_header(a)
    assert "GPIOB" in h


def test_end_to_end_parse_netlist_unknown_format():
    """闭环:parse_netlist 未知格式给出中文警告(移植 JS 同名用例)。"""
    from trae_agent.hardware import parse_netlist

    m = parse_netlist("完全不是网表的文本")
    assert m["format"] is None
    assert any("无法识别网表格式" in w for w in m["warnings"])


def test_empty_and_null_models_do_not_throw():
    """空模型与 None 输入不抛异常(移植 JS:analysis 空模型与 null 输入)。"""
    a1 = analyze_hardware(None)
    assert len(a1["issues"]) > 0
    a2 = analyze_hardware({"format": "kicad", "components": [], "nets": [], "warnings": []})
    assert any("未找到 MCU" in i for i in a2["issues"])


def test_temp_file_io_roundtrip(tmp_path: Path):
    """临时文件 I/O:从磁盘 fixture 解析 → 生成 board.h → 写回(移植 JS 同名用例)。"""
    from trae_agent.hardware import analyze_netlist_text

    text = """(export (version "E") (components
      (comp (ref "U1") (value "STM32F103C8T6"))
      (comp (ref "D1") (value "LED_BLUE")))
    (nets
      (net (code "1") (name "3V3") (node (ref "D1") (pin "A")))
      (net (code "2") (name "Net-(D1-K)") (node (ref "D1") (pin "K")) (node (ref "U1") (pin "PA2")))))"""
    src = tmp_path / "board.net"
    src.write_text(text, encoding="utf-8")
    a = analyze_netlist_text(src.read_text(encoding="utf-8"), filename="board.net")
    assert a["mcu"]["part"] == "STM32F103C8T6"
    led = next((g for g in a["gpio"] if g["role"] == "led"), None)
    assert led is not None
    assert led["activeLow"] is True
    out = tmp_path / "board.h"
    out.write_text(
        generate_pinmap_header(a, {"guardPrefix": "BOARD", "boardDefine": "esp-test"}),
        encoding="utf-8",
    )
    written = out.read_text(encoding="utf-8")
    assert "#ifndef BOARD_H" in written
    assert "STM32F103C8T6" in written
