# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""test_formats.py — CSV 嗅探/分号/BOM/引号 与 board.yaml 全特性 与 detectFormat 全 4 种。

移植自 embedded-ai-studio/test/unit/hardware-formats.test.js(21 用例)。
"""

import pytest

from trae_agent.hardware.board_yaml import parse_board_yaml
from trae_agent.hardware.csv_pins import parse_csv_line, parse_csv_pins, sniff_separator
from trae_agent.hardware.model import detect_format
from trae_agent.hardware.peripherals import analyze_hardware

# ---------------- CSV ----------------


def test_csv_comma_basic():
    """逗号分隔基本解析(ref,pin,net,name)(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "ref,pin,net,name,value,pin_type",
            "U1,PA5,LED_STATUS,status-led,,GPIO_Output",
            "U1,PB9,I2C1_SDA,,BMP280,AF",
            "D1,K,Net-(D1-K),pwr-led,,",
        ]
    )
    model = parse_csv_pins(text)
    assert model["format"] == "csv"
    assert len(model["components"]) == 2
    u1 = next(c for c in model["components"] if c["ref"] == "U1")
    assert len(u1["pins"]) == 2
    assert u1["value"] == "status-led"  # 首个非空 name/value 生效(name 兼作 value 别名)
    led_net = next(n for n in model["nets"] if n["name"] == "LED_STATUS")
    assert led_net["connections"] == [{"ref": "U1", "pin": "PA5"}]


def test_csv_semicolon_sniffing():
    """分号分隔自动嗅探(移植 JS 同名用例)。"""
    text = "\n".join(["ref;pin;net", "U1;PA0;SW_USER", "U1;PA1;LED_GREEN"])
    model = parse_csv_pins(text)
    assert len(model["components"]) == 1
    assert len(model["nets"]) == 2
    assert sniff_separator(text) == ";"


def test_csv_tab_sniffing():
    """TAB 分隔自动嗅探(移植 JS 同名用例)。"""
    text = "ref\tpin\tnet\nU1\tPB0\tLED_STATUS"
    model = parse_csv_pins(text)
    assert len(model["components"]) == 1
    u1 = model["components"][0]
    assert u1["pins"][0]["pin"] == "PB0"
    assert u1["pins"][0]["net"] == "LED_STATUS"


def test_csv_bom():
    """BOM 处理(移植 JS 同名用例)。"""
    text = "\ufeffref,pin,net\nU1,PA5,LED"
    model = parse_csv_pins(text)
    assert len(model["components"]) == 1


def test_csv_header_case_insensitive():
    """表头大小写/空格不敏感(移植 JS 同名用例)。"""
    text = "Ref, Pin ,NET\nU1,PA5,LEDX"
    model = parse_csv_pins(text)
    assert len(model["components"]) == 1
    assert model["nets"][0]["name"] == "LEDX"


def test_csv_header_subset():
    """表头任意子集(只有 ref,pin,net)(移植 JS 同名用例)。"""
    text = "ref,pin,net\nU1,PA5,LED"
    model = parse_csv_pins(text)
    assert len([w for w in model["warnings"] if "缺少" in w]) == 0


def test_csv_quoted_fields():
    """引号包裹字段(含逗号/双引号转义)(移植 JS 同名用例)。"""
    assert parse_csv_line('a,"b,c",d', ",") == ["a", "b,c", "d"]
    assert parse_csv_line('"say ""hi""",x', ",") == ['say "hi"', "x"]
    text = 'ref,pin,net,value\nU1,PB3,"NET, 1","10k, 1%"'
    model = parse_csv_pins(text)
    assert model["nets"][0]["name"] == "NET, 1"


def test_csv_malformed_inputs():
    """畸形输入不崩溃(空文本/无表头/缺列)(移植 JS 同名用例)。"""
    assert len(parse_csv_pins("")["components"]) == 0
    no_header = parse_csv_pins("U1,PA5,LED")
    assert len(no_header["components"]) == 0
    assert any("未找到有效表头" in w for w in no_header["warnings"])
    missing_pin = parse_csv_pins("ref,net\nU1,LED")
    assert len(missing_pin["warnings"]) > 0


# ---------------- board.yaml(真实样例) ----------------


@pytest.fixture(scope="module")
def board_model(board_yaml_text):
    return parse_board_yaml(board_yaml_text)


