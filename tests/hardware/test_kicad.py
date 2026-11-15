# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""test_kicad.py — KiCad 网表解析 + tokenizer + blink 板完整分析断言。

移植自 embedded-ai-studio/test/unit/hardware-kicad.test.js(29 用例)。
"""

import re

from tests.hardware.conftest import build_kicad
from trae_agent.hardware.codegen import generate_pinmap_header, hardware_context_prompt
from trae_agent.hardware.kicad_netlist import parse_kicad_netlist
from trae_agent.hardware.model import detect_format
from trae_agent.hardware.peripherals import analyze_hardware
from trae_agent.hardware.sexp import parse_sexp, tokenize_sexp

KICAD_SAMPLE = """(export (version "E")
  (design (source "blink.kicad_sch") (date "2026-01-01") (tool "Eeschema 7.0"))
  (components
    (comp (ref "U1") (value "STM32F103C8T6")
      (footprint "Package_QFP:LQFP-48_7x7mm_P0.5mm")
      (property (name "Sheetname") (value "root"))
      (libsource (lib "MCU_STM32") (part "STM32F103C8Tx"))
      (sheetpath (names "/") (tstamps "/")) (tstamps "uuid-1"))
    (comp (ref "D1") (value "LED_GREEN") (footprint "LED_SMD:LED_0603")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "C1") (pin "2") (pinfunction "Passive") (tstamp "u10")))
    (net (code "3") (name "Net-(D1-Pad2)")
      (node (ref "D1") (pin "2"))
      (node (ref "U1") (pin "PA5")))))"""


# ---------------- tokenizer / S 表达式 ----------------


def test_tokenizer_paren_types_str_atoms_whitespace():
    """括号类型、字符串 atom 与空白忽略(移植 JS: tokenizer:supports paren types...)。"""
    toks = tokenize_sexp('(comp (ref "U 1") (pin PA5))')
    kinds = ",".join(t.t for t in toks)
    assert kinds == "(,atom,(,atom,str,),(,atom,atom,),)"
    # 含空格/括号的字符串不会被拆分
    str_tok = next(t for t in toks if t.t == "str")
    assert str_tok.v == "U 1"


def test_tokenizer_backslash_escaping():
    """反斜杠转义引号与反斜杠(移植 JS: tokenizer:supports backslash escaping)。"""
    toks = tokenize_sexp(r'(value "a\"b\\c")')
    s = next(t for t in toks if t.t == "str")
    assert s.v == 'a"b\\c'


def test_tokenizer_multiline_safe():
    """多行安全:字符串字面量可跨行保留换行(移植 JS: tokenizer:multiline safe)。"""
    toks = tokenize_sexp('(value "line1\nline2"\n  (ref "R1"))')
    strs = [t.v for t in toks if t.t == "str"]
    assert strs == ["line1\nline2", "R1"]


def test_parse_sexp_truncated_best_effort():
    """截断的表达式:尽力解析 + 警告(移植 JS: parseSexp:truncated expression)。"""
    root, warnings = parse_sexp('(export (components (comp (ref "U1"')
    assert isinstance(root, list), "root should still get a best-effort node"
    assert len(warnings) > 0
    assert "未完全闭合" in warnings[0]


def test_parse_sexp_extra_close_parens():
    """多余右括号不崩溃只告警(移植 JS: parseSexp:extra closing parentheses)。"""
    root, warnings = parse_sexp("(a (b)) )")
    assert root is not None
    assert len(warnings) > 0


def test_parse_sexp_empty_text_returns_none():
    """空文本 → root None(移植 JS: parseSexp:empty text returns null)。"""
    root, _warnings = parse_sexp("   \n ")
    assert root is None


# ---------------- KiCad 解析 ----------------


def test_parse_kicad_basic_structure():
    """基础结构 ref/value/footprint/net/node + 自动创建未声明器件(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(KICAD_SAMPLE)
    assert model["format"] == "kicad"
    # U1 与 D1 显式声明;C1 只在 nets 中被引用 → 自动创建 + 警告
    assert len(model["components"]) == 3
    u1 = next(c for c in model["components"] if c["ref"] == "U1")
    assert u1["value"] == "STM32F103C8T6"
    assert u1["footprint"] == "Package_QFP:LQFP-48_7x7mm_P0.5mm"
    assert any("自动创建" in w for w in model["warnings"])
    assert len(model["nets"]) == 2
    gnd = model["nets"][0]
    assert gnd["name"] == "GND"
    assert len(gnd["connections"]) == 1
    assert gnd["connections"][0] == {"ref": "C1", "pin": "2"}
    # 网名可含 ()- 等特殊字符
    assert model["nets"][1]["name"] == "Net-(D1-Pad2)"
    # components[].pins 从 nets 反填
    assert any(p["pin"] == "PA5" for p in u1["pins"])


def test_parse_kicad_fields_out_of_order():
    """字段乱序/缺失均不影响解析(移植 JS:fields out of order)。"""
    text = """(export (version "E")
    (components
      (comp (footprint "X:Y") (value "LED") (ref "D9"))
      (comp (ref "R1"))
      (comp (value "only-value-no-ref")))
    (nets))"""
    model = parse_kicad_netlist(text)
    assert len(model["components"]) == 2  # 无 ref 的那条被跳过
    d9 = next(c for c in model["components"] if c["ref"] == "D9")
    assert d9["value"] == "LED"
    assert any("缺少 ref" in w for w in model["warnings"])


def test_parse_kicad_chinese_net_names():
    """中文网络名原样保留(移植 JS:Chinese net names are preserved)。"""
    text = '(export (nets (net (code "1") (name "电源-地")\n    (node (ref "U1") (pin "PA1")))))'
    model = parse_kicad_netlist(text)
    assert model["nets"][0]["name"] == "电源-地"


def test_parse_kicad_unterminated_string():
    """未闭合字符串字面量:尽力解析 + 警告(移植 JS:mnetlist containing unterminated)。"""
    text = '(export (components (comp (ref "U1)'  # 引号未闭合
    model = parse_kicad_netlist(text)
    assert len(model["warnings"]) > 0


def test_parse_kicad_empty_and_garbled():
    """空文本/纯乱码不崩溃(移植 JS:empty text/only garbled content)。"""
    empty = parse_kicad_netlist("")
    assert len(empty["components"]) == 0
    assert len(empty["warnings"]) > 0
    junk = parse_kicad_netlist("not a netlist at all")
    assert len(junk["components"]) == 0
    assert len(junk["warnings"]) > 0


# ---------------- 真实样例分析(blink.net) ----------------


def _analyze_blink(blink_net_text: str):
    model = parse_kicad_netlist(blink_net_text)
    return model, analyze_hardware(model)


def test_analyze_blink_mcu(blink_net_text):
    """MCU 识别:U1 / STM32F103C8T6 / 9 引脚(移植 JS 同名用例)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    assert analysis["mcu"]["ref"] == "U1"
    assert analysis["mcu"]["part"] == "STM32F103C8T6"
    # 网表中 U1 实际出现的引脚:VSS,VDD,PB0,PA5,PA0,PB9,PB8,PD0,PD1
    assert analysis["mcu"]["pinCount"] == 9


def test_analyze_blink_i2c_bus(blink_net_text):
    """总线成组识别 + 上拉电阻不算设备(移植 JS:buses grouped detection)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    i2c = next((b for b in analysis["buses"] if b["type"] == "i2c"), None)
    assert i2c is not None, "I2C should pair up"
    assert i2c["pins"]["sda"] == "PB9"
    assert i2c["pins"]["scl"] == "PB8"
    bmp = next((d for d in (i2c.get("devices") or []) if d["ref"] == "U2"), None)
    assert bmp is not None, "U2 BMP280 should be on the bus"
    assert bmp["part"] == "BMP280"
    assert not any(d["ref"] in ("R2", "R3") for d in i2c["devices"]), "上拉电阻不算设备"
    assert i2c["note"]


def test_analyze_blink_gpio_is_array(blink_net_text):
    """gpio 结构健全;blink 无 SWD 网络,非总线信号落在 gpio(移植 JS 同名用例)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    assert isinstance(analysis["gpio"], list)


def test_analyze_blink_led_polarity(blink_net_text):
    """LED 极性:D1 低有效(阳极 3V3),D2 高有效(阴极 GND)(移植 JS 同名用例)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    leds = [g for g in analysis["gpio"] if g["role"] == "led"]
    assert len(leds) == 2, f"should have 2 LED gpios: {leds}"
    d1 = next(g for g in leds if g["deviceRef"] == "D1")  # 阳极 3V3 阴极 PA5 → 低有效
    assert d1["pin"] == "PA5"
    assert d1["activeLow"] is True
    assert d1["direction"] == "out"
    d2 = next(g for g in leds if g["deviceRef"] == "D2")  # 阳极 PB0 阴极 GND → 高有效
    assert d2["pin"] == "PB0"
    assert d2["activeLow"] is False


def test_analyze_blink_button(blink_net_text):
    """按键:PA0 输入低有效且与 LED 无冲突(移植 JS 同名用例)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    btn = next(g for g in analysis["gpio"] if g["role"] == "button")
    assert btn["pin"] == "PA0"
    assert btn["direction"] == "in"
    assert btn["activeLow"] is True
    assert not any("冲突" in str(i) for i in analysis["issues"])


def test_analyze_blink_crystal(blink_net_text):
    """晶振引脚进 pinmap(OSC_IN/OSC_OUT 网络)(移植 JS:crystal detected)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    osc = [p for p in analysis["pinmap"] if p["net"] in ("OSC_IN", "OSC_OUT")]
    assert len(osc) == 2, f"OSC pins should be in the pinmap: {analysis['pinmap']}"


def test_analyze_blink_power(blink_net_text):
    """电源轨 + 退耦电容计数(移植 JS:power rails + decoupling)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    names = [r["name"] for r in analysis["power"]["rails"]]
    assert "GND" in names
    assert "3V3" in names
    assert "5V" in names
    assert analysis["power"]["decouplingCount"] == 4
    gnd = next(r for r in analysis["power"]["rails"] if r["name"] == "GND")
    assert gnd["connections"] == 9


def test_analyze_blink_pinmap_structure(blink_net_text):
    """pinmap 结构断言:宏名合法、端口 P[A-K]、num 整数、af 非空(移植 JS 同名用例)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    for p in analysis["pinmap"]:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", p["signal"]), (
            f"signal 名须为合法宏前缀: {p['signal']}"
        )
        assert re.fullmatch(r"P[A-K]", p["port"])
        assert isinstance(p["num"], int) and p["num"] >= 0
        assert isinstance(p["af"], str) and len(p["af"]) > 0
    sda = next(p for p in analysis["pinmap"] if p["signal"] in ("I2C1_SDA", "I2C_SDA"))
    assert sda["port"] == "PB"
    assert sda["num"] == 9
    assert sda["af"] == "I2C1"
    led_status = next(p for p in analysis["pinmap"] if p["signal"] == "LED_STATUS")
    assert led_status["af"] == "GPIO"


