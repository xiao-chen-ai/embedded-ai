# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""tests/hardware/conftest.py — 硬件移植测试共享 fixture 与辅助构造器。

自 embedded-ai-studio/test/unit/hardware-*.test.js(89 用例)移植;
fixture 网表取自 tests/hardware/fixtures/blink/(自 examples/blink-board 只读复制)。
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def blink_net_path() -> Path:
    """真实样例 blink.net(KiCad 网表)。"""
    return FIXTURES_DIR / "blink" / "blink.net"


@pytest.fixture(scope="session")
def blink_net_text(blink_net_path: Path) -> str:
    return blink_net_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def board_yaml_path() -> Path:
    """真实样例 board.yaml(规范板级描述)。"""
    return FIXTURES_DIR / "blink" / "board.yaml"


@pytest.fixture(scope="session")
def board_yaml_text(board_yaml_path: Path) -> str:
    return board_yaml_path.read_text(encoding="utf-8")


def build_kicad(
    mcu_part: str = "STM32F103C8T6",
    skip_mcu: bool = False,
    comps: list[dict] | None = None,
    nets: list[dict] | None = None,
) -> str:
    """构造 MCU + 若干器件的最小 KiCad 网表(对应 JS 测试的 kicad() 构造器)。"""
    comps = comps or []
    nets = nets or []
    comp_lines: list[str] = []
    if not skip_mcu:
        comp_lines.append(f'(comp (ref "U1") (value "{mcu_part}"))')
    for c in comps:
        footprint = f' (footprint "{c["footprint"]}")' if c.get("footprint") else ""
        comp_lines.append(f'(comp (ref "{c["ref"]}") (value "{c.get("value") or ""}"){footprint})')
    net_lines: list[str] = []
    for n in nets:
        nodes = "\n      ".join(
            f'(node (ref "{nd["ref"]}") (pin "{nd["pin"]}"))' for nd in n.get("nodes", [])
        )
        net_lines.append(f'(net (code "1") (name "{n["name"]}")\n      {nodes})')
    return (
        '(export (version "E") (components\n    '
        + "\n    ".join(comp_lines)
        + ")\n  (nets\n    "
        + "\n    ".join(net_lines)
        + "))"
    )