def test_board_yaml_basic_structure(board_model):
    """基础结构(board/mcu/components/nets)(移植 JS 同名用例)。"""
    assert board_model["format"] == "board-yaml"
    assert board_model["boardName"] == "blink-board"
    assert board_model["mcu"]["ref"] == "U1"
    assert board_model["mcu"]["part"] == "STM32F103C8T6"
    assert board_model["mcu"]["package"] == "LQFP48"
    # 显式声明 10 个(D1,D2,SW1,U2,U3,Y1,C1-C4)+ 由 nets 引用自动创建的 U1
    assert len(board_model["components"]) == 11
    # 显式 nets:4 条;其余(3V3/GND/PA5/PB0/PA0/5V)由 components pins 反推生成
    for name in ("I2C1_SDA", "I2C1_SCL", "OSC_IN", "OSC_OUT"):
        assert any(n["name"] == name for n in board_model["nets"]), f"应有 nets:{name}"
    assert len(board_model["nets"]) >= 10, "反推网络也应存在"


def test_board_yaml_component_pins_map(board_model):
    """components 的 pins 映射(pinName→netName)(移植 JS 同名用例)。"""
    d1 = next(c for c in board_model["components"] if c["ref"] == "D1")
    assert d1["type"] == "led"
    assert d1["value"] == "power-green"
    anode = next(p for p in d1["pins"] if p["pin"] == "anode")
    assert anode["net"] == "3V3"
    cath = next(p for p in d1["pins"] if p["pin"] == "cathode")
    assert cath["net"] == "PA5"


def test_board_yaml_nets_ref_pin_and_comment(board_model):
    """nets 的 REF.PIN 引用与 comment(移植 JS 同名用例)。"""
    sda = next(n for n in board_model["nets"] if n["name"] == "I2C1_SDA")
    assert sda["comment"] == "传感器 I2C 总线数据线"
    assert sda["connections"] == [
        {"ref": "U1", "pin": "PB9"},
        {"ref": "U2", "pin": "SDA"},
    ]


def test_board_yaml_pins_backfill_nets(board_model):
    """components 的 pins 反向并入 net 连接表(未显式给 nets 的网络)(移植 JS 同名用例)。"""
    # D1 的 anode=3V3:3V3 不在 nets: 段 → 由 pins 反推生成
    v33 = next(n for n in board_model["nets"] if n["name"] == "3V3")
    refs = sorted(f"{c['ref']}.{c['pin']}" for c in v33["connections"])
    assert "C1.1" in refs
    assert "D1.anode" in refs
    assert "U2.VCC" in refs
    assert "U3.VOUT" in refs


def test_board_yaml_mcu_pins_fields():
    """mcu.pins 固定字段(ref.part + package)(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "board: x",
            "mcu:",
            "  ref: U1",
            "  part: STM32F103C8T6",
            "  package: LQFP48",
            "  pins:",
            "    PA0: SW_USER",
            "    PA5: LED1",
            "components:",
            "  - ref: SW1",
            "    type: button",
            "    pins:",
            "      a: SW_USER",
            "      b: GND",
            "nets: []",
        ]
    )
    model = parse_board_yaml(text)
    assert model["mcu"]["package"] == "LQFP48"
    u1 = next((c for c in model["components"] if c["ref"] == "U1"), None)
    assert u1 is not None, "mcu.pins 声明应生成 U1 组件"
    assert any(p["pin"] == "PA0" and p["net"] == "SW_USER" for p in u1["pins"])


def test_board_yaml_quoted_values():
    """值带引号去引号(# 注释不影响)(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "# 头部注释",
            'board: "quoted-board"  # 行尾注释',
            "mcu:",
            "  ref: 'U9'",
            "  part: ESP32-S3",
        ]
    )
    model = parse_board_yaml(text)
    assert model["boardName"] == "quoted-board"
    assert model["mcu"]["ref"] == "U9"
    assert model["mcu"]["part"] == "ESP32-S3"


def test_board_yaml_missing_mcu_fallback():
    """缺 mcu 段 → 警告 + 分析走兜底(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "board: x",
            "components:",
            "  - ref: U1",
            "    value: STM32F103C8T6",
            "    pins:",
            "      PA5: LED",
        ]
    )
    model = parse_board_yaml(text)
    assert any("缺少 mcu 段" in w for w in model["warnings"])
    analysis = analyze_hardware(model)
    assert analysis["mcu"]["ref"] == "U1"  # value 匹配兜底


def test_board_yaml_malformed_inputs():
    """畸形输入不崩溃(空/纯注释/坏缩进)(移植 JS 同名用例)。"""
    assert len(parse_board_yaml("")["components"]) == 0
    only_comments = parse_board_yaml("# just a comment\n# another")
    assert len(only_comments["components"]) == 0
    assert len(only_comments["warnings"]) > 0
    bad = parse_board_yaml("board: x\n  weird indent\n    deeper")
    assert len(bad["warnings"]) >= 0  # 不抛异常即通过


def test_board_yaml_missing_ref_component():
    """缺 ref 的 components 项跳过并警告(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "board: x",
            "mcu:",
            "  ref: U1",
            "  part: STM32F103C8T6",
            "components:",
            "  - type: led",
            "    value: no-ref-led",
            "  - ref: D1",
            "    type: led",
            "    pins:",
            "      a: PA5",
        ]
    )
    model = parse_board_yaml(text)
    assert any("缺少 ref" in w for w in model["warnings"])
    assert any(c["ref"] == "D1" for c in model["components"])