def test_analyze_blink_summary_markdown(blink_net_text):
    """summaryMarkdown 囊括全部字段与连接表(移植 JS:summaryMarkdown contains every field)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    md = analysis["summaryMarkdown"]
    assert "硬件分析结果" in md
    assert "总线" in md
    assert "GPIO" in md
    assert "电源" in md
    assert "网络连接表" in md
    assert "问题与提示" in md
    assert "BMP280" in md
    assert "PB9" in md
    # 连接表应含 U1.PB9 与 U2.SDA
    assert "U1.PB9" in md
    assert "U2.SDA" in md


def test_analyze_no_mcu_issues():
    """无 MCU → issues 带中文说明(移植 JS:no MCU → issues carry Chinese explanation)。"""
    model = parse_kicad_netlist(KICAD_SAMPLE)
    model["components"] = [c for c in model["components"] if c["ref"] != "U1"]
    analysis = analyze_hardware(model)
    assert any("未找到 MCU" in str(i) for i in analysis["issues"])


# ---------------- codegen ----------------


def test_codegen_blink_macros(blink_net_text):
    """board.h 宏名合法 + activeLow 注解(移植 JS:macro names legality)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    h = generate_pinmap_header(analysis, {"guardPrefix": "BOARD", "boardDefine": "blink-board"})
    assert "#ifndef BOARD_H" in h
    assert "#define BOARD_H" in h
    assert '#define BOARD_MCU_PART "STM32F103C8T6"' in h
    assert '#define BOARD_BOARD_NAME "blink-board"' in h
    assert "/* Generated by Embedded AI Studio */" in h
    # 所有宏名合法
    for m in re.findall(r"#define\s+\S+", h):
        name = m.replace("#define", "").strip().split()[0]
        assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", name), f"illegal macro name: {name}"
    # LED 低有效注解
    assert re.search(r"BOARD_LED_STATUS_ACTIVE_LOW\s+0", h)
    assert re.search(r"BOARD_.*D1.*_ACTIVE_LOW\s+1", h)
    assert re.search(r"BOARD_I2C1_SDA_PORT\s+GPIOB", h)
    assert re.search(r"BOARD_I2C1_SDA_PIN\s+9", h)
    assert re.search(r"BOARD_I2C1_SDA_AF\s+I2C1", h)


