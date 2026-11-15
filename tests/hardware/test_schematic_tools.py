# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""test_schematic_tools.py — trae-agent 两个自定义工具的端到端测试(Tool.execute 走 tmp_path)。

新增用例(非 JS 移植):覆盖 analyze_schematic / generate_pinmap 的参数 schema、
成功路径、文件不存在、格式枚举、类级缓存与 analysis.json 恢复、目录自动创建。
"""

import json
import re
from pathlib import Path

import pytest

from trae_agent.tools.base import ToolCallArguments
from trae_agent.tools.schematic_tools import AnalyzeSchematicTool, GeneratePinmapTool

# 与 tests/hardware/fixtures/blink/blink.net 相同的最小 KiCad 网表(内嵌,不依赖 cwd)。
KICAD_BODY = """(export (version "E")
  (components
    (comp (ref "U1") (value "STM32F103C8T6"))
    (comp (ref "U2") (value "BMP280"))
    (comp (ref "D1") (value "LED_PWR"))
    (comp (ref "SW1") (value "SW_Push"))
    (comp (ref "C1") (value "100n"))
    (comp (ref "C2") (value "100n")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "U1") (pin "VSS"))
      (node (ref "U2") (pin "GND"))
      (node (ref "SW1") (pin "2"))
      (node (ref "D1") (pin "K"))
      (node (ref "C1") (pin "2"))
      (node (ref "C2") (pin "2")))
    (net (code "2") (name "3V3")
      (node (ref "U1") (pin "VDD"))
      (node (ref "U2") (pin "VCC"))
      (node (ref "C1") (pin "1"))
      (node (ref "C2") (pin "1")))
    (net (code "3") (name "SDA")
      (node (ref "U1") (pin "PB9"))
      (node (ref "U2") (pin "SDA")))
    (net (code "4") (name "SCL")
      (node (ref "U1") (pin "PB8"))
      (node (ref "U2") (pin "SCL")))
    (net (code "5") (name "Net-(D1-A)")
      (node (ref "D1") (pin "A"))
      (node (ref "U1") (pin "PA5")))
    (net (code "6") (name "SW_USER")
      (node (ref "U1") (pin "PA0"))
      (node (ref "SW1") (pin "1")))))"""


@pytest.fixture(autouse=True)
def _reset_class_cache():
    """每个用例前后清空类级缓存,避免用例间串扰。"""
    AnalyzeSchematicTool.last_analysis = None
    yield
    AnalyzeSchematicTool.last_analysis = None


@pytest.fixture
def netfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """在 tmp_path 下放一个 blink.net,并把 cwd 切到 tmp_path。"""
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "blink.net"
    p.write_text(KICAD_BODY, encoding="utf-8")
    return p


class TestAnalyzeSchematicTool:
    """AnalyzeSchematicTool(analyze_schematic)的行为。"""

    async def test_tool_metadata(self):
        tool = AnalyzeSchematicTool()
        assert tool.get_name() == "analyze_schematic"
        params = {p.name: p for p in tool.get_parameters()}
        assert "path" in params and params["path"].required
        assert "format" in params and not params["format"].required
        assert params["format"].enum == ["kicad", "spice", "csv", "board-yaml"]

    async def test_success_kicad(self, netfile: Path):
        """成功路径:解析 blink.net → 写 hardware/analysis.json → summary 输出。"""
        tool = AnalyzeSchematicTool()
        result = await tool.execute(ToolCallArguments({"path": "blink.net"}))
        assert result.error_code == 0
        assert result.error is None
        # analysis.json 原子写到 <cwd>/hardware/
        analysis_path = Path.cwd() / "hardware" / "analysis.json"
        assert analysis_path.is_file()
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
        assert data["mcu"]["part"] == "STM32F103C8T6"
        assert any(b["type"] == "i2c" for b in data["buses"])
        # 类级缓存
        assert AnalyzeSchematicTool.last_analysis is not None
        # output 是 summaryMarkdown + issues,含关键中文小节
        assert "硬件分析结果" in result.output
        assert "I2C" in result.output

    async def test_file_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        tool = AnalyzeSchematicTool()
        result = await tool.execute(ToolCallArguments({"path": "missing.net"}))
        assert result.error_code != 0
        assert result.error is not None
        assert "not found" in result.error or "不存在" in result.error

    async def test_format_override_board_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """format 显式指定 board-yaml(文件后缀不看内容)。"""
        monkeypatch.chdir(tmp_path)
        yml = tmp_path / "board.yaml"
        yml.write_text(
            "board: tb\nmcu:\n  ref: U1\n  part: STM32F103C8T6\ncomponents:\n  - ref: D1\n    type: led\n    pins:\n      anode: PA5\n      cathode: GND\n",
            encoding="utf-8",
        )
        tool = AnalyzeSchematicTool()
        result = await tool.execute(
            ToolCallArguments({"path": "board.yaml", "format": "board-yaml"})
        )
        assert result.error_code == 0
        assert AnalyzeSchematicTool.last_analysis["sourceFormat"] == "board-yaml"

    async def test_output_truncated_to_4000(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """超长输出截断到前 4000 字。"""
        monkeypatch.chdir(tmp_path)
        big = KICAD_BODY
        for i in range(500):
            big += f'\n    (net (code "9") (name "PAD{i}") (node (ref "U1") (pin "PA1")))'
        p = tmp_path / "big.net"
        p.write_text(big, encoding="utf-8")
        tool = AnalyzeSchematicTool()
        result = await tool.execute(ToolCallArguments({"path": "big.net"}))
        assert result.error_code == 0
        assert len(result.output) <= 4000

    async def test_oversize_file_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """>200KB 的文件被拒绝。"""
        monkeypatch.chdir(tmp_path)
        p = tmp_path / "huge.net"
        p.write_text(
            "(export (nets " + '(net (code "1") (name "N"))' * 20000 + "))", encoding="utf-8"
        )
        assert p.stat().st_size > 200 * 1024
        tool = AnalyzeSchematicTool()
        result = await tool.execute(ToolCallArguments({"path": "huge.net"}))
        assert result.error_code != 0
        assert "too large" in (result.error or "")


class TestGeneratePinmapTool:
    """GeneratePinmapTool(generate_pinmap)的行为。"""

    async def test_requires_analysis_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """无缓存且无 analysis.json → 明确报错并提示先 analyze_schematic。"""
        monkeypatch.chdir(tmp_path)
        tool = GeneratePinmapTool()
        result = await tool.execute(ToolCallArguments({}))
        assert result.error_code != 0
        assert "analyze_schematic" in (result.error or "")

    async def test_end_to_end_default_out_path(self, netfile: Path):
        """端到端:analyze → generate 默认 board/board.h,宏合法。"""
        analyze_tool = AnalyzeSchematicTool()
        res = await analyze_tool.execute(ToolCallArguments({"path": "blink.net"}))
        assert res.error_code == 0
        gen_tool = GeneratePinmapTool()
        result = await gen_tool.execute(ToolCallArguments({}))
        assert result.error_code == 0, result.error
        out = Path.cwd() / "board" / "board.h"
        assert out.is_file(), "默认应写 board/board.h(自动建目录)"
        header = out.read_text(encoding="utf-8")
        assert "#ifndef BOARD_H" in header
        assert '#define BOARD_MCU_PART "STM32F103C8T6"' in header
        # 宏名合法性
        for m in re.findall(r"#define\s+(\S+)", header):
            assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", m), f"illegal macro: {m}"
        # SDA 总线宏 PB9
        assert re.search(r"BOARD_SDA_PORT\s+GPIOB", header)
        # 输出展示前 60 行
        lines = header.splitlines()
        assert "\n".join(lines[:60]) in result.output

    async def test_recovers_from_persisted_json(self, netfile: Path):
        """类级缓存为空时,从 <cwd>/hardware/analysis.json 恢复(跨进程场景)。"""
        analyze_tool = AnalyzeSchematicTool()
        await analyze_tool.execute(ToolCallArguments({"path": "blink.net"}))
        assert AnalyzeSchematicTool.last_analysis is not None
        AnalyzeSchematicTool.last_analysis = None  # 模拟新进程
        gen_tool = GeneratePinmapTool()
        result = await gen_tool.execute(ToolCallArguments({"out_path": "gen/board2.h"}))
        assert result.error_code == 0, result.error
        assert (Path.cwd() / "gen" / "board2.h").is_file()

    async def test_custom_out_path(self, netfile: Path):
        """自定义 out_path(含多层新目录)。"""
        await AnalyzeSchematicTool().execute(ToolCallArguments({"path": "blink.net"}))
        result = await GeneratePinmapTool().execute(ToolCallArguments({"out_path": "a/b/c/pins.h"}))
        assert result.error_code == 0, result.error
        assert (Path.cwd() / "a" / "b" / "c" / "pins.h").is_file()

    async def test_tool_metadata(self):
        tool = GeneratePinmapTool()
        assert tool.get_name() == "generate_pinmap"
        params = {p.name: p for p in tool.get_parameters()}
        assert "out_path" in params and not params["out_path"].required


class TestRegistryIntegration:
    """tools_registry 注册集成检查。"""

    def test_registry_contains_new_tools(self):
        from trae_agent.tools import tools_registry

        assert tools_registry.get("analyze_schematic") is AnalyzeSchematicTool
        assert tools_registry.get("generate_pinmap") is GeneratePinmapTool
        # 原有条目不动
        assert "bash" in tools_registry
        assert "sequentialthinking" in tools_registry