# ---------------- detectFormat ----------------


def test_detect_format_filename_priority():
    """文件名优先(全部后缀)(移植 JS 同名用例)。"""
    ANY = "whatever"
    assert detect_format(ANY, "a.net") == "kicad"
    assert detect_format(ANY, "a.kicad_net") == "kicad"
    assert detect_format(ANY, "a.cir") == "spice"
    assert detect_format(ANY, "a.spc") == "spice"
    assert detect_format(ANY, "a.sp") == "spice"
    assert detect_format(ANY, "a.spi") == "spice"
    assert detect_format(ANY, "a.csv") == "csv"
    assert detect_format(ANY, "a.tsv") == "csv"
    assert detect_format(ANY, "board.yaml") == "board-yaml"
    assert detect_format(ANY, "a.byml") == "board-yaml"
    assert detect_format(ANY, "x/board.yaml") == "board-yaml"


def test_detect_format_content_kicad():
    """内容嗅探 kicad(移植 JS 同名用例)。"""
    assert detect_format('(export (version "E")\n(nets))') == "kicad"
    assert detect_format("(sym ...") == "kicad"


def test_detect_format_content_board_yaml():
    """内容嗅探 board-yaml(含前导注释)(移植 JS 同名用例)。"""
    assert detect_format("board: blink\nmcu:\n  ref: U1") == "board-yaml"
    assert detect_format("# 注释\nmcu:\n  ref: U1") == "board-yaml"


def test_detect_format_content_csv():
    """内容嗅探 csv(表头特征)(移植 JS 同名用例)。"""
    assert detect_format("ref,pin,net\nU1,PA5,LED") == "csv"
    assert detect_format("ref;pin;net\nU1;PA5;LED") == "csv"
    assert detect_format("ref\tpin\tnet\nU1\tPA5\tLED") == "csv"


def test_detect_format_no_match():
    """不匹配任何格式 → None(移植 JS 同名用例)。"""
    assert detect_format("") is None
    assert detect_format("hello world") is None
    assert detect_format("只是一段中文文本,没有网表特征") is None


def test_detect_format_spice_over_csv():
    """spice 内容嗅探优先级于 csv(明确 .subckt)(移植 JS 同名用例)。"""
    assert detect_format(".subckt A b c\nR1 a b 1k\n.ends") == "spice"


# ---------------- board.yaml 全流程分析 ----------------


def test_analyze_board_yaml_full(board_model):
    """board.yaml 分析(LED 双极性/按键/I2C/晶振/退耦)(移植 JS 同名用例)。"""
    analysis = analyze_hardware(board_model)
    assert analysis["mcu"]["part"] == "STM32F103C8T6"
    # I2C 总线
    i2c = next(b for b in analysis["buses"] if b["type"] == "i2c")
    assert i2c["pins"]["sda"] == "PB9"
    assert i2c["pins"]["scl"] == "PB8"
    assert any(d["ref"] == "U2" for d in i2c["devices"])
    # LED:D1 anode=3V3 cathode=PA5 → activeLow;D2 anode=PB0 cathode=GND → !activeLow
    led1 = next(g for g in analysis["gpio"] if g["role"] == "led" and g["deviceRef"] == "D1")
    assert led1["activeLow"] is True
    led2 = next(g for g in analysis["gpio"] if g["role"] == "led" and g["deviceRef"] == "D2")
    assert led2["activeLow"] is False
    # 按键
    btn = next(g for g in analysis["gpio"] if g["role"] == "button")
    assert btn["pin"] == "PA0"
    assert btn["activeLow"] is True
    # 晶振
    assert any(p["net"] == "OSC_IN" and p["role"] == "crystal" for p in analysis["pinmap"])
    # 电源
    assert analysis["power"]["decouplingCount"] == 4
    rails = [r["name"] for r in analysis["power"]["rails"]]
    assert "3V3" in rails and "GND" in rails