def test_codegen_custom_guard_prefix(blink_net_text):
    """自定义 guardPrefix(移植 JS:custom guardPrefix)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    h = generate_pinmap_header(analysis, {"guardPrefix": "MYBOARD"})
    assert "#ifndef MYBOARD_H" in h
    assert "#define MYBOARD_LED_STATUS_PORT" in h


def test_codegen_unsafe_guard_prefix(blink_net_text):
    """不安全 guardPrefix 被净化为合法宏(移植 JS:unsafe guardPrefix is sanitized)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    h = generate_pinmap_header(analysis, {"guardPrefix": "my board 9"})
    assert "#ifndef MY_BOARD_9_H" in h


def test_hardware_context_prompt(blink_net_text):
    """hardwareContextPrompt 含引脚约束与速查表(移植 JS:contains pin constraints)。"""
    _model, analysis = _analyze_blink(blink_net_text)
    p = hardware_context_prompt(analysis)
    assert "必须遵守的引脚约束" in p
    assert "board.h" in p
    assert "不得直接写 PA5" in p
    assert "引脚宏速查" in p
    assert "BOARD_" in p


def test_detect_format_kicad():
    """kicad 内容嗅探与文件名识别(移植 JS:detectFormat:kicad content sniffing)。"""
    assert detect_format(KICAD_SAMPLE) == "kicad"
    assert detect_format(KICAD_SAMPLE, "blink.net") == "kicad"
    assert detect_format('(version "1")', "x.kicad_net") == "kicad"


