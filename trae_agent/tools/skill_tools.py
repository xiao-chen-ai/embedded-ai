# SPDX-License-Identifier: MIT

"""skill_tools.py — trae-agent 的三个技能工具（JS 版 core/agent/tools.js 的移植）。

- SearchSkillsTool  (search_skills)  : 自然语言检索技能库（中文/英文），返回前 5
- LoadSkillTool     (load_skill)     : 加载 SKILL.md 全文 / 技能附属文件（含越界防护）
- RunSkillScriptTool(run_skill_script): 受控运行技能 scripts/ 下的 Python 脚本

差异说明（相对 JS 版，按任务规格有意为之）：
- Python 解释器直接用 sys.executable（当前 venv），不做探测链——更可靠
- 脚本输出含 ImportError 时在尾部追加 uv pip install 提示
- 技能目录缺 config.json 而 config.example.json 存在时自动复制一份（首次运行 bootstrap）
"""

import asyncio
import contextlib
import shutil
import sys
from pathlib import Path
from typing import override

from trae_agent.skills_engine.loader import Skill, scan_skills
from trae_agent.skills_engine.matcher import match_skills
from trae_agent.skills_engine.skill_paths import SKILLS_ROOT
from trae_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter

# 与 JS 版一致的限额
SKILL_FILE_LIMIT = 100 * 1024  # load_skill 附属文件上限（字节）
OUTPUT_LIMIT = 16 * 1024  # run_skill_script 输出截断（字节，按 JS 版字符语义近似为字符数）
SCRIPT_TIMEOUT_S = 120.0  # 脚本超时

# 自动补装提示的常见技能依赖（ImportError 时提示 uv pip install）
_IMPORT_HINT = "可运行: uv pip install pyserial"


def _fail(output: str) -> ToolExecResult:
    """JS 版 fail() 的等价物：error_code=-1 + error 文本。"""
    return ToolExecResult(error=str(output), error_code=-1)


def _ok(output: str) -> ToolExecResult:
    return ToolExecResult(output=str(output))


def _load_all_skills(skills_root: Path = SKILLS_ROOT) -> list[Skill]:
    """扫描技能根（本机路径，默认 SKILLS_ROOT），返回技能列表。"""
    skills, _warnings = scan_skills([skills_root])
    return skills


def _find_skill(query: object, skills_root: Path = SKILLS_ROOT) -> Skill | None:
    """按 id 或名称模糊查找技能（JS 版 tools.js findSkill 语义）。

    exact id > exact name > 后缀匹配 > 名称互含（大小写不敏感）。
    """
    q = str(query if query is not None else "").strip()
    if not q:
        return None
    skills = _load_all_skills(skills_root)
    if not skills:
        return None
    for s in skills:
        if s.id == q:
            return s
    for s in skills:
        if s.name == q:
            return s
    for s in skills:
        if q.endswith(f"/{s.name}") or s.id.endswith(f"/{q}"):
            return s
    low = q.lower()
    for s in skills:
        n = s.name.lower()
        if low in n or n in low:
            return s
    return None


def _slice_output(s: str, limit: int = OUTPUT_LIMIT) -> tuple[str, bool]:
    if len(s) <= limit:
        return s, False
    return s[:limit], True


class SearchSkillsTool(Tool):
    """search_skills — 技能库自然语言检索。"""

    def __init__(self, model_provider: str | None = None, skills_root: Path | str | None = None):
        super().__init__(model_provider)
        self._skills_root = Path(skills_root) if skills_root is not None else SKILLS_ROOT

    @override
    def get_name(self) -> str:
        return "search_skills"

    @override
    def get_description(self) -> str:
        return (
            "Search the installed skill library with a natural-language query (Chinese or English), "
            'e.g. "调试串口" or "build firmware". Returns the top matches with a match reason '
            "(score included); then call load_skill on the best one to read its full instructions."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description='Natural-language search query, e.g. "keil", "serial", "调试串口". '
                "Empty query lists the first 10 skills.",
                required=True,
            )
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        try:
            query = arguments.get("query")
            skills = _load_all_skills(self._skills_root)
            q = str(query if query is not None else "").strip()
            if not skills:
                return _ok(
                    f"技能库为空（skills_root={self._skills_root} 不存在或没有技能）。"
                    "可从 _upstream/ 导入技能后再试。"
                )
            if not q:
                # 空 query：返回前 10 个技能清单
                lines = [f"{i + 1}. {s.id} — {s.name}" for i, s in enumerate(skills[:10])]
                lines.append("用 load_skill 加载全文")
                return _ok("\n".join(lines))
            results = match_skills(q, skills, limit=5)
            if not results:
                return _ok("没有找到匹配的技能")
            lines = [
                f"{i + 1}. {s.id} ({s.name}) — {why} [score {score}]"
                for i, (s, score, why) in enumerate(results)
            ]
            lines.append(
                "用 load_skill 加载全文（如 load_skill name=serial），按 SKILL.md 指南行动"
            )
            return _ok("\n".join(lines))
        except Exception as err:  # execute 永不抛（JS 契约）
            return _fail(f"检索失败: {err}")


