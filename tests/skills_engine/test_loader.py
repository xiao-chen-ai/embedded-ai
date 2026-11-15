# SPDX-License-Identifier: MIT

"""scan_skills 测试：两级布局发现、id 规则、去重、disabled、extra_files，
以及注入的真实技能库（trae-embedded-agent/skills/ = 39 个）。
"""

from pathlib import Path

from trae_agent.skills_engine.loader import Skill, scan_skills
from trae_agent.skills_engine.skill_paths import SKILLS_ROOT


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_scan_discovers_two_level_layout_and_builds_ids(tmp_path: Path) -> None:
    _write(
        tmp_path / "mp" / "tdd" / "SKILL.md",
        "---\nname: tdd\ndescription: Test-driven development.\n---\n\n# TDD\n",
    )
    _write(
        tmp_path / "mp" / "ask-matt" / "SKILL.md",
        "---\nname: ask-matt\ndescription: Router.\n---\n\n# Ask Matt\n",
    )
    _write(
        tmp_path / "zh" / "serial" / "SKILL.md",
        "---\nname: serial\ndescription: 串口调试。\n---\n\n# Serial\n",
    )
    # 必须被忽略的噪声
    _write(tmp_path / "mp" / "notes.txt", "not a skill")
    _write(tmp_path / "mp" / "notaskill" / "other.md", "no SKILL.md")

    skills, warnings = scan_skills([tmp_path])
    assert warnings == []
    assert sorted(s.id for s in skills) == ["mp/ask-matt", "mp/tdd", "zh/serial"]
    tdd = next(s for s in skills if s.id == "mp/tdd")
    assert tdd.tag == "mp"
    assert tdd.name == "tdd"
    assert tdd.description == "Test-driven development."
    assert Path(tdd.entry_path) == tmp_path / "mp" / "tdd" / "SKILL.md"
    assert tdd.enabled is True


def test_scan_missing_name_falls_back_to_dir_name_with_warning(tmp_path: Path) -> None:
    _write(
        tmp_path / "mp" / "code-review" / "SKILL.md",
        "---\ndescription: no name here\n---\n\n# Code review\n",
    )
    skills, warnings = scan_skills([tmp_path])
    assert len(skills) == 1
    assert skills[0].id == "mp/code-review"
    assert skills[0].name == "code-review"
    assert any("mp/code-review" in w and "name" in w for w in warnings)


def test_scan_missing_description_tolerated(tmp_path: Path) -> None:
    _write(tmp_path / "mp" / "tiny" / "SKILL.md", "---\nname: tiny\n---\n\n# Tiny\n")
    skills, _ = scan_skills([tmp_path])
    assert len(skills) == 1
    assert skills[0].description == ""


def test_scan_collects_extra_files(tmp_path: Path) -> None:
    d = tmp_path / "zh" / "serial"
    _write(d / "SKILL.md", "---\nname: serial\ndescription: d\n---\nbody")
    _write(d / "config.json", "{}")
    _write(d / "config.example.json", "{}")
    _write(d / "scripts" / "serial_scan.py", "print(1)")
    skills, _ = scan_skills([tmp_path])
    s = next(x for x in skills if x.id == "zh/serial")
    assert "config.json" in s.extra_files
    assert "config.example.json" not in s.extra_files  # 不在 EXTRA_FILE_NAMES 清单里


def test_scan_dedupes_same_id_across_roots_first_wins(tmp_path: Path) -> None:
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    _write(root1 / "mp" / "tdd" / "SKILL.md", "---\nname: tdd\ndescription: first\n---\n")
    _write(root2 / "mp" / "tdd" / "SKILL.md", "---\nname: tdd\ndescription: second\n---\n")
    skills, warnings = scan_skills([root1, root2])
    assert len(skills) == 1
    assert skills[0].description == "first"
    assert Path(skills[0].dir) == root1 / "mp" / "tdd"
    assert len(warnings) == 1
    assert "mp/tdd" in warnings[0]


