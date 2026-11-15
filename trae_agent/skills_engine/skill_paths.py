# SPDX-License-Identifier: MIT

"""技能根目录定位：trae-embedded-agent/skills/。

skill_paths.py — 本包三行胶水，单独成文件以便测试与工具层共享同一路径。
"""

from pathlib import Path

# trae_agent/skills_engine/skill_paths.py -> trae_agent -> trae-embedded-agent -> skills/
SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"

__all__ = ["SKILLS_ROOT"]
