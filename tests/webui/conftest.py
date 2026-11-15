# SPDX-License-Identifier: MIT

"""conftest.py — webui 测试共享 fixture：临时 workspace、测试配置、client 工厂。

- chat 测试绝不调用真实模型：通过把 state.agent_factory 替换为假 Agent 工厂
  （FakeAgent 逐步塞 queue 事件后返回假 execution）实现。
- 测试配置用内嵌 YAML（api_key 为占位）写入 tmp_path。
- webui 包不在 pyproject 的 wheel 打包列表里，测试需要把项目根加进 sys.path
  （rootdir = repo 根，trae_agent 以 editable 安装、webui 以路径注入）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from webui.app import create_app  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"
#: 最小可用配置：两个同协议 provider（不同 base_url，验证 provider_key 反查歧义消解）
# + 一个未挂 model 的 provider（验证顶层 providers 能露出无 model 的行）。
TEST_CONFIG = """
model_providers:
  fake:
    api_key: test-placeholder-key
    provider: openai_compatible
    base_url: https://fake.example.com/v1
  fake2:
    api_key: test-placeholder-key-2
    provider: openai_compatible
    base_url: https://fake2.example.com/v1
  spare:
    api_key: ""
    provider: ollama
    base_url: http://127.0.0.1:11434

models:
  model-a:
    model: fake-model-a
    model_provider: fake
    max_tokens: 512
    temperature: 0.3
    top_p: 1
    top_k: 0
    parallel_tool_calls: true
    max_retries: 1
  model-b:
    model: fake-model-b
    model_provider: fake
    max_tokens: 512
    temperature: 0.3
    top_p: 1
    top_k: 0
    parallel_tool_calls: true
    max_retries: 1
  model-c:
    model: fake-model-c
    model_provider: fake2
    max_tokens: 512
    temperature: 0.3
    top_p: 1
    top_k: 0
    parallel_tool_calls: true
    max_retries: 1

agents:
  trae_agent:
    model: model-a
    max_steps: 4
    enable_lakeview: false
    tools:
      - bash
      - analyze_schematic
      - search_skills
      - load_skill
      - task_done
"""


async def _client_fixture(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# pytest fixture 注册（pytest.app fixture 语法不受约束，这里用显义写法）
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture
def test_config_file(tmp_path: Path) -> Path:
    """写入临时目录的测试配置文件。"""
    cfg = tmp_path / "trae_config.yaml"
    cfg.write_text(TEST_CONFIG, encoding="utf-8")
    return cfg


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """临时 workspace（已创建）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def app(test_config_file: Path, workspace_dir: Path):
    """测试用 Starlette 应用（临时配置 + 临时 workspace）。"""
    return create_app(config_file=str(test_config_file), workspace=workspace_dir)


@pytest.fixture
def state(app):
    """应用内 WebUIState（测试中可替换 agent_factory）。"""
    return app.state.webui


@pytest.fixture
async def client(app):
    """httpx AsyncClient（ASGITransport；无真实网络）。"""
    async for c in _client_fixture(app):
        yield c


# ---------------------------------------------------------------------------
# 假 Agent（chat SSE 测试用；绝不调真实模型）
# ---------------------------------------------------------------------------


class FakeStep:
    """AgentStep 的轻量替身（duck typing：字段与 WebConsole 消费的一致）。"""

    def __init__(self, step_number: int) -> None:
        from trae_agent.agent.agent_basics import AgentStepState

        self.step_number = step_number
        self.state: object = AgentStepState.THINKING
        self.llm_response: object = None
        self.tool_calls: list | None = None
        self.tool_results: list | None = None
        self.error: str | None = None