def test_scan_disabled_marks_not_enabled(tmp_path: Path) -> None:
    _write(tmp_path / "mp" / "tdd" / "SKILL.md", "---\nname: tdd\ndescription: x\n---\n")
    skills, _ = scan_skills([tmp_path], disabled={"mp/tdd"})
    assert len(skills) == 1
    assert skills[0].enabled is False
    # 验证 scan_skills 未修改入参集合语义（set/list 都支持）
    skills2, _ = scan_skills([tmp_path], disabled=["mp/tdd"])
    assert skills2[0].enabled is False


def test_scan_ignores_git_and_node_modules(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "hook" / "SKILL.md", "---\nname: bad\n---\n")
    _write(tmp_path / "node_modules" / "x" / "SKILL.md", "---\nname: bad2\n---\n")
    skills, _ = scan_skills([tmp_path])
    assert len(skills) == 0


def test_scan_accepts_single_root_path(tmp_path: Path) -> None:
    _write(tmp_path / "mp" / "tdd" / "SKILL.md", "---\nname: tdd\ndescription: x\n---\n")
    skills, _ = scan_skills(tmp_path)
    assert [s.id for s in skills] == ["mp/tdd"]


def test_scan_sorts_by_id(tmp_path: Path) -> None:
    for name in ("zzz", "aaa", "mmm"):
        _write(tmp_path / "mp" / name / "SKILL.md", f"---\nname: {name}\ndescription: d\n---\n")
    skills, _ = scan_skills([tmp_path])
    assert [s.id for s in skills] == ["mp/aaa", "mp/mmm", "mp/zzz"]


def test_scan_returns_skill_dataclass(tmp_path: Path) -> None:
    _write(tmp_path / "mp" / "tdd" / "SKILL.md", "---\nname: tdd\n---\n")
    skills, _ = scan_skills([tmp_path])
    assert isinstance(skills[0], Skill)


def test_scan_missing_root_warns_not_raises(tmp_path: Path) -> None:
    skills, warnings = scan_skills([tmp_path / "does-not-exist"])
    assert skills == []
    assert len(warnings) == 1
    assert "无法读取" in warnings[0]


# ---------------- 真实注入技能库验证 ----------------


def test_real_skills_root_scans_39_skills() -> None:
    """真实验证用例：扫注入后的 trae-embedded-agent/skills/ 应得 39 个技能。"""
    assert SKILLS_ROOT.exists(), f"SKILLS_ROOT 不存在: {SKILLS_ROOT}"
    skills, warnings = scan_skills([SKILLS_ROOT])
    assert len(skills) == 39, f"期望 39 个技能，实际 {len(skills)}: {[s.id for s in skills]}"
    assert warnings == []


def test_real_skills_root_id_pattern() -> None:
    skills, _ = scan_skills([SKILLS_ROOT])
    mp = [s for s in skills if s.tag == "mp"]
    zh = [s for s in skills if s.tag == "zh"]
    assert len(mp) == 27
    assert len(zh) == 12
    assert all(s.id.startswith("mp/") for s in mp)
    assert all(s.id.startswith("zh/") for s in zh)
    # 必要技能在场
    for expect in ("mp/tdd", "mp/code-review", "zh/serial", "zh/gcc", "zh/keil"):
        assert any(s.id == expect for s in skills), f"缺少 {expect}"


def test_real_skills_source_markers(tmp_path: Path) -> None:
    """每个 tag 目录的 .source.json 存在且计数正确。"""
    import json

    for tag, count in (("mp", 27), ("zh", 12)):
        src = SKILLS_ROOT / tag / ".source.json"
        assert src.is_file(), f"缺少 {src}"
        data = json.loads(src.read_text(encoding="utf-8"))
        assert data["count"] == count
        assert data["tag"] == tag
        assert "repo" in data and "importedAt" in data
