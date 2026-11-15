# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

from typing import override

from trae_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter


class TaskDoneTool(Tool):
    """Tool to mark a task as done."""

    def __init__(self, model_provider: str | None = None):
        super().__init__(model_provider)

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "task_done"

    @override
    def get_description(self) -> str:
        return (
            "Report the completion of the task. Note that you cannot call this tool "
            "before any verification is done. You can write reproduce / test script to "
            "verify your solution.\n"
            "Pass your final answer/summary in the `summary` parameter (plain text, "
            "markdown allowed, in the user's language)."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="summary",
                type="string",
                description=(
                    "The final answer or work summary for the user. Write the complete "
                    "answer here — the user sees this message directly."
                ),
                required=True,
            )
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        summary = str(arguments.get("summary", "") or "")
        if not summary.strip():
            return ToolExecResult(
                error="task_done requires a non-empty `summary` — write your final answer for the user.",
                error_code=-1,
            )
        return ToolExecResult(output="Task done.")
