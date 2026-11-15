# SPDX-License-Identifier: MIT
"""skills_engine — 技能发现 / frontmatter 解析 / 自然语言匹配的纯标准库 Python 实现。

移植自 embedded-ai-studio 的 core/skills/{loader,matcher}.js，行为与 JS 版对齐。
"""

from trae_agent.skills_engine.loader import EXTRA_FILE_NAMES, Skill, parse_skill_md, scan_skills
from trae_agent.skills_engine.matcher import (
    extract_query_terms,
    glob_matches_everything,
    glob_to_regex,
    match_any_glob,
    match_skills,
)
from trae_agent.skills_engine.skill_paths import SKILLS_ROOT

__all__ = [
    "EXTRA_FILE_NAMES",
    "Skill",
    "parse_skill_md",
    "scan_skills",
    "match_skills",
    "match_any_glob",
    "glob_to_regex",
    "extract_query_terms",
    "glob_matches_everything",
    "SKILLS_ROOT",
]