def test_contract_sample_structure():
    """任务正文显式给的样例结构(sheetpath/libsource/tstamps)字段兼容(移植 JS 同名用例)。"""
    model = parse_kicad_netlist(KICAD_SAMPLE)
    assert len([w for w in model["warnings"] if "缺少 ref" in w]) == 0
    assert model["components"][0]["value"] == "STM32F103C8T6"


# ---------------- 扩展样例:I2C + UART + 按钮 + 晶振 + 退耦 ----------------


KICAD_FULL = """(export (version "E")
  (design (source "full.kicad_sch") (date "2026-01-01") (tool "Eeschema 8.0"))
  (components
    (comp (ref "U1") (value "STM32F103C8T6")
      (footprint "Package_QFP:LQFP-48_7x7mm_P0.5mm"))
    (comp (ref "U2") (value "BMP280") (footprint "Package_LGA:Bosch_LGA-8"))
    (comp (ref "U4") (value "CH340N") (footprint "Package_SO:SOP-8"))
    (comp (ref "D1") (value "LED_GREEN") (footprint "LED_SMD:LED_0603"))
    (comp (ref "SW1") (value "SW_Push") (footprint "Button_SMD:SW_SPST"))
    (comp (ref "Y1") (value "8MHz") (footprint "Crystal:Crystal_HC49"))
    (comp (ref "R1") (value "10k"))
    (comp (ref "R2") (value "4.7k"))
    (comp (ref "R3") (value "4.7k"))
    (comp (ref "C1") (value "100n"))
    (comp (ref "C2") (value "0.1u")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "U1") (pin "VSS"))
      (node (ref "U2") (pin "GND"))
      (node (ref "U4") (pin "GND"))
      (node (ref "SW1") (pin "2"))
      (node (ref "D1") (pin "K"))
      (node (ref "C1") (pin "2"))
      (node (ref "C2") (pin "2")))
    (net (code "2") (name "3V3")
      (node (ref "U1") (pin "VDD"))
      (node (ref "U2") (pin "VCC"))
      (node (ref "U4") (pin "VCC"))
      (node (ref "C1") (pin "1"))
      (node (ref "C2") (pin "1")))
    (net (code "3") (name "I2C_SDA")
      (node (ref "U1") (pin "PB7"))
      (node (ref "U2") (pin "SDA"))
      (node (ref "R2") (pin "1")))
    (net (code "4") (name "I2C_SCL")
      (node (ref "U1") (pin "PB6"))
      (node (ref "U2") (pin "SCL"))
      (node (ref "R3") (pin "1")))
    (net (code "5") (name "UART_TX")
      (node (ref "U1") (pin "PA9"))
      (node (ref "U4") (pin "RXD")))
    (net (code "6") (name "UART_RX")
      (node (ref "U1") (pin "PA10"))
      (node (ref "U4") (pin "TXD")))
    (net (code "7") (name "SW_USER")
      (node (ref "U1") (pin "PA0"))
      (node (ref "SW1") (pin "1"))
      (node (ref "R1") (pin "1")))
    (net (code "8") (name "Net-(D1-A)")
      (node (ref "D1") (pin "A"))
      (node (ref "U1") (pin "PA5")))))"""