class FakeAgent:
    """假的 trae Agent：execute_task 按真实 base_agent 时序同步驱动 console。

    覆盖 THINKING → llm_response（assistant + tool_use）→ tool_results →
    step2 → COMPLETED(final) 的完整事件链，最后补发一个「尾部 flush」事件。
    """

    def __init__(self, console, model_key: str | None, run=None) -> None:
        self.console = console
        self.model_key = model_key
        self.run = run
        self.new_task_calls: list[dict] = []

    def new_task(self, task: str, extra_args: dict | None = None, tool_names=None) -> None:
        self.new_task_calls.append({"task": task, "extra_args": extra_args or {}})

    async def run(self, task: str, extra_args: dict | None = None, tool_names=None):
        """与真实 Agent 包装类同名接口：new_task + execute_task。"""
        self.new_task(task, extra_args, tool_names)
        return await self.execute_task()

    async def execute_task(self):
        from trae_agent.agent.agent_basics import AgentExecution, AgentState
        from trae_agent.tools.base import ToolCall, ToolResult
        from trae_agent.utils.llm_clients.llm_basics import LLMResponse

        queue = self.run.queue if self.run is not None else None
        execution = AgentExecution(task="fake", steps=[])
        execution.agent_state = AgentState.RUNNING

        # ---- step 1：THINKING → llm_response → tool_call → tool_result ----
        step1 = FakeStep(step_number=1)
        self.console.update_status(step1, execution)  # → {type: step, n:1}

        step1.llm_response = LLMResponse(content="我先分析一下硬件……", tool_calls=None)
        self.console.update_status(step1, execution)  # → assistant

        step1.llm_response = LLMResponse(
            content="我先分析一下硬件……",
            tool_calls=[
                ToolCall(
                    name="analyze_schematic", call_id="call_1", arguments={"path": "blink.net"}
                )
            ],
        )
        self.console.update_status(step1, execution)  # → tool_use（不重复 assistant）

        step1.tool_results = [
            ToolResult(
                call_id="call_1", name="analyze_schematic", success=True, result="MCU: STM32F103"
            )
        ]
        self.console.update_status(step1, execution)  # → tool_result

        # ---- step 2：完成 ----
        step2 = FakeStep(step_number=2)
        self.console.update_status(step2, execution)  # → {type: step, n:2}
        step2.llm_response = LLMResponse(content="完成了", tool_calls=None)
        self.console.update_status(step2, execution)  # → assistant

        execution.agent_state = AgentState.COMPLETED
        execution.final_result = "假任务完成"
        execution.success = True
        self.console.update_status(step2, execution)  # → final

        # 模拟真实时序：execute_task 返回前后 console 队列仍有尾部事件
        if queue is not None:
            queue.put_nowait({"type": "assistant", "text": "尾部补发文本"})

        return execution


class _RunShim:
    """给 FakeAgent 提供 queue 属性的最小壳。"""

    def __init__(self, queue) -> None:
        self.queue = queue


def make_fake_agent_factory(events_script: list[dict] | None = None):
    """生成假 Agent 工厂；events_script 指定自定义事件序列（None 走完整链）。"""

    def factory(st, console, model_key: str | None):
        return _ScriptedAgent(console, model_key, events_script)

    return factory


class _ScriptedAgent:
    """按脚本执行的假 Agent（脚本为空串表/None 时用默认完整链）。"""

    def __init__(self, console, model_key: str | None, events_script: list[dict] | None) -> None:
        self.console = console
        self.model_key = model_key
        self.script = events_script
        self.new_task_args: dict | None = None

    def new_task(self, task: str, extra_args: dict | None = None, tool_names=None) -> None:
        self.new_task_args = {"task": task, "extra_args": extra_args or {}}

    async def run(self, task: str, extra_args: dict | None = None, tool_names=None):
        """与真实 Agent 包装类同名接口：new_task + execute_task。"""
        self.new_task(task, extra_args, tool_names)
        return await self.execute_task()

    async def execute_task(self):
        from trae_agent.agent.agent_basics import AgentExecution, AgentState

        if self.script is not None:
            for event in self.script:
                self.console._queue.put_nowait(event)  # noqa: SLF001 - 测试直塞 queue
            last = self.script[-1] if self.script else {}
            if last.get("type") == "error":
                execution = AgentExecution(
                    task="x", steps=[], final_result=str(last.get("message", "")), success=False
                )
                execution.agent_state = AgentState.ERROR
                return execution
            execution = AgentExecution(
                task="x", steps=[], final_result=str(last.get("text", "")), success=True
            )
            execution.agent_state = AgentState.COMPLETED
            return execution

        agent = FakeAgent(self.console, self.model_key, run=_RunShim(self.console._queue))  # noqa: SLF001
        return await agent.execute_task()