class LoadSkillTool(Tool):
    """load_skill — 加载技能 SKILL.md 全文或技能目录内的附属文件。"""

    def __init__(self, model_provider: str | None = None, skills_root: Path | str | None = None):
        super().__init__(model_provider)
        self._skills_root = Path(skills_root) if skills_root is not None else SKILLS_ROOT

    @override
    def get_name(self) -> str:
        return "load_skill"

    @override
    def get_description(self) -> str:
        return (
            "Load a skill. Without `file`, returns the full SKILL.md body plus a listing of its "
            "scripts/references/templates files. With `file`, returns that side file content "
            "(max 100KB, no .git access, no path traversal). Always load a skill before executing it."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="name",
                type="string",
                description='Skill name or id, e.g. "tdd", "mp/tdd", "serial". Fuzzy matched.',
                required=True,
            ),
            ToolParameter(
                name="file",
                type="string",
                description=(
                    "Optional side-file path relative to the skill directory, "
                    'e.g. "scripts/gcc_build.py".'
                ),
            ),
        ]

    @staticmethod
    def _list_side_files(skill_dir: Path) -> list[str]:
        """递归列出附属文件（posix 风格相对路径，排序），失败时返回空。"""
        out: list[str] = []

        def walk(d: Path, prefix: str) -> None:
            try:
                entries = sorted(d.iterdir(), key=lambda p: p.name)
            except OSError:
                return
            for e in entries:
                rel = f"{prefix}/{e.name}" if prefix else e.name
                if e.is_dir():
                    walk(e, rel)
                else:
                    out.append(rel)  # 文件才收录（目录本身不列）

        walk(skill_dir, "")
        return out

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        try:
            name_arg = arguments.get("name")
            if name_arg is None or str(name_arg).strip() == "":
                return _fail("缺少参数 name")
            skill = _find_skill(name_arg, self._skills_root)
            if skill is None:
                return _fail(f"未找到技能: {name_arg}")
            skill_dir = Path(skill.dir)

            file_arg = arguments.get("file")
            if file_arg is not None:
                rel = str(file_arg).replace("\\", "/")
                if ".." in rel or rel.startswith("/"):
                    return _fail("路径越界")
                if rel == "" or ".git" in rel.split("/"):
                    return _fail("不允许访问 .git")
                target = (skill_dir / rel).resolve()
                # 双保险：解析后的绝对路径必须仍在技能目录内
                if skill_dir.resolve() not in target.parents and target != skill_dir.resolve():
                    return _fail(f"路径越界: {rel}")
                try:
                    if not target.is_file():
                        return _fail(f"读取技能文件失败: {skill.id}/{rel} (不是文件或不存在)")
                    data = target.read_bytes()
                except OSError as err:
                    return _fail(f"读取技能文件失败: {skill.id}/{rel} ({err})")
                if len(data) > SKILL_FILE_LIMIT:
                    return _fail(f"文件过大: {rel} (上限 {SKILL_FILE_LIMIT} 字节)")
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                return _ok(f"# {skill.id}/{rel}\n\n{text}")

            # 无 file：SKILL.md 正文 + 附属文件清单
            try:
                body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            except OSError as err:
                return _fail(f"读取 SKILL.md 失败: {err}")
            # JS 版先取 SKILL.md 的 body（含 frontmatter 原文），listing 排除之
            side_files = [
                f
                for f in self._list_side_files(skill_dir)
                if not f.endswith("SKILL.md") and ".git" not in f.split("/")
            ]
            listing_text = (
                "\n\n## 附属文件\n" + "\n".join(f"- {f}" for f in side_files) if side_files else ""
            )
            full = f"# 技能 {skill.id} ({skill.name})\n\n{body[:SKILL_FILE_LIMIT]}{listing_text}"
            return _ok(full)
        except Exception as err:
            return _fail(f"加载技能失败: {err}")


