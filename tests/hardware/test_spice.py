# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""test_spice.py — SPICE 网表解析:类型归一、工程单位、注释、续行、subckt 展开、控制行忽略。

移植自 embedded-ai-studio/test/unit/hardware-spice.test.js(15 用例)。
"""

import math

from trae_agent.hardware.common import parse_engineering_value
from trae_agent.hardware.model import detect_format
from trae_agent.hardware.peripherals import analyze_hardware
from trae_agent.hardware.spice_netlist import logical_lines, parse_spice_netlist


def test_logical_lines_comments_continuation():
    """* 与 ; 注释跳过、+ 续行拼接、空行忽略(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "* header comment",
            "R1 N001 N002 10k",
            "+ this part is ignored comment? no, appended",
            "; semicolon comment",
            "",
            "C1 N002 0 100n",
        ]
    )
    lines = logical_lines(text)
    assert len(lines) == 2
    assert lines[0] == "R1 N001 N002 10k this part is ignored comment? no, appended"
    assert lines[1] == "C1 N002 0 100n"


def test_parse_spice_type_normalization():
    """基本器件行与类型归一(R/C/L/D/Q/J/M/U/V/I/X/S/K)(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "V1 in 0 5",
            "I1 in 0 1m",
            "R1 in out 10k",
            "C1 out 0 100n",
            "L1 out 0 10u",
            "D1 out 0 1N4148",
            "Q1 c b e 2N2222",
            "J1 d g s J310",
            "M1 d g s b IRF540",
            "U1 a b vcc gnd LM358",
            "S1 a b ctrl 0 SW1",
            "K1 L1 L2 0.99",
        ]
    )
    model = parse_spice_netlist(text)
    types = {c["ref"]: c["type"] for c in model["components"]}
    assert types["V1"] == "source"
    assert types["I1"] == "current"
    assert types["R1"] == "R"
    assert types["C1"] == "C"
    assert types["L1"] == "L"
    assert types["D1"] == "diode"
    assert types["Q1"] == "bjt"
    assert types["J1"] == "jfet"
    assert types["M1"] == "mosfet"
    assert types["U1"] == "ic"
    assert types["S1"] == "switch"
    assert types["K1"] == "coupled"


def test_parse_spice_subckt_value():
    """X 器件取 subckt 名做 value,类型 subckt(移植 JS 同名用例)。"""
    model = parse_spice_netlist(
        "\n".join(["XU1 a b c OPAMP", "X2 n1 n2 SUB9", "X3 n1 n2 SUB9 GAIN=2"])
    )
    x1 = next(c for c in model["components"] if c["ref"] == "XU1")
    assert x1["type"] == "subckt"
    assert x1["value"] == "OPAMP"
    x2 = next(c for c in model["components"] if c["ref"] == "X2")
    assert x2["value"] == "SUB9"
    # 带 key=value 参数时:最后一个非参数字段作 subckt 名
    x3 = next(c for c in model["components"] if c["ref"] == "X3")
    assert x3["value"] == "SUB9"


def test_parse_spice_subckt_flattening():
    """.subckt/.ends 之间器件归入 subckt 并加前缀展平(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            "* top level",
            "R1 a b 1k",
            ".subckt OPAMP inp inn out",
            "Rin inp inn 1meg",
            "Cfb out inn 10p",
            ".ends OPAMP",
            "XU1 a b c OPAMP",
        ]
    )
    model = parse_spice_netlist(text)
    refs = [c["ref"] for c in model["components"]]
    assert "R1" in refs
    assert "OPAMP.Rin" in refs, f"应有展平器件 OPAMP.Rin: {','.join(refs)}"
    assert "OPAMP.Cfb" in refs
    assert "XU1" in refs
    rin = next(c for c in model["components"] if c["ref"] == "OPAMP.Rin")
    assert rin["value"] == "1meg"
    assert math.isclose(rin["valueNumeric"], 1e6, rel_tol=1e-12)
    # subckt 定义信息进 warnings
    assert any("子电路定义 OPAMP" in w for w in model["warnings"])