def _analyze_full():
    model = parse_kicad_netlist(KICAD_FULL)
    return model, analyze_hardware(model)


def test_analyze_extended_buses():
    """扩展样例(UART/I2C/按钮/晶振/退耦)buses 全字段(移植 JS 同名用例)。"""
    _model, analysis = _analyze_full()
    # I2C
    i2c = next(b for b in analysis["buses"] if b["type"] == "i2c")
    assert i2c["pins"]["sda"] == "PB7"
    assert i2c["pins"]["scl"] == "PB6"
    assert i2c["devices"] == [{"ref": "U2", "part": "BMP280"}]
    assert "推断" in i2c["note"]
    # UART
    uart = next(b for b in analysis["buses"] if b["type"] == "uart")
    assert uart["pins"]["tx"] == "PA9"
    assert uart["pins"]["rx"] == "PA10"
    assert any(d["ref"] == "U4" for d in uart["devices"])
    tx_pm = next(p for p in analysis["pinmap"] if p["signal"] == "UART_TX")
    assert tx_pm["af"] == "USART1"
    assert tx_pm["direction"] == "out"
    rx_pm = next(p for p in analysis["pinmap"] if p["signal"] == "UART_RX")
    assert rx_pm["direction"] == "in"


def test_analyze_extended_gpio_power_issues():
    """扩展样例 gpio/power/issues 全字段 + codegen 关键宏(移植 JS 同名用例)。"""
    _model, analysis = _analyze_full()
    # LED:D1 阳极接 MCU PA5、阴极接地 → 高电平点亮
    led = next(g for g in analysis["gpio"] if g["role"] == "led")
    assert led["pin"] == "PA5"
    assert led["activeLow"] is False
    assert led["direction"] == "out"
    # 按钮:PA0 输入,另一端接地 → 低电平按下
    btn = next(g for g in analysis["gpio"] if g["role"] == "button")
    assert btn["pin"] == "PA0"
    assert btn["direction"] == "in"
    assert btn["activeLow"] is True
    # 电源
    rails = {r["name"]: r["connections"] for r in analysis["power"]["rails"]}
    assert rails["GND"] == 7
    assert rails["3V3"] == 5
    assert analysis["power"]["decouplingCount"] == 2  # 100n + 0.1u
    # 上拉电阻 R2/R3 的 3V3 端不在网络里 → 引脚悬空提示
    assert any("未连接" in str(i) for i in analysis["issues"]), f"issues: {analysis['issues']}"
    # codegen:关键宏
    h = generate_pinmap_header(analysis, {"guardPrefix": "FULL"})
    assert re.search(r"FULL_UART_TX_AF\s+USART1", h)
    assert re.search(r"FULL_I2C_SDA_PORT\s+GPIOB", h)
    assert re.search(r"FULL_SW_USER_DIR\s+FULL_PIN_INPUT", h)


def test_build_kicad_helper_roundtrip():
    """共享构造器 build_kicad 产出可解析网表(辅助自检,非 JS 移植用例)。"""
    text = build_kicad(
        comps=[{"ref": "D1", "value": "LED_BLUE"}],
        nets=[{"name": "3V3", "nodes": [{"ref": "D1", "pin": "A"}]}],
    )
    model = parse_kicad_netlist(text)
    assert model["components"][0]["ref"] == "U1"
    assert model["nets"][0]["name"] == "3V3"
