# SPDX-License-Identifier: MIT

"""test_console_bridge.py — WebConsole 事件翻译与去重逻辑单测（不经 HTTP）。"""

from __future__ import annotations

import asyncio

from trae_agent.agent.agent_basics import AgentExecution, AgentState, AgentStep, AgentStepState
from trae_agent.tools.base import ToolCall, ToolResult
from trae_agent.utils.llm_clients.llm_basics import LLMResponse
from webui.console_bridge import WebConsole

# pyproject asyncio_mode=auto：异步测试无需显式标记


def _drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _execution(
    state: AgentState, final: str | None = "done", success: bool = True
) -> AgentExecution:
    execution = AgentExecution(task="t", steps=[], final_result=final, success=success)
    execution.agent_state = state
    return execution


async def test_step_events_dedup():
    q: asyncio.Queue = asyncio.Queue()
    console = WebConsole(q)

    step = AgentStep(step_number=1, state=AgentStepState.THINKING)
    # 同一步多次 THINKING 更新 → 只发一个 step 事件
    console.update_status(step, _execution(AgentState.RUNNING, final=None))
    console.update_status(step, _execution(AgentState.RUNNING, final=None))
    console.update_status(step, _execution(AgentState.RUNNING, final=None))

    events = _drain(q)
    assert events == [{"type": "step", "n": 1}]


async def test_assistant_and_tool_use_once():
    q: asyncio.Queue = asyncio.Queue()
    console = WebConsole(q)
    step = AgentStep(step_number=1, state=AgentStepState.THINKING)
    console.update_status(step, _execution(AgentState.RUNNING, final=None))

    # llm_response 分两次填充（先 content 后 tool_calls）——真实 base_agent 时序
    step.llm_response = LLMResponse(content="文本")
    console.update_status(step, _execution(AgentState.RUNNING, final=None))
    step.llm_response = LLMResponse(
        content="文本",
        tool_calls=[ToolCall(name="bash", call_id="c1", arguments={"command": "ls"})],
    )
    console.update_status(step, _execution(AgentState.RUNNING, final=None))
    # 再来一次同样的 update_status → 不重复
    console.update_status(step, _execution(AgentState.RUNNING, final=None))

    events = _drain(q)
    assert events == [
        {"type": "step", "n": 1},
        {"type": "assistant", "text": "文本"},
        {"type": "tool_use", "id": "c1", "name": "bash", "args": {"command": "ls"}},
    ]


async def test_tool_results_once():
    q: asyncio.Queue = asyncio.Queue()
    console = WebConsole(q)

    class _S:  # 轻量 step 替身
        step_number = 1
        state = AgentStepState.CALLING_TOOL
        llm_response = None
        tool_results = [
            ToolResult(call_id="c1", name="bash", success=True, result="ok"),
            ToolResult(call_id="c2", name="bash", success=False, error="failed", result=None),
        ]

    console.update_status(_S(), _execution(AgentState.RUNNING, final=None))
    console.update_status(_S(), _execution(AgentState.RUNNING, final=None))

    events = _drain(q)
    assert events == [
        {"type": "step", "n": 1},
        {
            "type": "tool_result",
            "id": "c1",
            "name": "bash",
            "output": "ok",
            "success": True,
        },
        {
            "type": "tool_result",
            "id": "c2",
            "name": "bash",
            "output": "",
            "success": False,
        },
    ]


async def test_final_once_and_error_priority():
    q: asyncio.Queue = asyncio.Queue()
    console = WebConsole(q)

    execution = _execution(AgentState.COMPLETED, final="结果文本", success=True)
    console.update_status(None, execution)
    console.update_status(None, execution)  # 重复 → 不再发

    events = _drain(q)
    assert events == [{"type": "final", "text": "结果文本", "success": True}]

    # step 级 ERROR
    step = AgentStep(step_number=2, state=AgentStepState.ERROR, error="工具炸了")
    console.update_status(step, _execution(AgentState.ERROR, final=None))
    console.update_status(step, _execution(AgentState.ERROR, final=None))
    events = _drain(q)
    assert events == [
        {"type": "step", "n": 2},
        {"type": "error", "message": "工具炸了"},
    ]


async def test_queue_full_drops_oldest():
    q: asyncio.Queue = asyncio.Queue(maxsize=3)
    console = WebConsole(q, queue_limit=3)
    for i in range(10):
        console._emit({"type": "assistant", "text": str(i)})
    events = _drain(q)
    # 不崩、不阻塞；最多 3 条
    assert len(events) <= 3
    assert [e["text"] for e in events] == ["7", "8", "9"]
