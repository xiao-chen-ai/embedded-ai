# SPDX-License-Identifier: MIT

"""skill_tools 三个工具的端到端测试（经 Tool.execute），含越界拒绝与脚本执行。

tmp 技能目录全部自造（不依赖注入的真实 39 个）；另有一个真实验证用例
扫 trae-embedded-agent/skills/。
"""

from pathlib import Path

import pytest

from trae_agent.skills_engine.skill_paths import SKILLS_ROOT
from trae_agent.tools.skill_tools import LoadSkillTool, RunSkillScriptTool, SearchSkillsTool


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf8")


@pytest.fixture()
def skill_root(tmp_path: Path) -> Path:
    """tmp 技能根：zh/serial + mp/tdd 两个自造技能。"""
    root = tmp_path / "skills"
    d = root / "zh" / "serial"
    _write(
        d / "SKILL.md",
        "---\nname: serial\ndescription: 串口调试\n---\n\n# Serial\n\n说明正文。",
    )
    _write(d / "scripts" / "serial_scan.py", 'print("scan")')
    _write(d / "config.example.json", '{"port": "COM3"}')
    t = root / "mp" / "tdd"
    _write(
        t / "SKILL.md",
        "---\nname: tdd\ndescription: Test-driven development\n---\n\n# TDD\n\n正文。",
    )
    _write(t / "scripts" / "hello.py", "import sys\nprint('args:', sys.argv[1:])")
    return root


# ---------------- search_skills ----------------


async def test_search_skills_finds_serial_via_tool(skill_root: Path) -> None:
    tool = SearchSkillsTool(skills_root=skill_root)
    result = await tool.execute({"query": "调试串口"})
    assert result.error is None
    assert "zh/serial" in (result.output or "")
    assert "load_skill" in (result.output or "")


async def test_search_skills_empty_query_lists_first_10(skill_root: Path) -> None:
    tool = SearchSkillsTool(skills_root=skill_root)
    result = await tool.execute({"query": ""})
    assert result.error is None
    out = result.output or ""
    assert "zh/serial" in out and "mp/tdd" in out
    assert "load_skill" in out


async def test_search_skills_no_match_reports_empty(skill_root: Path) -> None:
    tool = SearchSkillsTool(skills_root=skill_root)
    result = await tool.execute({"query": "zzzzqqqq"})
    assert result.error is None
    # 完全不匹配时可能有小 Jaccard 分；只验证不抛 + 文本响应
    assert isinstance(result.output, str)


async def test_search_skills_never_raises_on_bad_root(tmp_path: Path) -> None:
    tool = SearchSkillsTool(skills_root=tmp_path / "nope")
    result = await tool.execute({"query": "anything"})
    assert result.error is None
    assert "技能库为空" in (result.output or "")


# ---------------- load_skill ----------------


async def test_load_skill_body_and_side_files(skill_root: Path) -> None:
    tool = LoadSkillTool(skills_root=skill_root)
    result = await tool.execute({"name": "serial"})
    assert result.error is None
    out = result.output or ""
    assert "# Serial" in out
    assert "scripts/serial_scan.py" in out  # 附属文件清单
    assert "config.example.json" in out


async def test_load_skill_side_file(skill_root: Path) -> None:
    tool = LoadSkillTool(skills_root=skill_root)
    result = await tool.execute({"name": "zh/serial", "file": "scripts/serial_scan.py"})
    assert result.error is None
    assert 'print("scan")' in (result.output or "")


async def test_load_skill_fuzzy_name_match(skill_root: Path) -> None:
    # 裸名 serial 解析到 zh/serial；mp/tdd 形式也支持
    tool = LoadSkillTool(skills_root=skill_root)
    r1 = await tool.execute({"name": "serial"})
    assert r1.error is None
    r2 = await tool.execute({"name": "mp/tdd"})
    assert r2.error is None
    assert "# TDD" in (r2.output or "")


async def test_load_skill_unknown_name_fails(skill_root: Path) -> None:
    tool = LoadSkillTool(skills_root=skill_root)
    result = await tool.execute({"name": "no-such-skill"})
    assert result.error is not None
    assert "未找到技能" in result.error


async def test_load_skill_rejects_path_traversal(skill_root: Path) -> None:
    tool = LoadSkillTool(skills_root=skill_root)
    result = await tool.execute({"name": "serial", "file": "../outside.txt"})
    assert result.error is not None
    result2 = await tool.execute({"name": "serial", "file": "scripts/../../SKILL.md"})
    assert result2.error is not None


async def test_load_skill_rejects_git_and_absolute(skill_root: Path) -> None:
    tool = LoadSkillTool(skills_root=skill_root)
    r1 = await tool.execute({"name": "serial", "file": ".git/config"})
    assert r1.error is not None
    r2 = await tool.execute({"name": "serial", "file": "/etc/passwd"})
    assert r2.error is not None
    # Windows 盘符形式也无法越界（解析后在技能目录外）
    r3 = await tool.execute({"name": "serial", "file": "C:/Windows/win.ini"})
    assert r3.error is not None


