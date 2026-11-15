# SPDX-License-Identifier: MIT

"""test_app.py — webui 服务端全端点测试（httpx ASGI 传输；chat 不调真实模型）。

覆盖：
- /api/health、/api/config（models/base_url 脱敏/configured）
- /api/config/model 切换（未知 key 400）
- /api/skills 列表、/api/skills/{id} 详情（%2F 与原始斜杠）、404
- /api/skills/search（中文「调试串口」→ zh/serial 第一）
- /api/hardware/analyze（内嵌 KiCad 网表 → MCU 识别 + analysis.json 落盘 + path 模式 + 400）
- /api/files/upload（multipart 多文件、../ 清洗、白名单外拒收、zip 解包）
- /api/files（recursive 平铺 + .git 过滤）、/api/file（200KB 截断、越界 404）
- /api/chat SSE（假 Agent 工厂：全事件链 / 脚本化事件 / 异常转 error / 参数校验）
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from tests.webui.conftest import make_fake_agent_factory

# 注意：pyproject 已设 asyncio_mode=auto（pytest-asyncio），异步测试自动按
# asyncio 运行；不使用 anyio 标记（避免 trio 后端参数化，venv 无 trio）。


# ---------------------------------------------------------------------------
# 最小 KiCad 网表（MCU + LED + 按键 + 晶振 + 去耦电容）
# ---------------------------------------------------------------------------

KICAD_NETLIST = """(export (version "E") (components
    (comp (ref "U1") (value "STM32F103C8T6"))
    (comp (ref "D1") (value "LED"))
    (comp (ref "R1") (value "330"))
    (comp (ref "SW1") (value "BUTTON"))
    (comp (ref "Y1") (value "8MHz"))
)
  (nets
    (net (code "1") (name "+3V3")
      (node (ref "U1") (pin "VDD")) (node (ref "R1") (pin "1")))
    (net (code "2") (name "GND")
      (node (ref "U1") (pin "VSS")))
    (net (code "3") (name "LED_OUT")
      (node (ref "U1") (pin "PB9")) (node (ref "R1") (pin "2")) (node (ref "D1") (pin "1")))
    (net (code "4") (name "BTN")
      (node (ref "U1") (pin "PA0")) (node (ref "SW1") (pin "1")))
    (net (code "5") (name "OSC_IN")
      (node (ref "U1") (pin "PD0")) (node (ref "Y1") (pin "1")))
  ))"""


# ---------------------------------------------------------------------------
# health / config
# ---------------------------------------------------------------------------


async def test_health(client: httpx.AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["version"]
    assert data["default_model"] == "model-a"


async def test_config_models_and_active(client: httpx.AsyncClient):
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) >= {"models", "active_model", "providers", "workspace", "tools"}
    assert data["active_model"] == "model-a"
    assert "model-b" in data["models"]
    # 顶层 providers = model_providers 全部键（含未挂 model 的 spare）
    assert data["providers"] == ["fake", "fake2", "spare"]
    ma = data["models"]["model-a"]
    # base_url 脱敏（无 userinfo/query 泄露）
    assert ma["base_url"] == "https://fake.example.com/v1"
    assert ma["provider"] == "openai_compatible"  # 协议类型（client_type）
    assert ma["provider_key"] == "fake"  # model_providers 键（PUT credentials 用）
    assert ma["model"] == "fake-model-a"
    assert ma["configured"] is True
    # 两个 model 共享同一 provider 对象 → 反查键一致
    assert data["models"]["model-b"]["provider_key"] == "fake"
    # 同协议不同键的 provider：provider_key 必须反查到各自的键（siliconflow/deepseek 场景）
    mc = data["models"]["model-c"]
    assert mc["provider"] == "openai_compatible"
    assert mc["provider_key"] == "fake2"
    assert mc["base_url"] == "https://fake2.example.com/v1"
    # tools 来自 agents.trae_agent.tools（测试配置 5 个）
    assert data["tools"] == [
        "bash",
        "analyze_schematic",
        "search_skills",
        "load_skill",
        "task_done",
    ]


async def test_config_switch_model_ok(client: httpx.AsyncClient):
    resp = await client.post("/api/config/model", json={"model_key": "model-b"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "active_model": "model-b"}

    # 切换后 active 生效
    resp = await client.get("/api/config")
    assert resp.json()["active_model"] == "model-b"
    resp = await client.get("/api/health")
    assert resp.json()["default_model"] == "model-b"


async def test_config_switch_model_unknown(client: httpx.AsyncClient):
    resp = await client.post("/api/config/model", json={"model_key": "no-such-model"})
    assert resp.status_code == 400
    assert "no-such-model" in resp.json()["error"]


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
    ],
)
async def test_config_switch_model_bad_body(client: httpx.AsyncClient, body: str):
    resp = await client.post(
        "/api/config/model", content=body, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------


async def test_skills_list(client: httpx.AsyncClient):
    resp = await client.get("/api/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == len(data["skills"]) > 0
    serial = next(s for s in data["skills"] if s["id"] == "zh/serial")
    # 中文描述原样返回
    assert "串口" in serial["description"]
    assert serial["tag"] == "zh"
    assert serial["enabled"] is True


async def test_skill_detail_encoded_and_raw(client: httpx.AsyncClient):
    for url in ("/api/skills/zh%2Fserial", "/api/skills/zh/serial"):
        resp = await client.get(url)
        assert resp.status_code == 200, url
        data = resp.json()
        assert data["id"] == "zh/serial"
        assert data["name"] == "serial"
        assert "串口" in data["description"]
        # frontmatter 解析后的 dict
        assert data["frontmatter"]["name"] == "serial"
        # SKILL.md 全文
        assert "串口调试" in data["body"]
        # 附属文件（脚本/参考）相对路径
        files = data["files"]
        assert any(f.startswith("scripts/") for f in files)
        assert all(not f.endswith("SKILL.md") for f in files)


async def test_skill_detail_not_found(client: httpx.AsyncClient):
    resp = await client.get("/api/skills/zh/no-such-skill")
    assert resp.status_code == 404


async def test_skills_search_chinese(client: httpx.AsyncClient):
    resp = await client.post("/api/skills/search", json={"query": "调试串口"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) > 0
    assert len(results) <= 5
    top = results[0]
    assert top["id"] == "zh/serial"
    assert top["score"] > 0
    assert top["why"]  # 中文理由非空


async def test_skills_search_missing_query(client: httpx.AsyncClient):
    resp = await client.post("/api/skills/search", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# hardware/analyze
# ---------------------------------------------------------------------------


async def test_hardware_analyze_content(client: httpx.AsyncClient, workspace_dir: Path):
    resp = await client.post(
        "/api/hardware/analyze", json={"content": KICAD_NETLIST, "filename": "blink.net"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "kicad"
    # MCU 识别
    mcu = data["analysis"]["mcu"]
    assert mcu is not None
    assert mcu["part"] == "STM32F103C8T6"
    assert mcu["ref"] == "U1"
    assert isinstance(data["analysis"]["issues"], list)
    assert "STM32F103C8T6" in data["summary_markdown"]
    # 落盘 analysis.json
    assert data["saved"] == "hardware/analysis.json"
    saved = json.loads((workspace_dir / "hardware" / "analysis.json").read_text(encoding="utf-8"))
    assert saved["mcu"]["part"] == "STM32F103C8T6"


async def test_hardware_analyze_path_mode(client: httpx.AsyncClient, workspace_dir: Path):
    (workspace_dir / "blink.net").write_text(KICAD_NETLIST, encoding="utf-8")
    resp = await client.post("/api/hardware/analyze", json={"path": "blink.net"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "kicad"


async def test_hardware_analyze_bad_content(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/hardware/analyze", json={"content": "完全不是网表的内容 hello world"}
    )
    assert resp.status_code == 400
    assert "网表" in resp.json()["error"] or "格式" in resp.json()["error"]


async def test_hardware_analyze_path_missing(client: httpx.AsyncClient):
    resp = await client.post("/api/hardware/analyze", json={"path": "no-such.net"})
    assert resp.status_code == 400


async def test_hardware_analyze_no_input(client: httpx.AsyncClient):
    resp = await client.post("/api/hardware/analyze", json={})
    assert resp.status_code == 400


async def test_hardware_analyze_path_traversal(client: httpx.AsyncClient, tmp_path: Path):
    resp = await client.post("/api/hardware/analyze", json={"path": "../outside.net"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# files 上传 / 列表 / 读回
# ---------------------------------------------------------------------------


async def test_upload_and_list_files(client: httpx.AsyncClient, workspace_dir: Path):
    # 预置 hardware/analysis.json（模拟此前硬件分析的落盘产物）
    (workspace_dir / "hardware").mkdir()
    (workspace_dir / "hardware" / "analysis.json").write_text("{}", encoding="utf-8")

    # 两个合法文件 + 一个拒收类型
    files = [
        ("files", ("main.c", b"int main(void){return 0;}", "text/plain")),
        ("files", ("blink.net", KICAD_NETLIST.encode("utf-8"), "text/plain")),
        ("files", ("evil.exe", b"MZ...", "application/octet-stream")),
    ]
    resp = await client.post("/api/files/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert {f["name"] for f in data["files"]} == {"main.c", "blink.net"}
    assert len(data["rejected"]) == 1
    assert data["rejected"][0]["name"] == "evil.exe"
    # 磁盘落位
    assert (workspace_dir / "main.c").read_text(encoding="utf-8").startswith("int main")
    assert (workspace_dir / "blink.net").is_file()

    # 列表（单层）
    resp = await client.get("/api/files")
    data = resp.json()
    names = {f["name"] for f in data["files"]}
    assert {"main.c", "blink.net", "hardware"} <= names
    hardware_dir = next(f for f in data["files"] if f["name"] == "hardware")
    assert hardware_dir["dir"] is True
    assert hardware_dir["size"] is None

    # 递归平铺
    resp = await client.get("/api/files?recursive=true")
    data = resp.json()
    paths = {f["path"] for f in data["files"]}
    assert "main.c" in paths
    assert "hardware/analysis.json" in paths


async def test_upload_subdir_and_traversal_cleanse(client: httpx.AsyncClient, workspace_dir: Path):
    # subdir 正常写入
    resp = await client.post(
        "/api/files/upload",
        files=[("files", ("boot.s", b"startup code", "text/plain"))],
        data={"subdir": "src"},
    )
    assert resp.status_code == 200
    assert resp.json()["files"][0]["path"] == "src/boot.s"

    # 危险文件名被清洗/拒收：路径分隔符拒收
    resp = await client.post(
        "/api/files/upload",
        files=[("files", ("../escape.c", b"x", "text/plain"))],
    )
    data = resp.json()
    assert data["total"] == 0
    assert data["rejected"][0]["reason"]
    # workspace 外无文件
    assert not (workspace_dir.parent / "escape.c").exists()

    # 危险 subdir 拒收
    resp = await client.post(
        "/api/files/upload",
        files=[("files", ("a.c", b"x", "text/plain"))],
        data={"subdir": "../../etc"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_upload_zip_unpacked(client: httpx.AsyncClient, workspace_dir: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("board/board.h", "#define BOARD_LED_PORT B\n")
        zf.writestr("board/pins.csv", "ref,pin\nU1,PA0\n")
    buf.seek(0)
    resp = await client.post(
        "/api/files/upload",
        files=[("files", ("blinkpack.zip", buf.read(), "application/zip"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "blinkpack" in data["unzipped"]
    # 解包文件落位（同名目录）
    assert (workspace_dir / "blinkpack" / "board" / "board.h").is_file()
    # zip 本体也保存
    assert (workspace_dir / "blinkpack.zip").is_file()


async def test_get_file_and_truncation(client: httpx.AsyncClient, workspace_dir: Path):
    (workspace_dir / "big.txt").write_bytes(b"A" * (250 * 1024))
    (workspace_dir / "small.md").write_bytes("# 标题\n正文".encode("utf-8"))

    resp = await client.get("/api/file?path=small.md")
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "# 标题\n正文"
    assert data["truncated"] is False

    resp = await client.get("/api/file?path=big.txt")
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is True
    assert len(data["content"]) <= 200 * 1024


async def test_get_file_traversal_and_missing(
    client: httpx.AsyncClient, workspace_dir: Path, tmp_path: Path
):
    resp = await client.get("/api/file?path=../secret.txt")
    assert resp.status_code in (400, 404)

    resp = await client.get("/api/file?path=no-such.txt")
    assert resp.status_code == 404


async def test_files_listing_filters_git(client: httpx.AsyncClient, workspace_dir: Path):
    (workspace_dir / ".git").mkdir()
    (workspace_dir / ".git" / "config").write_text("x", encoding="utf-8")
    (workspace_dir / "src").mkdir()
    (workspace_dir / "src" / "__pycache__").mkdir()
    (workspace_dir / "src" / "__pycache__" / "m.pyc").write_bytes(b"x")
    (workspace_dir / "src" / "app.py").write_text("print()", encoding="utf-8")

    resp = await client.get("/api/files?path=src&recursive=true")
    data = resp.json()
    paths = {f["path"] for f in data["files"]}
    assert "src/app.py" in paths
    assert not any(".git" in p or "__pycache__" in p for p in paths)

    # 不存在的子目录 → 空列表（不 500）
    resp = await client.get("/api/files?path=no-such-dir")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# chat SSE
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
    """解析 SSE 文本 → data 事件列表（忽略 :hb 注释行）。"""
    events: list[dict] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


async def test_chat_sse_full_event_chain(client: httpx.AsyncClient, state):
    """假 Agent 全链事件：step → assistant → tool_use → tool_result → final + 尾部 flush。"""
    state.agent_factory = make_fake_agent_factory()  # 默认脚本（FakeAgent 完整链）

    resp = await client.post("/api/chat", json={"message": "分析 blink.net 并生成 board.h"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)

    types = [e["type"] for e in events]
    # 完整事件链（step 出现 2 次；final 是终止事件）
    assert types[0] == "step" and events[0]["n"] == 1
    assert "assistant" in types
    assert "tool_use" in types
    assert "tool_result" in types
    assert "final" in types
    assert types[-1] in ("final", "assistant")  # final 后还有 flush 的尾部事件
    terminal_idx = max(i for i, t in enumerate(types) if t == "final")
    assert terminal_idx < len(types) - 1 or types[-1] == "final"

    # tool_use 字段契约
    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["id"] == "call_1"
    assert tool_use["name"] == "analyze_schematic"
    assert tool_use["args"] == {"path": "blink.net"}

    # tool_result 字段契约
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["id"] == "call_1"
    assert tool_result["success"] is True
    assert "STM32F103" in tool_result["output"]

    # final 契约
    final = next(e for e in events if e["type"] == "final")
    assert final["text"] == "假任务完成"
    assert final["success"] is True

    # 去重：assistant 只发一次（llm_response 被多次 update_status 观察到）
    assistant_texts = [e["text"] for e in events if e["type"] == "assistant"]
    assert assistant_texts.count("我先分析一下硬件……") == 1
    # 尾部 flush 的补发事件在 final 之后仍能收到
    assert "尾部补发文本" in assistant_texts

    # step 事件按号递增且不重复
    step_ns = [e["n"] for e in events if e["type"] == "step"]
    assert step_ns == sorted(set(step_ns))


async def test_chat_sse_error_event(client: httpx.AsyncClient, state):
    """脚本化事件：error 终止事件。"""
    state.agent_factory = make_fake_agent_factory(
        [{"type": "step", "n": 1}, {"type": "error", "message": "模型连接失败"}]
    )
    resp = await client.post("/api/chat", json={"message": "任务"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert types == ["step", "error"]
    assert events[-1]["message"] == "模型连接失败"


async def test_chat_sse_agent_exception_to_error(client: httpx.AsyncClient, state):
    """Agent execute_task 抛异常 → 转为 error 事件（不崩流）。"""

    class ExplodingAgent:
        def new_task(self, task, extra_args=None, tool_names=None):
            pass

        async def run(self, task, extra_args=None, tool_names=None):
            self.new_task(task, extra_args, tool_names)
            return await self.execute_task()

        async def execute_task(self):
            raise RuntimeError("内部炸了")

    state.agent_factory = lambda st, console, model_key: ExplodingAgent()
    resp = await client.post("/api/chat", json={"message": "任务"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert "内部炸了" in events[-1]["message"]


async def test_chat_sse_new_task_receives_working_dir(
    client: httpx.AsyncClient, state, workspace_dir: Path
):
    """new_task 收到 project_path=working_dir 绝对路径与 issue=消息。"""
    captured: dict = {}

    class CapturingAgent:
        def __init__(self, console, model_key):
            self.console = console
            self.model_key = model_key

        def new_task(self, task, extra_args=None, tool_names=None):
            captured["task"] = task
            captured["extra_args"] = extra_args

        async def run(self, task, extra_args=None, tool_names=None):
            self.new_task(task, extra_args, tool_names)
            return await self.execute_task()

        async def execute_task(self):
            self.console.update_status(None, _completed_execution("完成"))
            return _completed_execution("完成")

    def _completed_execution(text: str):
        from trae_agent.agent.agent_basics import AgentExecution, AgentState

        execution = AgentExecution(task="x", steps=[], final_result=text, success=True)
        execution.agent_state = AgentState.COMPLETED
        return execution

    state.agent_factory = lambda st, console, model_key: CapturingAgent(console, model_key)
    resp = await client.post(
        "/api/chat", json={"message": "帮我分析", "working_dir": str(workspace_dir)}
    )
    assert resp.status_code == 200
    assert captured["task"] == "帮我分析"
    assert captured["extra_args"]["project_path"] == str(workspace_dir.resolve())
    assert captured["extra_args"]["issue"] == "帮我分析"
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "final"


async def test_chat_sse_validation_errors(client: httpx.AsyncClient):
    # 非 JSON
    resp = await client.post(
        "/api/chat", content="oops", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    # 缺 message
    resp = await client.post("/api/chat", json={})
    assert resp.status_code == 400
    # 未知 model_key
    resp = await client.post("/api/chat", json={"message": "hi", "model_key": "no-such-model"})
    assert resp.status_code == 400
    # 非法 working_dir
    resp = await client.post("/api/chat", json={"message": "hi", "working_dir": "../../etc"})
    assert resp.status_code == 400


async def test_chat_sse_model_key_passed_to_factory(
    client: httpx.AsyncClient, state, workspace_dir: Path
):
    """请求里的 model_key 原样传给 Agent 工厂。"""
    seen_keys: list = []
    from tests.webui.conftest import make_fake_agent_factory as mk

    def factory(st, console, model_key):
        seen_keys.append(model_key)
        return mk()(st, console, model_key)

    state.agent_factory = factory
    resp = await client.post("/api/chat", json={"message": "hi", "model_key": "model-b"})
    assert resp.status_code == 200
    assert seen_keys == ["model-b"]


async def test_chat_sse_default_working_dir_is_workspace(
    client: httpx.AsyncClient, state, workspace_dir: Path
):
    """不传 working_dir 默认 workspace。"""
    captured: dict = {}

    class NoopAgent:
        def __init__(self, console):
            self.console = console

        def new_task(self, task, extra_args=None, tool_names=None):
            captured["extra_args"] = extra_args

        async def run(self, task, extra_args=None, tool_names=None):
            self.new_task(task, extra_args, tool_names)
            return await self.execute_task()

        async def execute_task(self):
            return _completed_execution("done")

    def _completed_execution(text):
        from trae_agent.agent.agent_basics import AgentExecution, AgentState

        execution = AgentExecution(task="x", steps=[], final_result=text, success=True)
        execution.agent_state = AgentState.COMPLETED
        return execution

    state.agent_factory = lambda st, console, model_key: NoopAgent(console)
    resp = await client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    assert captured["extra_args"]["project_path"] == str(workspace_dir.resolve())
    assert Path(captured["extra_args"]["project_path"]).is_dir()


# ---------------------------------------------------------------------------
# 并发 chat
# ---------------------------------------------------------------------------


async def test_chat_sse_concurrent(client: httpx.AsyncClient, state):
    """多个并发 chat：每请求独立 Agent + queue，事件不串流。"""
    state.agent_factory = lambda st, console, model_key: make_fake_agent_factory(None)(
        st, console, model_key
    )

    async def one(i: int) -> str:
        resp = await client.post("/api/chat", json={"message": f"任务 {i}"})
        return resp.text

    texts = await asyncio.gather(*[one(i) for i in range(4)], return_exceptions=False)
    for t in texts:
        events = _parse_sse(t)
        final = next(e for e in events if e["type"] == "final")
        assert final["text"] == "假任务完成"
    assert len(texts) == 4