def test_parse_spice_nested_subckt():
    """嵌套 subckt(多层前缀)(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            ".subckt A x y",
            ".subckt B p q",
            "R1 p q 1k",
            ".ends B",
            "X1 x y B",
            ".ends A",
            "XTOP a b A",
        ]
    )
    model = parse_spice_netlist(text)
    refs = [c["ref"] for c in model["components"]]
    assert "B.R1" in refs, f"内层器件应有 B. 前缀: {','.join(refs)}"
    assert "A.X1" in refs
    assert "XTOP" in refs


def test_parse_spice_unclosed_subckt():
    """未闭合的 .subckt 不崩溃并警告(移植 JS 同名用例)。"""
    model = parse_spice_netlist("\n".join([".subckt OP a b", "R1 a b 1k"]))
    assert any("未用 .ends 闭合" in w for w in model["warnings"])
    assert any(c["ref"] == "OP.R1" for c in model["components"])


def test_parse_spice_extra_ends():
    """.ends 多余不崩溃(移植 JS 同名用例)。"""
    model = parse_spice_netlist("R1 a b 1k\n.ends OOPS")
    assert any(".ends" in w for w in model["warnings"])
    assert any(c["ref"] == "R1" for c in model["components"])


def test_parse_spice_control_lines_ignored():
    """控制行 .include/.lib/.model/.param/.option 忽略且记 warnings(移植 JS 同名用例)。"""
    text = "\n".join(
        [
            ".include other.cir",
            ".lib diodes.lib",
            ".model D1N4148 D(IS=1n)",
            ".param TEMP=27",
            ".option nopage",
            "R1 a b 1k",
        ]
    )
    model = parse_spice_netlist(text)
    assert len(model["components"]) == 1
    assert any(".include" in w for w in model["warnings"])
    assert any(".lib" in w for w in model["warnings"])
    # .model/.param/.option 是常见控制行,静默忽略(不产生"无法识别"警告)
    assert not any("无法识别" in w for w in model["warnings"])


def test_parse_spice_engineering_suffixes():
    """工程后缀 value 解析(k/M/meg/m/u/n/p/f/g)(移植 JS 同名用例)。"""
    model = parse_spice_netlist(
        "\n".join(
            [
                "R1 a b 10k",
                "R2 a b 1M",
                "R3 a b 2meg",
                "R4 a b 5m",
                "C1 a b 100n",
                "C2 a b 47p",
                "C3 a b 1u",
                "L1 a b 10u",
                "R5 a b 1g",
            ]
        )
    )
    v = {c["ref"]: c.get("valueNumeric") for c in model["components"]}

    def near(actual, expected):
        assert actual is not None and math.isclose(actual, expected, rel_tol=1e-12), (
            f"{actual} ≈ {expected}"
        )

    near(v["R1"], 1e4)
    near(v["R2"], 1e6)  # 大写 M = 兆(工程记法)
    near(v["R3"], 2e6)  # meg 一律兆
    near(v["R4"], 5e-3)  # 小写 m = 毫
    near(v["C1"], 1e-7)
    near(v["C2"], 47e-12)
    near(v["C3"], 1e-6)
    near(v["R5"], 1e9)


def test_parse_spice_case_insensitive():
    """大小写不敏感(器件名与单位)(移植 JS 同名用例)。"""
    model = parse_spice_netlist("r1 A B 10K\nc1 a b 100N")
    assert len(model["components"]) == 2
    r1 = next(c for c in model["components"] if c["ref"] == "r1")
    assert math.isclose(r1["valueNumeric"], 1e4, rel_tol=1e-12)


def test_parse_spice_nets_derived_from_pins():
    """网表由器件引脚推导 nets(移植 JS 同名用例)。"""
    model = parse_spice_netlist("R1 n1 n2 1k\nC1 n2 0 100n")
    net_names = sorted(n["name"] for n in model["nets"])
    assert net_names == ["0", "n1", "n2"]
    n2 = next(n for n in model["nets"] if n["name"] == "n2")
    assert len(n2["connections"]) == 2
    assert any(c["ref"] == "R1" and c["pin"] == "n2" for c in n2["connections"])
    assert any(c["ref"] == "C1" and c["pin"] == "n2" for c in n2["connections"])


def test_parse_spice_malformed_inputs():
    """畸形输入不崩溃(空文本/只有注释/未知首字母)(移植 JS 同名用例)。"""
    empty = parse_spice_netlist("")
    assert len(empty["components"]) == 0
    assert len(empty["warnings"]) > 0
    only_comments = parse_spice_netlist("* nothing here\n; still nothing")
    assert len(only_comments["components"]) == 0
    junk = parse_spice_netlist("Z1 a b weird")
    assert len(junk["components"]) == 0
    assert any("无法识别的器件行" in w for w in junk["warnings"])


def test_parse_engineering_value_fundamentals():
    """基础值与夹心记法(移植 JS:fundamentals and sandwich notation)。"""
    assert parse_engineering_value("10k") == 1e4
    assert parse_engineering_value("4k7") == 4.7e3
    assert parse_engineering_value("4R7") == 4.7
    assert parse_engineering_value("100n") == 100 * 1e-9
    assert parse_engineering_value("1meg") == 1e6
    assert parse_engineering_value("2.2u") == 2.2e-6
    assert parse_engineering_value("abc") is None
    assert parse_engineering_value("") is None


def test_detect_format_spice():
    """spice 文件名与内容嗅探(移植 JS 同名用例)。"""
    assert detect_format("* comment\nR1 a b 1k", "x.cir") == "spice"
    assert detect_format("R1 a b 1k", "x.spc") == "spice"
    assert detect_format("R1 a b 1k", "x.sp") == "spice"
    assert detect_format("R1 a b 1k", "x.spi") == "spice"
    # 内容嗅探:* 注释开头
    assert detect_format("* title line\nR1 a b 1k\nC1 b 0 1n") == "spice"
    # 内容嗅探:.subckt 开头
    assert detect_format(".subckt A b c\n.ends") == "spice"
    # 内容嗅探:多于 1 行网表行
    assert detect_format("R1 a b 1k\nC1 b 0 1n") == "spice"


def test_analyze_spice_no_mcu_fallback():
    """spice 模型可进分析(无 MCU 场景的兜底)(移植 JS 同名用例)。"""
    model = parse_spice_netlist("R1 a b 1k\nC1 b 0 100n")
    analysis = analyze_hardware(model)
    assert isinstance(analysis["issues"], list)
    assert any(("未找到 MCU" in str(i)) or ("电源轨" in str(i)) for i in analysis["issues"])