async def test_load_skill_rejects_oversized_file(skill_root: Path, tmp_path: Path) -> None:
    _write(skill_root / "zh" / "serial" / "big.txt", "x" * (100 * 1024 + 1))
    tool = LoadSkillTool(skills_root=skill_root)
    result = await tool.execute({"name": "serial", "file": "big.txt"})
    assert result.error is not None
    assert "文件过大" in result.error
    _cleanup(skill_root / "zh" / "serial" / "big.txt")


def _cleanup(p: Path) -> None:
    p.unlink(missing_ok=True)


# ---------------- run_skill_script ----------------


async def test_run_skill_script_hello_world(skill_root: Path) -> None:
    tool = RunSkillScriptTool(skills_root=skill_root)
    result = await tool.execute({"skill": "serial", "script": "hello_missing.py"})
    assert result.error is not None  # serial 没有 hello_missing.py
    # tdd 有 hello.py：真实 spawn 当前 venv python
    result2 = await tool.execute({"skill": "tdd", "script": "hello.py", "args": ["--json"]})
    assert result2.error is None
    out = result2.output or ""
    assert "args:" in out and "--json" in out
    assert "exitCode: 0" in out


async def test_run_skill_script_bootstrap_config(skill_root: Path) -> None:
    # 首次运行自动从 config.example.json 复制 config.json
    cfg = skill_root / "zh" / "serial" / "config.json"
    assert not cfg.exists()
    tool = RunSkillScriptTool(skills_root=skill_root)
    await tool.execute({"skill": "serial", "script": "serial_scan.py"})
    assert cfg.exists()
    assert cfg.read_text(encoding="utf8") == '{"port": "COM3"}'


async def test_run_skill_script_rejects_path_separator(skill_root: Path) -> None:
    tool = RunSkillScriptTool(skills_root=skill_root)
    for bad in ("sub/hello.py", "a\\hello.py", "..", "."):
        result = await tool.execute({"skill": "serial", "script": bad})
        assert result.error is not None
        assert "纯文件名" in result.error or "分隔符" in result.error


async def test_run_skill_script_missing_script_and_skill(skill_root: Path) -> None:
    tool = RunSkillScriptTool(skills_root=skill_root)
    r1 = await tool.execute({"skill": "serial", "script": "missing.py"})
    assert r1.error is not None
    assert "不存在" in r1.error or "脚本" in r1.error
    r2 = await tool.execute({"skill": "no-such-skill", "script": "hello.py"})
    assert r2.error is not None
    assert "未找到技能" in r2.error


async def test_run_skill_script_missing_args_failures(skill_root: Path) -> None:
    tool = RunSkillScriptTool(skills_root=skill_root)
    r1 = await tool.execute({"skill": "", "script": "hello.py"})
    assert r1.error is not None
    r2 = await tool.execute({"skill": "serial"})
    assert r2.error is not None
    r3 = await tool.execute({"skill": "tdd", "script": "hello.py", "args": "not-a-list"})
    assert r3.error is not None


async def test_run_skill_script_import_error_hint(skill_root: Path) -> None:
    # 脚本 import venv 里没有的模块 → 输出原样带回 + uv pip install 提示
    # （用自造的不存在模块名，避免与环境里实际安装的包耦合）
    _write(
        skill_root / "mp" / "tdd" / "scripts" / "needs_dep.py",
        "import definitely_not_installed_xyz\nprint('loaded')",
    )
    tool = RunSkillScriptTool(skills_root=skill_root)
    result = await tool.execute({"skill": "tdd", "script": "needs_dep.py"})
    assert result.error is None  # 脚本失败不是工具失败：输出原样带回
    out = result.output or ""
    assert "ModuleNotFoundError" in out or "ImportError" in out
    assert "uv pip install" in out
    assert "exitCode:" in out


async def test_run_skill_script_stdout_and_stderr(skill_root: Path) -> None:
    _write(
        skill_root / "mp" / "tdd" / "scripts" / "both.py",
        "import sys\nprint('to-out')\nprint('to-err', file=sys.stderr)\n",
    )
    tool = RunSkillScriptTool(skills_root=skill_root)
    result = await tool.execute({"skill": "tdd", "script": "both.py"})
    assert result.error is None
    out = result.output or ""
    assert "to-out" in out and "to-err" in out and "exitCode: 0" in out


# ---------------- 真实技能库端到端 ----------------


async def test_real_library_search_keil() -> None:
    """真实验证用例：默认 SKILLS_ROOT 上 search_skills("keil") → zh/keil 命中。"""
    tool = SearchSkillsTool()  # 默认 SKILLS_ROOT
    result = await tool.execute({"query": "keil"})
    assert result.error is None
    assert "zh/keil" in (result.output or "")


async def test_real_library_load_keil_skill() -> None:
    tool = LoadSkillTool()  # 默认 SKILLS_ROOT
    result = await tool.execute({"name": "keil"})
    assert result.error is None
    assert "SKILL" in (result.output or "") or "keil" in (result.output or "")


def test_real_library_root_exists() -> None:
    assert SKILLS_ROOT.is_dir()
