# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""Tools module for Trae Agent."""

from trae_agent.tools.base import Tool, ToolCall, ToolExecutor, ToolResult
from trae_agent.tools.bash_tool import BashTool
from trae_agent.tools.ckg_tool import CKGTool
from trae_agent.tools.edit_tool import TextEditorTool
from trae_agent.tools.json_edit_tool import JSONEditTool
from trae_agent.tools.keil_tool import KeilBuildFlashTool
from trae_agent.tools.schematic_tools import AnalyzeSchematicTool, GeneratePinmapTool
from trae_agent.tools.sequential_thinking_tool import SequentialThinkingTool
from trae_agent.tools.skill_tools import LoadSkillTool, RunSkillScriptTool, SearchSkillsTool
from trae_agent.tools.task_done_tool import TaskDoneTool

__all__ = [
    "Tool",
    "ToolResult",
    "ToolCall",
    "ToolExecutor",
    "BashTool",
    "TextEditorTool",
    "JSONEditTool",
    "SequentialThinkingTool",
    "TaskDoneTool",
    "CKGTool",
    "SearchSkillsTool",
    "LoadSkillTool",
    "RunSkillScriptTool",
    "AnalyzeSchematicTool",
    "GeneratePinmapTool",
    "KeilBuildFlashTool",
]

tools_registry: dict[str, type[Tool]] = {
    "bash": BashTool,
    "str_replace_based_edit_tool": TextEditorTool,
    "json_edit_tool": JSONEditTool,
    "sequentialthinking": SequentialThinkingTool,
    "task_done": TaskDoneTool,
    "ckg": CKGTool,
    "search_skills": SearchSkillsTool,
    "load_skill": LoadSkillTool,
    "run_skill_script": RunSkillScriptTool,
    "analyze_schematic": AnalyzeSchematicTool,
    "generate_pinmap": GeneratePinmapTool,
    "keil_build_flash": KeilBuildFlashTool,
}