class RunSkillScriptTool(Tool):
    """run_skill_script — 受控执行技能 scripts/ 下的 Python 脚本。"""

    def __init__(self, model_provider: str | None = None, skills_root: Path | str | None = None):
        super().__init__(model_provider)
        self._skills_root = Path(skills_root) if skills_root is not None else SKILLS_ROOT

    @override
    def get_name(self) -> str:
        return "run_skill_script"

    @override
    def get_description(self) -> str:
        return (
            "Run a Python script from a skill's scripts/ directory (e.g. serial scan, gcc build). "
            '`script` must be a bare filename like "serial_scan.py" (no path separators). '
            "Runs with the current venv Python, 120s timeout; stdout+stderr+exit code returned. "
            "The skill's config.json is auto-created from config.example.json on first run."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="skill",
                type="string",
                description='Skill name or id, e.g. "serial" or "zh/serial".',
                required=True,
            ),
            ToolParameter(
                name="script",
                type="string",
                description="Bare filename under the skill's scripts/ directory.",
                required=True,
            ),
            ToolParameter(
                name="args",
                type="array",
                description="Command-line arguments passed to the script.",
                items={"type": "string"},
            ),
        ]

    @staticmethod
    def _bootstrap_config(skill_dir: Path) -> None:
        """技能目录缺 config.json 而 config.example.json 存在时自动复制（首次运行 bootstrap）。"""
        cfg = skill_dir / "config.json"
        example = skill_dir / "config.example.json"
        if not cfg.exists() and example.is_file():
            with contextlib.suppress(OSError):
                shutil.copyfile(example, cfg)  # bootstrap 失败不阻塞脚本执行

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        try:
            skill_arg = arguments.get("skill")
            if skill_arg is None or str(skill_arg).strip() == "":
                return _fail("缺少参数 skill")
            skill = _find_skill(skill_arg, self._skills_root)
            if skill is None:
                return _fail(f"未找到技能: {skill_arg}")
            script = str(arguments.get("script") if arguments.get("script") is not None else "")
            if not script:
                return _fail("缺少参数 script")
            if "/" in script or "\\" in script or script in (".", ".."):
                return _fail(f"script 必须是 scripts/ 下的纯文件名，不允许包含路径分隔符: {script}")
            skill_dir = Path(skill.dir)
            script_abs = (skill_dir / "scripts" / script).resolve()
            # 双保险：脚本必须位于技能 scripts/ 目录内
            scripts_dir = (skill_dir / "scripts").resolve()
            if scripts_dir != script_abs and scripts_dir not in script_abs.parents:
                return _fail(f"非法脚本路径: {script}")
            if not script_abs.is_file():
                return _fail(f"脚本不存在: {skill.id}/scripts/{script}")

            args_arg = arguments.get("args")
            if args_arg is not None and not isinstance(args_arg, list):
                return _fail("args 必须是字符串数组")
            argv: list[str] = []
            if isinstance(args_arg, list):
                for a in args_arg:
                    if not isinstance(a, (str, int, float)) or isinstance(a, bool):
                        return _fail("args 数组元素必须是字符串")
                    argv.append(str(a))

            self._bootstrap_config(skill_dir)

            # 用当前 venv 的 python 直接跑（比 JS 探测链更可靠）
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(script_abs),
                    *argv,
                    cwd=str(skill_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as err:
                return _fail(f"启动 Python 失败: {err}")

            timed_out = False
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=SCRIPT_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                # JS 版语义：超时杀死进程后仍按普通结果返回（输出 + exitCode）
                timed_out = True
                proc.kill()
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                except Exception:
                    stdout_b, stderr_b = b"", b""

            code = proc.returncode if proc.returncode is not None else -1
            text = (stdout_b or b"").decode("utf-8", errors="replace") + (stderr_b or b"").decode(
                "utf-8", errors="replace"
            )
            sliced, truncated = _slice_output(text)
            tail = "\n... (输出已截断)" if truncated else ""
            if timed_out:
                tail += f"\n(脚本超过 {int(SCRIPT_TIMEOUT_S)}s 已被终止)"
            # ImportError 时补 uv pip install 提示（pyserial 等依赖不在 venv）
            if "ImportError" in text or "ModuleNotFoundError" in text:
                tail += f"\n提示: 脚本依赖缺失。{_IMPORT_HINT}"
            return _ok(f"{sliced}{tail}\nexitCode: {code}")
        except Exception as err:
            return _fail(f"运行脚本失败: {err}")
