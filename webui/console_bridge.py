# SPDX-License-Identifier: MIT

"""console_bridge.py — WebConsole(CLIConsole)：把 agent 执行过程翻译为 SSE 事件。

挂钩 trae_agent 的 CLIConsole 抽象（agent/base_agent.py 在每步 THINKING →
LLM 返回 → 工具调用/结果 → COMPLETED/ERROR 时同步调用 update_status）。
本类识别「首次出现」的状态推进并翻译成事件 dict，放入 asyncio.Queue。

去重策略：update_status 在一个 step 生命周期内会被调用多次，且对象是
同一个可变 AgentStep（字段逐步填充）。因此：
- 首次见到更大 step_number → 发 {type:'step'}
- step.llm_response 首次非空且内容未发过 → 发 assistant + tool_use 事件
  → tool_use 只发一次（按 tool_call.call_id 去重）
- step.tool_results 首次非空 → 每个 result 发 tool_result（按 call_id 去重）
- execution.agent_state == COMPLETED → 发 final（只发一次）
- step.state == ERROR / execution.agent_state == ERROR → 发 error（只发一次）

queue 满时丢弃最旧事件（put_nowait + QueueFull 兜底），保证 update_status
永不阻塞 agent 的事件循环。
"""

from __future__ import annotations

import asyncio
import contextlib

from trae_agent.agent.agent_basics import (
    AgentExecution,
    AgentState,
    AgentStep,
    AgentStepState,
)
from trae_agent.utils.cli.cli_console import CLIConsole


class WebConsole(CLIConsole):
    """把 AgentStep/AgentExecution 的状态推进翻译为事件 dict 并塞入队列。"""

    def __init__(self, event_queue: "asyncio.Queue[dict]", queue_limit: int = 200) -> None:
        """初始化。

        Args:
            event_queue: 目标事件队列（每条是一个可 JSON 序列化的 dict）。
            queue_limit: 自建队列容量（仅用于满时丢最旧的判断基准，
                实际容量由调用方创建 queue 时决定）。
        """
        super().__init__()
        self._queue: "asyncio.Queue[dict]" = event_queue
        self._queue_limit = queue_limit
        # 已发送去重状态
        self._max_step_seen: int = 0
        self._assistant_sent: bool = (
            False  # final 的 assistant 文本与 tool_use 由同一 llm_response 驱动
        )
        self._sent_tool_use_ids: set[str] = set()
        self._sent_result_call_ids: set[str] = set()
        self._final_sent: bool = False
        self._error_sent: bool = False

    # ------------------------------------------------------------------
    # 事件入队（同步、非阻塞；满了丢最旧）
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        """非阻塞入队；queue 满时丢弃最旧一条再入队，保证不阻塞 agent。"""
        while True:
            try:
                self._queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()  # 丢最旧，给新事件腾位
                # 极端并发下若仍满（队列长度 0 的病态配置），放弃本条事件
                if self._queue.qsize() >= max(self._queue_limit, 1) and self._queue.qsize() > 0:
                    try:
                        self._queue.put_nowait(event)
                        return
                    except asyncio.QueueFull:
                        return

    # ------------------------------------------------------------------
    # CLIConsole 抽象实现
    # ------------------------------------------------------------------

    def update_status(
        self, agent_step: AgentStep | None = None, agent_execution: AgentExecution | None = None
    ) -> None:
        """核心挂钩：识别首次状态推进并翻译为事件（同步调用，绝不阻塞）。"""
        if agent_execution is not None:
            self.agent_execution = agent_execution

        if agent_step is not None:
            self._translate_step(agent_step)

        if agent_execution is not None:
            self._translate_execution(agent_execution)

    def _translate_step(self, step: AgentStep) -> None:
        """单个 AgentStep 的状态推进 → step/assistant/tool_use/tool_result 事件。"""
        # 1) 首次见到更大的 step_number → 新步骤开始（THINKING）
        if step.step_number > self._max_step_seen:
            self._max_step_seen = step.step_number
            self._assistant_sent = False  # 新 step 允许新的 assistant 内容
            self._emit({"type": "step", "n": step.step_number})

        # 2) llm_response 到达：assistant 文本（只发一次）与 tool_use（按 call_id 去重）
        llm = getattr(step, "llm_response", None)
        if llm is not None:
            if not self._assistant_sent:
                self._assistant_sent = True
                content = getattr(llm, "content", None)
                if content:  # 空文本不发事件，前端无需渲染空气泡
                    self._emit({"type": "assistant", "text": str(content)})
            for tool_call in getattr(llm, "tool_calls", None) or []:
                call_id = str(getattr(tool_call, "call_id", None) or "")
                if call_id and call_id in self._sent_tool_use_ids:
                    continue
                if call_id:
                    self._sent_tool_use_ids.add(call_id)
                args = getattr(tool_call, "arguments", None)
                # arguments 由 LLM 客户端解析为 dict；确保是 JSON 可序列化对象
                if not isinstance(args, dict):
                    args = {"value": args}
                self._emit(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": str(getattr(tool_call, "name", "") or ""),
                        "args": args,
                    }
                )

        # 3) tool_results 到达（按 call_id 只发一次）
        for tool_result in getattr(step, "tool_results", None) or []:
            call_id = str(getattr(tool_result, "call_id", None) or "")
            if call_id and call_id in self._sent_result_call_ids:
                continue
            if call_id:
                self._sent_result_call_ids.add(call_id)
            self._emit(
                {
                    "type": "tool_result",
                    "id": call_id,
                    "name": str(getattr(tool_result, "name", "") or ""),
                    "output": str(getattr(tool_result, "result", None) or ""),
                    "success": bool(getattr(tool_result, "success", False)),
                }
            )

        # 4) 单步错误（step.state == ERROR）
        if step.state == AgentStepState.ERROR and not self._error_sent:
            self._error_sent = True
            message = str(step.error) if step.error else "Agent 执行步骤出错"
            self._emit({"type": "error", "message": message})

    def _translate_execution(self, execution: AgentExecution) -> None:
        """整次执行的状态推进 → final / error 事件（各只发一次）。"""
        state = getattr(execution, "agent_state", None)
        if state == AgentState.COMPLETED and not self._final_sent:
            self._final_sent = True
            text = execution.final_result or ""
            self._emit({"type": "final", "text": str(text), "success": bool(execution.success)})
        elif state == AgentState.ERROR and not self._error_sent:
            self._error_sent = True
            message = str(execution.final_result or "Agent 执行失败")
            self._emit({"type": "error", "message": message})

    # ------------------------------------------------------------------
    # 其余抽象方法：server 模式空实现（无终端交互）
    # ------------------------------------------------------------------

    async def start(self) -> None:  # noqa: RUF029 - CLIConsole 接口约定
        """server 模式不需要启动显示（事件由 queue 消费方拉取）。"""

    def print_task_details(self, details: dict[str, str]) -> None:
        """任务详情不渲染（前端由 start 事件展示）。"""

    def print(self, message: str, color: str = "blue", bold: bool = False) -> None:
        """控制台消息（如 MCP 初始化）忽略。"""

    def get_task_input(self) -> str | None:
        """server 模式不交互；Agent.run 不调用（我们直接 new_task+execute_task）。"""
        return None

    def get_working_dir_input(self) -> str:
        """server 模式工作目录来自请求参数。"""
        return ""

    def stop(self) -> None:
        """无后台资源需要清理。"""
