# SPDX-License-Identifier: MIT

"""app.py — Web 服务端核心：环境（WebUIState）+ Agent 工厂 + 工具函数。

路由表在本文件底部 create_app()（服务端入口，见 server.py 的 re-export）；
上半部分是与 ASGI 框架弱相关的可测试逻辑：
- WebUIState：配置加载/切换、workspace 定位、技能扫描缓存。
- Agent 工厂（可在测试中 monkeypatch state.agent_factory 为假 Agent）。
- 路径清洗、上下文相对路径解析、base_url 脱敏、文件清单。
- AgentRun：一次 chat 请求的执行体（queue 桥接，测试不真调模型）。

SSE 事件协议（模块级常量 SSE_EVENT_PROTOCOL，供前端对照）：
    {"type":"step","n":<int>}
    {"type":"assistant","text":"<str>"}
    {"type":"tool_use","id":"<call_id>","name":"<str>","args":{...}}
    {"type":"tool_result","id":"<call_id>","name":"<str>","output":"<str>","success":<bool>}
    {"type":"final","text":"<str>","success":<bool>}   # 终止事件
    {"type":"error","message":"<str>"}                   # 终止事件
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from trae_agent.utils.config import Config, ConfigError

if TYPE_CHECKING:  # 仅类型标注：Starlette 为运行时可选依赖（create_app 内延迟 import）
    from starlette.applications import Starlette

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 上传扩展名白名单（小写，含点）——嵌入式工程常见源码/数据/日志/文档类型。
#: 含 Excel(xls/xlsx)、Word(doc/docx)、PDF 等原理图导出文档，供 AI 读取引脚连接表。
UPLOAD_ALLOWED_EXTS: frozenset[str] = frozenset(
    {
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".py",
        ".uvprojx",
        ".uvoptx",
        ".s",
        ".ld",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".net",
        ".csv",
        ".cir",
        ".sp",
        ".hex",
        ".bin",
        ".map",
        ".xml",
        ".log",
        ".zip",
        ".tsv",
        ".kicad_net",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".doc",
        ".docx",
        ".pdf",
    }
)

#: 单文件读回上限（GET /api/file），超限截断。
FILE_READ_LIMIT: int = 200 * 1024

#: 文件列表忽略的目录名。
LISTING_IGNORED_DIRS: frozenset[str] = frozenset({".git", "__pycache__", ".venv", "node_modules"})

#: SSE 队列容量（事件多于容量时丢最旧）。
SSE_QUEUE_LIMIT: int = 200

#: SSE 心跳间隔（秒）。
SSE_HEARTBEAT_S: float = 15.0


# ---------------------------------------------------------------------------
# 路径清洗与定位
# ---------------------------------------------------------------------------


def clean_rel_path(raw: object) -> str | None:
    """清洗用户提供的相对路径：反斜杠归一 / 去 ../ 越界 / 禁绝锚定路径。

    Returns:
        合法相对路径（posix 风格，去除首尾斜杠）；非法（绝对路径 / 越界 / 空）返回 None。
    """
    if raw is None:
        return None
    rel = str(raw).strip()
    if not rel:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", rel):
        return None  # 绝对路径不允许
    # 按段清洗：跳过 .、拒绝 ..；循环检查清洗后的段序列
    parts: list[str] = []
    for seg in rel.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            # 只在已有段弹出后仍为非负深度时合法；否则整体非法
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    if not parts:
        return None
    joined = "/".join(parts)
    if ".." in joined.split("/"):  # 理论到不了；双保险
        return None
    return joined


def resolve_in_dir(base_dir: Path, rel: object) -> Path | None:
    """在 base_dir 内解析清洗后的相对路径，并确保不越出 base_dir。"""
    cleaned = clean_rel_path(rel)
    if cleaned is None:
        return None
    target = (base_dir / cleaned).resolve()
    base_resolved = base_dir.resolve()
    if target != base_resolved and base_resolved not in target.parents:
        return None  # 越界（如符号链接）
    return target


def sanitize_base_url(url: object) -> str | None:
    """base_url 脱敏：去掉 userinfo 与 query/fragment（含 api key 的场景零泄露）。"""
    if url is None:
        return None
    s = str(url)
    if not s:
        return None
    # query/fragment 直接剥离
    s = s.split("?", 1)[0].split("#", 1)[0]
    # scheme://user:pass@host → scheme://host
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/@\s]+@)(.+)$", s)
    if m:
        s = m.group(1) + m.group(3)
    return s


def _relative_posix(base: Path, target: Path) -> str:
    """target 相对 base 的 posix 风格相对路径。"""
    return target.resolve().relative_to(base.resolve()).as_posix()


def list_workspace_files(
    workspace: Path, rel_path: str = "", recursive: bool = False
) -> list[dict[str, Any]]:
    """列出 workspace 内（相对 rel_path 子目录）的文件清单。

    非递归返回子目录下一层条目；递归返回整棵子树平铺（目录本身也标记）。
    忽略 .git / __pycache__ / .venv / node_modules。
    """
    base = workspace if not rel_path else (workspace / rel_path)
    if not base.resolve().is_dir():
        return []
    out: list[dict[str, Any]] = []

    def add_entry(p: Path) -> None:
        try:
            is_dir = p.is_dir()
            stat = p.stat() if not is_dir else None
            size = stat.st_size if stat is not None else None
        except OSError:
            return
        out.append(
            {
                "name": p.name,
                "path": _relative_posix(workspace, p),
                "dir": is_dir,
                "size": size,
            }
        )

    def walk(d: Path) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_dir(), p.name.lower()))
        except OSError:
            return
        for e in entries:
            if e.is_dir():
                if e.name in LISTING_IGNORED_DIRS:
                    continue
                add_entry(e)
                walk(e)
            else:
                add_entry(e)

    try:
        entries = sorted(base.iterdir(), key=lambda p: (p.is_dir(), p.name.lower()))
    except OSError:
        return out
    for e in entries:
        if e.is_dir() and e.name in LISTING_IGNORED_DIRS:
            continue
        add_entry(e)
        if recursive and e.is_dir():
            walk(e)
    return out


# ---------------------------------------------------------------------------
# WebUIState：配置 / 模型切换 / workspace
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class WebUIState:
    """跨请求共享的服务端状态（配置 + workspace + 当前模型选择 + Agent 工厂）。

    切换 active model 最终落在环境变量 ``__WEBUI_ACTIVE_MODEL__`` 上（进程级，
    单进程 uvicorn 场景与 CLI 的交互切换语义一致）。
    """

    config_file: str
    workspace: Path
    app: Any = None  # 创建后回填（create_app 时）
    #: Agent 工厂注入点：None 用 default_agent_factory；测试替换为假 Agent 工厂。
    agent_factory: Any = None

    # ------------------------------------------------------------------
    # 配置加载（线程安全；active model 的切换最终生效于 _active_model_key）
    # ------------------------------------------------------------------

    def load_config(self) -> Config:
        """重新读取 Config 并应用当前 active model（进程内缺省取首个 model key）。"""
        config = Config.create(config_file=self.config_file)
        active = self.active_model_key(config=config)
        if active and config.models and active in config.models:
            config.resolve_config_values(provider=None, model=config.models[active].model)
        return config

    def default_model_key(self, config: Config) -> str | None:
        """缺省 active model：trae_agent 当前配置的 model key。"""
        if not config.models or not config.trae_agent:
            return None
        current = config.trae_agent.model
        for key, mc in config.models.items():
            if mc is current:
                return key
        return next(iter(config.models))

    def active_model_key(self, config: Config | None = None) -> str | None:
        """当前 active model key（os.environ.__WEBUI_ACTIVE_MODEL__ 或缺省）。"""
        env_key = os.environ.get("__WEBUI_ACTIVE_MODEL__", "")
        if not env_key:
            return self.default_model_key(
                config=config if config is not None else self.load_config()
            )
        return env_key

    def switch_model(self, model_key: str) -> None:
        """切换 active model（重载 Config 校验；未知 key 抛 ConfigError/K KeyError）。"""
        config = Config.create(config_file=self.config_file)
        if not config.models or model_key not in config.models:
            raise KeyError(model_key)
        os.environ["__WEBUI_ACTIVE_MODEL__"] = model_key

    def models_payload(self, config: Config | None = None) -> dict[str, Any]:
        """/api/config 的 models 子对象（base_url 脱敏 + configured + provider_key）。"""
        if config is None:
            config = self.load_config()
        # provider 键反查表：同一 ModelProvider 对象（或等值对象）→ model_providers 键。
        # Config.create 中每个 model 的 model_provider 就是 model_providers 里的实例，
        # 对象同一性稳定；resolve(provider=None) 不换对象，仅改其字符串字段。
        providers_map = config.model_providers or {}
        models: dict[str, Any] = {}
        if config.models:
            for key, mc in config.models.items():
                models[key] = self._model_info(key, mc, providers_map)
        return models

    def _model_info(self, key: str, mc: Any, providers_map: dict | None = None) -> dict[str, Any]:
        """单个 model 的脱敏信息（provider_key = model_providers 里的配置键名）。"""
        provider = str(mc.model_provider.provider)
        # 对象同一性反查 provider 键（即 trae_config.yaml 里 model_providers 的键名）；
        # 找不到时（理论上不发生）回退为 None，前端用 provider_key ?? provider 兜底。
        provider_key = None
        if providers_map:
            provider_key = next(
                (k for k, p in providers_map.items() if p is mc.model_provider), None
            )
            if provider_key is None:  # 兜底：等值匹配（防外部构造的非共享实例）
                provider_key = next(
                    (
                        k
                        for k, p in providers_map.items()
                        if str(p.provider) == provider and p.base_url == mc.model_provider.base_url
                    ),
                    None,
                )
        has_key = bool(
            mc.model_provider.api_key
            and str(mc.model_provider.api_key).strip() not in ("", "ollama", "dummy")
            and str(mc.model_provider.api_key).lower() != "none"
        )
        return {
            "model": mc.model,
            "provider": provider,
            "provider_key": provider_key,
            "base_url": sanitize_base_url(mc.model_provider.base_url),
            "configured": has_key or provider == "ollama",
            "api_key_set": has_key,
            "model_key": key,
        }

    # ------------------------------------------------------------------
    # 可视化配置编辑（保序读写 yaml：仅替换目标 provider/model 的最小字段）
    # ------------------------------------------------------------------

    def update_provider_credentials(
        self,
        provider_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """更新某个 model_providers.<name> 的 api_key/base_url（不传的字段不动）。

        直接对 yaml 文本做块级替换（保持注释与顺序），写回前用 Config.create 校验。
        """
        import yaml

        path = Path(self.config_file)
        if not path.is_file():
            raise FileNotFoundError(self.config_file)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        providers = data.get("model_providers")
        if not isinstance(providers, dict) or provider_name not in providers:
            raise KeyError(provider_name)
        if api_key is not None:
            providers[provider_name]["api_key"] = api_key
        if base_url is not None:
            providers[provider_name]["base_url"] = base_url
        # 校验 + 原子写回（先 tmp 再 replace）
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            Config.create(config_file=str(tmp))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)

    def add_model(
        self, model_key: str, model_name: str, provider_name: str, max_tokens: int | None = None
    ) -> None:
        """新增 models.<key>（provider 必须已存在于 model_providers；max_tokens 可省略）。"""
        import yaml

        path = Path(self.config_file)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        providers = data.get("model_providers") or {}
        if provider_name not in providers:
            raise KeyError(provider_name)
        models = data.setdefault("models", {})
        if model_key in models:
            raise ValueError(f"模型 key 已存在: {model_key}")
        entry: dict[str, Any] = {
            "model": model_name,
            "model_provider": provider_name,
            "temperature": 0.3,
            "top_p": 1,
            "top_k": 0,
            "parallel_tool_calls": True,
            "max_retries": 6,
        }
        if max_tokens:
            entry["max_tokens"] = max_tokens
        models[model_key] = entry
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            Config.create(config_file=str(tmp))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)

    def add_provider(
        self,
        provider_name: str,
        base_url: str,
        api_key: str = "",
        protocol: str = "openai_compatible",
        first_model: str | None = None,
    ) -> str:
        """新增 model_providers.<name>（自定义 API 端点），可选附带首个模型。

        Returns:
            创建的 models key（若带了 first_model），否则空串。
        """
        import re as _re

        path = Path(self.config_file)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        providers = data.setdefault("model_providers", {})
        if provider_name in providers:
            raise ValueError(f"provider 已存在: {provider_name}")
        if not _re.fullmatch(r"[A-Za-z0-9_-]{2,32}", provider_name):
            raise ValueError("provider 名称须为 2-32 位字母/数字/下划线/连字符")
        if not str(base_url).startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s):// 开头的地址")
        if protocol not in ("openai_compatible", "ollama", "openai", "anthropic"):
            raise ValueError(f"不支持的协议: {protocol}")
        providers[provider_name] = {
            "api_key": api_key or "",
            "provider": protocol,
            "base_url": base_url,
        }
        model_key = ""
        if first_model:
            stripped = first_model.strip()
            if stripped:
                model_key = f"{provider_name}-default"
                models = data.setdefault("models", {})
                if model_key in models:
                    model_key = f"{provider_name}-{len(models) + 1}"
                models[model_key] = {
                    "model": stripped,
                    "model_provider": provider_name,
                    "max_tokens": 8192,
                    "temperature": 0.3,
                    "top_p": 1,
                    "top_k": 0,
                    "parallel_tool_calls": True,
                    "max_retries": 6,
                }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            Config.create(config_file=str(tmp))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)
        return model_key

    # ------------------------------------------------------------------
    # UI 设置持久化（webui_settings.json：主题色/品牌/工具路径，与模型配置分离）
    # ------------------------------------------------------------------

    def _settings_file(self) -> Path:
        return Path(self.config_file).parent / "webui_settings.json"

    def load_ui_settings(self) -> dict[str, Any]:
        """读取 UI 设置；缺省给默认主题（靛蓝→紫罗兰）与品牌「小臣-AI」。"""
        defaults: dict[str, Any] = {
            "brand": "小臣-AI",
            "theme": {
                "primary": "#6366f1",
                "secondary": "#8b5cf6",
                "accent": "#2dd4bf",
            },
            "tool_paths": {},  # {"keil": r"D:\Keil_v5\UV4\UV4.exe", "jlink": ..., "openocd": ..., "gcc": ...}
        }
        f = self._settings_file()
        if not f.is_file():
            return defaults
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return defaults
        except (OSError, ValueError):
            return defaults
        # 深合并 defaults ← data（缺的字段补默认）
        merged = {**defaults, **data}
        theme = {**defaults["theme"], **(data.get("theme") or {})}
        merged["theme"] = theme
        merged["tool_paths"] = data.get("tool_paths") or {}
        return merged

    def save_ui_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """整体保存 UI 设置（先校验可 JSON 序列化，再原子写）。"""
        f = self._settings_file()
        payload = json.dumps(settings, ensure_ascii=False, indent=2)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(f)
        return settings

    @staticmethod
    def detect_tool(tool: str) -> dict[str, Any]:
        """在常见安装位置与 PATH 里探测烧录/调试/构建工具。

        支持的工具：keil(UV4) / jlink / jlink_gdb / openocd / pyocd / gcc /
        gdb / stlink(STM32CubeProgrammer) / make / ninja / atk_hswldbg。
        返回 {found: bool, path: str|null, hint, download?}。
        """
        import shutil as _sh

        def _registry_detect(exe_names: list[str]) -> str | None:  # type: ignore[misc]
            """Windows 注册表探测：卸载表 + Start Menu + 常见软件键 → 安装目录里搜 exe（深度 ≤3）。

            winreg 仅存在于 Windows：mypy 在 Linux CI 上无法解析其属性（attr-defined），
            故整个函数对 mypy 静默（平台分支代码，运行时已由 os.name 守卫）。
            """
            if os.name != "nt":
                return None
            try:
                import winreg  # type: ignore[import-not-found]
            except ImportError:
                return None
            wr: Any = winreg  # 平台模块统一经 Any 访问，避免 attr-defined 误报
            roots: list[str] = []
            # 1) 标准卸载表（全机 + 当前用户）——几乎所有安装器都会注册
            for hive, view in (
                (wr.HKEY_LOCAL_MACHINE, wr.KEY_WOW64_32KEY),
                (wr.HKEY_LOCAL_MACHINE, wr.KEY_WOW64_64KEY),
                (wr.HKEY_CURRENT_USER, 0),
            ):
                try:
                    reg_key: Any = wr.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                        0,
                        wr.KEY_READ | view,
                    )
                except OSError:
                    continue
                try:
                    for i in range(wr.QueryInfoKey(reg_key)[0]):
                        sub = wr.OpenKey(reg_key, wr.EnumKey(reg_key, i))
                        loc, _ = wr.QueryValueEx(sub, "InstallLocation")
                        if loc and loc.strip():
                            roots.append(loc.strip())
                finally:
                    wr.CloseKey(reg_key)
            # 2) 厂商专属键
            vendor_keys = [
                r"SOFTWARE\KEIL\Products\MDK",
                r"SOFTWARE\SEGGER\JLink",
                r"SOFTWARE\WOW6432Node\KEIL\Products\MDK",
            ]
            for hive in (wr.HKEY_LOCAL_MACHINE, wr.HKEY_CURRENT_USER):
                for vk in vendor_keys:
                    try:
                        loc, _ = wr.QueryValueEx(wr.OpenKey(hive, vk, 0, wr.KEY_READ), "Path")
                        if loc and loc.strip():
                            roots.append(loc.strip())
                    except OSError:
                        continue
            # 安装目录内搜 exe（限制深度 3、目录数 500，防扫描失控）
            seen = 0
            for root in roots:
                rp = Path(root)
                if not rp.is_dir():
                    continue
                for exe in exe_names:
                    # 先看根/一级 bin
                    for cand in [
                        rp / exe,
                        rp / "bin" / exe,
                        rp / "UV4" / exe,
                        rp / "ARM" / "BIN" / exe,
                    ]:
                        if cand.is_file():
                            return str(cand)
                try:
                    for sub in rp.rglob(exe_names[0]):
                        seen += 1
                        if seen > 500:
                            break
                        return str(sub)
                except OSError:
                    continue
            return None

        def _download_hint(tool_key: str) -> dict[str, str] | None:
            """未安装时的下载/安装指引（官方渠道）。"""
            hints = {
                "keil": {
                    "name": "Keil MDK",
                    "url": "https://www.keil.com/demo/eval/arm.htm",
                    "note": "安装后填授权或使用社区版 (Community Edition)",
                },
                "atk_hswldbg": {
                    "name": "ATK-HSWLDBG 上位机",
                    "url": "http://www.openedv.com",
                    "note": "正点原子论坛下载；或直接用 Keil 内置 CMSIS-DAP（本工具链已支持）",
                },
                "jlink": {
                    "name": "SEGGER J-Link 驱动",
                    "url": "https://www.segger.com/downloads/jlink/",
                    "note": "安装 J-Link Software Pack",
                },
                "jlink_gdb": {
                    "name": "SEGGER J-Link 驱动",
                    "url": "https://www.segger.com/downloads/jlink/",
                    "note": "随 J-Link 软件包一起安装",
                },
                "openocd": {
                    "name": "OpenOCD",
                    "url": "https://openocd.org/pages/getting-openocd.html",
                    "note": "解压后把 bin 目录路径填到左侧",
                },
                "pyocd": {
                    "name": "pyOCD",
                    "url": "https://pyocd.io/docs/install",
                    "note": "pip install pyocd（或 uv pip install pyocd）",
                },
                "gcc": {
                    "name": "ARM GNU 工具链",
                    "url": "https://developer.arm.com/downloads/-/gnu-rm",
                    "note": "安装 GNU Arm Embedded Toolchain",
                },
                "gdb": {
                    "name": "ARM GNU 工具链",
                    "url": "https://developer.arm.com/downloads/-/gnu-rm",
                    "note": "随 ARM GNU 工具链安装",
                },
                "stlink": {
                    "name": "STM32CubeProgrammer",
                    "url": "https://www.st.com/en/development-tools/stm32cubeprog.html",
                    "note": "ST 官网下载（含 ST-LINK 驱动）",
                },
                "make": {
                    "name": "Make (GnuWin32)",
                    "url": "https://gnuwin32.sourceforge.net/packages/make.htm",
                    "note": "或使用 xmake/cmake 替代",
                },
                "ninja": {
                    "name": "Ninja",
                    "url": "https://github.com/ninja-build/ninja/releases",
                    "note": "下载单文件 exe 放入任一 PATH 目录",
                },
            }
            return hints.get(tool_key)

        # exe 名 + 常见安装位置（盘符 C/D/E 由后面统一扩展）
        specs: dict[str, tuple[str, list[str]]] = {
            "keil": (
                "UV4.exe",
                [
                    r"Keil_v5\UV4\UV4.exe",
                    r"Keil\UV4\UV4.exe",
                    r"Program Files (x86)\Keil_v5\UV4\UV4.exe",
                ],
            ),
            "atk_hswldbg": (
                "ATK-HSWLDBG 上位机",
                [
                    r"ATK\ATK-HSWLDBG\ATK-HSWLDBG.exe",
                    r"ATK-HSWLDBG\ATK-HSWLDBG.exe",
                    r"Program Files (x86)\ATK\ATK-HSWLDBG\ATK-HSWLDBG.exe",
                    r"Program Files (x86)\ATK\ATK-HSWLDBG\ATK-HSWLDBG Server.exe",
                ],
            ),
            "jlink": (
                "JLink.exe",
                [
                    r"Program Files\SEGGER\JLink\JLink.exe",
                    r"Program Files (x86)\SEGGER\JLink\JLink.exe",
                    r"SEGGER\JLink\JLink.exe",
                ],
            ),
            "jlink_gdb": (
                "JLinkGDBServerCL.exe",
                [
                    r"Program Files\SEGGER\JLink\JLinkGDBServerCL.exe",
                    r"Program Files (x86)\SEGGER\JLink\JLinkGDBServerCL.exe",
                    r"SEGGER\JLink\JLinkGDBServerCL.exe",
                ],
            ),
            "openocd": (
                "openocd.exe",
                [
                    r"openocd\bin\openocd.exe",
                    r"OpenOCD\bin\openocd.exe",
                    r"Program Files\OpenOCD\bin\openocd.exe",
                ],
            ),
            "pyocd": ("pyocd.exe", []),  # pip 安装，仅 PATH
            "gcc": (
                "arm-none-eabi-gcc.exe",
                [
                    r"Program Files (x86)\GNU Arm Embedded Toolchain\10 2021.10\bin\arm-none-eabi-gcc.exe",
                    r"Program Files\GNU Arm Embedded Toolchain\10 2021.10\bin\arm-none-eabi-gcc.exe",
                    r"gcc-arm-none-eabi\bin\arm-none-eabi-gcc.exe",
                ],
            ),
            "gdb": (
                "arm-none-eabi-gdb.exe",
                [
                    r"Program Files (x86)\GNU Arm Embedded Toolchain\10 2021.10\bin\arm-none-eabi-gdb.exe",
                    r"Program Files\GNU Arm Embedded Toolchain\10 2021.10\bin\arm-none-eabi-gdb.exe",
                ],
            ),
            "stlink": (
                "STM32_Programmer_CLI.exe",
                [
                    r"Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
                    r"Program Files (x86)\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
                ],
            ),
            "make": ("make.exe", [r"Program Files (x86)\GnuWin32\bin\make.exe"]),
            "ninja": ("ninja.exe", []),
        }
        if tool == "atk_hswldbg":
            # ATK-HSWLDBG（正点原子高速无线调试器）= CMSIS-DAP 兼容。
            # 优先上位机 exe；没有时回退 Keil 的 CMSIS-DAP 驱动（BIN\CMSIS_AGDI.dll）。
            for c in specs["atk_hswldbg"][1]:
                for drive in "CDE":
                    pp = drive + ":\\" + c.replace("/", "\\")
                    try:
                        if Path(pp).is_file():
                            return {"found": True, "path": pp, "hint": "正点原子上位机"}
                    except OSError:
                        continue
            for drive in "CDE":
                for keil_base in (drive + ":\\Keil_v5", drive + ":\\Keil"):
                    agdi = Path(keil_base) / "ARM" / "BIN" / "CMSIS_AGDI.dll"
                    if agdi.is_file():
                        return {
                            "found": True,
                            "path": str(agdi),
                            "hint": "无需上位机：Keil 自带 CMSIS-DAP 驱动，ATK-HSWLDBG 可直接使用",
                        }
            dl_atk = _download_hint("atk_hswldbg")
            atk_result: dict[str, Any] = {
                "found": False,
                "path": None,
                "hint": "未找到上位机或 Keil CMSIS-DAP 驱动；ATK-HSWLDBG 兼容 CMSIS-DAP，装其一即可",
            }
            if dl_atk:
                atk_result["download"] = dl_atk
            return atk_result
        if tool not in specs:
            return {
                "found": False,
                "path": None,
                "hint": f"未知的工具: {tool}（支持: {'/'.join(specs)}）",
            }
        exe_name, rel_candidates = specs[tool]
        for c in rel_candidates:
            for drive in "CDE":
                p = rf"{drive}:\{c}"
                try:
                    if Path(p).is_file():
                        return {"found": True, "path": p, "hint": "在常见安装位置找到"}
                except OSError:
                    continue
        # SEGGER 版本号目录（如 JLinkARM_V458a）：扫 SEGGER 下一级子目录
        if tool in ("jlink", "jlink_gdb"):
            exe = "JLink.exe" if tool == "jlink" else "JLinkGDBServerCL.exe"
            for base in (
                Path(r"C:\Program Files (x86)\SEGGER"),
                Path(r"C:\Program Files\SEGGER"),
            ):
                try:
                    if not base.is_dir():
                        continue
                    for sub in base.iterdir():
                        if sub.is_dir():
                            cand = sub / exe
                            if cand.is_file():
                                return {
                                    "found": True,
                                    "path": str(cand),
                                    "hint": f"在 {sub.name}（SEGGER 版本目录）找到",
                                }
                except OSError:
                    continue
        which = _sh.which(exe_name)
        if which:
            return {"found": True, "path": str(Path(which)), "hint": "在系统 PATH 找到"}
        dl = _download_hint(tool)
        result: dict[str, Any] = {
            "found": False,
            "path": None,
            "hint": "未找到——可安装后重新探测，或点「浏览」选择程序位置",
        }
        if dl:
            result["download"] = dl
        # 注册表兜底（在固定位置与 PATH 均未命中时）
        reg = _registry_detect([exe_name])
        if reg:
            result = {"found": True, "path": reg, "hint": "通过注册表在安装目录找到"}
        return result

    @staticmethod
    def detect_all_tools() -> list[dict[str, Any]]:
        """一键探测全部支持的工具。"""
        order = [
            "keil",
            "atk_hswldbg",
            "jlink",
            "jlink_gdb",
            "openocd",
            "pyocd",
            "gcc",
            "gdb",
            "stlink",
            "make",
            "ninja",
        ]
        labels = {
            "keil": "Keil MDK (UV4)",
            "atk_hswldbg": "正点ATK-HSWLDBG（CMSIS-DAP）",
            "jlink": "J-Link",
            "jlink_gdb": "J-Link GDB Server",
            "openocd": "OpenOCD",
            "pyocd": "pyOCD",
            "gcc": "ARM GCC",
            "gdb": "ARM GDB",
            "stlink": "STM32 CubeProgrammer",
            "make": "Make",
            "ninja": "Ninja",
        }
        results = []
        for t in order:
            r = WebUIState.detect_tool(t)
            results.append({"tool": t, "label": labels[t], **r})
        return results

    # ------------------------------------------------------------------
    # skills 缓存
    # ------------------------------------------------------------------

    def iter_skills(self) -> list[Any]:
        """扫描技能根（cache 分钟级），返回 Skill 列表。"""
        from trae_agent.skills_engine import SKILLS_ROOT, scan_skills

        now = time.time()
        cache = getattr(self, "_skills_cache", None)
        if cache is not None:
            skills, ts = cache
            if now - ts < 60.0:
                return skills
        skills, _warnings = scan_skills([SKILLS_ROOT])
        self._skills_cache = (skills, now)  # type: ignore[attr-defined]
        return skills


# ---------------------------------------------------------------------------
# Agent 工厂 + AgentRun（chat SSE 的执行体）
# ---------------------------------------------------------------------------


def default_agent_factory(state: WebUIState, console: Any, model_key: str | None) -> Any:
    """默认 Agent 工厂：每次 chat 新建 Agent（Config.create + resolve + Agent(...)）。

    Args:
        state: 服务端状态（config_file 等）。
        console: CLIConsole 实现（WebConsole）。
        model_key: 请求指定的 model key（None 用 active）。

    Returns:
        trae_agent.agent.Agent 实例。
    """
    from trae_agent.agent import Agent

    config = Config.create(config_file=state.config_file)
    key = model_key or state.active_model_key(config=config)
    if key and config.models and key in config.models:
        config.resolve_config_values(provider=None, model=config.models[key].model)
    # 工具路径（Keil/驱动等）注入运行时提示词：用户没装 C 盘时 Agent 也能找到工具
    try:
        ui = state.load_ui_settings()
        tp = ui.get("tool_paths") or {}
    except Exception:  # noqa: BLE001 - 设置读取失败不影响 chat
        tp = {}
    tool_note = ""
    if tp:
        lines = [f"  - {k}: {v}" for k, v in tp.items() if v]
        if lines:
            tool_note = (
                "\n\n[Configured tool paths on this machine — use these absolute paths "
                "when invoking the corresponding tools (e.g. build Keil with `<uv4 path> -b <project>`); "
                "do NOT search other locations]:\n" + "\n".join(lines)
            )

    agent = Agent(
        "trae_agent",
        config,
        trajectory_file=None,
        cli_console=console,
    )

    if tool_note:

        class _AgentWithTools:
            """包装 Agent：在 user message 前注入工具路径注记（不动共享系统提示词）。"""

            def __init__(self, wrapper, note):
                self._wrapper = wrapper
                self._note = note
                self.agent = wrapper.agent

            def __getattr__(self, name):
                return getattr(self._wrapper, name)

            async def run(self, task, extra_args=None, tool_names=None):
                patched = dict(extra_args or {})
                issue = str(patched.get("issue", task))
                patched["issue"] = issue + self._note
                return await self._wrapper.run(task, patched, tool_names)

        return _AgentWithTools(agent, tool_note)
    return agent


class AgentRun:
    """一次 /api/chat 请求的执行体：假想 Agent 工厂注入 + 事件流桥接。

    真实模式：default_agent_factory 建真 Agent，execute_task 逐步触发 console
    事件；queue 读取方逐条 yield。测试模式：monkeypatch state 的 agent_factory
    为假 Agent（execute_task 直接塞事件后返回假 execution，绝不调真实模型）。
    """

    def __init__(
        self,
        *,
        state: "WebUIState",
        task: str,
        working_dir: str,
        model_key: str | None,
    ) -> None:
        self._state = state
        self._task = task
        self._working_dir = working_dir
        self._model_key = model_key
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=SSE_QUEUE_LIMIT)

    @property
    def queue(self) -> asyncio.Queue:
        return self._queue

    def make_console(self) -> Any:
        """创建绑定本 run 队列的 WebConsole。"""
        from webui.console_bridge import WebConsole

        return WebConsole(self._queue, queue_limit=SSE_QUEUE_LIMIT)

    def _make_agent(self) -> Any:
        """调用状态上的 Agent 工厂（真实 / 测试可替换，见 state.agent_factory）。"""
        factory = getattr(self._state, "agent_factory", None) or default_agent_factory
        return factory(self._state, self.make_console(), self._model_key)

    async def _drive(self, agent: Any) -> None:
        """Agent.run（内部完成 new_task + execute_task；异常转 error 事件）。

        注意：不改进程 CWD。工具层继承进程 CWD，但并发 chat 时全局 chdir
        会互相串扰，因此保持稳定；working_dir 以绝对路径经 new_task 的
        project_path 注入 agent 上下文（提示词内含项目根路径）。
        """
        try:
            await agent.run(
                self._task,
                {"project_path": self._working_dir, "issue": self._task},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 任意异常都转 error 事件，不崩 SSE
            self._enqueue({"type": "error", "message": f"Agent 执行失败: {exc}"})

    def _enqueue(self, event: dict) -> None:
        """drive 侧兜底入队（execute_task 内部没经过 console 时使用）。"""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(event)

    async def start(self) -> None:
        """创建 Agent 并后台启动 execute_task（fire-and-forget）。"""
        agent = self._make_agent()
        # LLM SDK 是同步阻塞调用：放到线程里跑，事件循环只负责 SSE 转发，
        # 多个 chat 才能真正并发（queue.put_nowait 线程安全）。
        self._exec_task = asyncio.get_running_loop().create_task(
            asyncio.to_thread(lambda: asyncio.run(self._drive(agent)))
        )

    async def wait_done(self) -> None:
        """等待执行任务结束（execute_task 返回或异常已转 error 事件）。"""
        task = getattr(self, "_exec_task", None)
        if task is not None:
            with contextlib.suppress(Exception):
                await task

    async def events(self) -> "Any":
        """事件生成器：逐条 yield queue 事件；final/error 后排空残余再收尾。

        心跳/超时逻辑由 server.py 的 SSE response 负责；此处只管队列语义。
        """
        while True:
            event = await self._queue.get()
            yield event
            if event.get("type") in ("final", "error"):
                break
        # 终止事件后：排空残余事件（execute_task 收尾时 console 可能还有尾部事件）
        while not self._queue.empty():
            try:
                yield self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break


# ---------------------------------------------------------------------------
# 硬件分析 / 上传等纯逻辑（端点薄封装）
# ---------------------------------------------------------------------------


def analyze_hardware_payload(
    content: str, filename: str, fmt: str | None, workspace: Path
) -> dict[str, Any]:
    """文本 → detect_format + 解析 + analyzeHardware → 响应 dict（含写盘 analysis.json）。

    Raises:
        ValueError: 格式无法识别 / 解析失败（转 400 中文信息）。
    """
    from trae_agent.hardware import analyze_netlist_text

    analysis = analyze_netlist_text(content, filename=filename, format=fmt)
    source_fmt = analysis.get("sourceFormat")
    if not source_fmt:
        raise ValueError("无法识别网表格式（不是 KiCad / SPICE / CSV / board.yaml）")

    saved_rel = None
    saved_err = None
    out_target = workspace / "hardware" / "analysis.json"
    try:
        out_target.parent.mkdir(parents=True, exist_ok=True)
        import json

        out_target.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        saved_rel = "hardware/analysis.json"
    except OSError as e:
        saved_err = str(e)

    return {
        "format": source_fmt,
        "analysis": {
            "mcu": analysis.get("mcu"),
            "buses": analysis.get("buses") or [],
            "gpio": analysis.get("gpio") or [],
            "power": analysis.get("power") or {},
            "pinmap": analysis.get("pinmap") or [],
            "issues": analysis.get("issues") or [],
        },
        "summary_markdown": analysis.get("summaryMarkdown") or "",
        "saved": saved_rel,
        "saved_error": saved_err,
    }


def _safe_zip_member(name: str) -> bool:
    """zip 成员名安全检查：禁绝对路径/盘符/../越界（含反斜杠变体）。"""
    member = name.replace("\\", "/")
    if member.startswith("/") or re.match(r"^[A-Za-z]:", member):
        return False
    return ".." not in member.split("/")


def _validate_zip_safe(dest_root: Path) -> str | None:
    """校验解包目标目录不含有害文件；返回拒收原因或 None（通过）。

    规则：目标内不得出现 py/pyc/exe/bat/cmd/dll 等可执行类文件；py 只在
    UPLOAD_ALLOWED_EXTS 语境下是允许的，因此 zip 内 py 也放行，但 pyc/pyz/
    exe/bat/com/cmd/dll/scr/ps1/js/vbs 一律拒绝（嵌入式工程用不到）。
    """
    forbidden = {
        ".pyc",
        ".pyo",
        ".pyz",
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".scr",
        ".ps1",
        ".js",
        ".vbs",
    }
    for p in dest_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in forbidden:
            return f"压缩包内含禁止类型文件: {p.name}"
    return None


def save_uploaded_file(
    workspace: Path,
    filename: str,
    data: bytes,
    subdir: str | None,
) -> tuple[dict[str, Any], str | None]:
    """保存单个上传文件（或解包 zip）到 workspace；返回 (saved 条目, 拒收原因)。

    文件名安全规则：取最后一段为纯文件名，但整串里出现 ``..`` 段直接拒收
    （不静默改写，前端能拿到明确 reason）。
    """
    raw = str(filename if filename is not None else "").strip()
    if not raw:
        return {"name": "", "path": ""}, "文件名为空"
    # 整串含 .. 段（../x、a/../../b、..\x 变体已在归一后覆盖）→ 拒收
    if ".." in [seg for seg in raw.replace("\\", "/").split("/") if seg]:
        return {"name": raw, "path": ""}, "文件名不允许包含 .. 路径段"
    # 清洗相对路径：保留目录结构（浏览器目录拖拽/递归上传场景），去盘符与 '.' 段
    segs = [
        s
        for s in raw.replace("\\", "/").split("/")
        if s and s != "." and not re.match(r"^[A-Za-z]:$", s)
    ]
    if not segs:
        return {"name": raw, "path": ""}, "文件名为空"
    rel_name = segs[-1]
    dir_segs = segs[:-1]
    ext = Path(rel_name).suffix.lower()
    if ext and ext not in UPLOAD_ALLOWED_EXTS:
        return {"name": rel_name, "path": ""}, f"不支持的文件类型: {ext}"
    if not ext:
        return {"name": rel_name, "path": ""}, "缺少可识别的扩展名"

    # 目标目录 = workspace / (subdir?) / 上传自带目录结构
    target_dir = workspace
    if subdir:
        cleaned = clean_rel_path(subdir)
        if cleaned is None:
            return {"name": rel_name, "path": ""}, f"非法的子目录: {subdir}"
        target_dir = workspace / cleaned
    if dir_segs:
        target_dir = target_dir.joinpath(*dir_segs)
    target_dir.mkdir(parents=True, exist_ok=True)
    # 防御：目标不得逃出 workspace
    if (
        workspace.resolve() not in target_dir.resolve().parents
        and target_dir.resolve() != workspace.resolve()
    ):
        return {"name": raw, "path": ""}, "路径越界"

    if ext == ".zip":
        # zip：解包到同名目录（去后缀）
        stem = Path(rel_name).stem or "unpacked"
        dest_root = target_dir / stem
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not _safe_zip_member(info.filename):
                        continue  # 越界成员直接跳过
                    if info.is_dir():
                        continue
                    member_name = info.filename.replace("\\", "/")
                    segs = [s for s in member_name.split("/") if s and s != "."]
                    if not segs:
                        continue
                    if sum(1 for s in segs if s == ".."):
                        continue  # _safe_zip_member 已拦，双保险
                    # 顶层多余包装目录压平：常见「x/…」首段等于 stem 时保留，否则也不压平
                    out_path = dest_root.joinpath(*segs)
                    base_resolved = dest_root.resolve()
                    if (
                        base_resolved not in out_path.resolve().parents
                        and out_path.resolve() != base_resolved
                    ):
                        continue
                    if out_path.suffix.lower() not in UPLOAD_ALLOWED_EXTS and out_path.suffix:
                        continue
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(zf.read(info))
        except zipfile.BadZipFile:
            return {"name": rel_name, "path": ""}, "非法的 zip 文件"
        reason = _validate_zip_safe(dest_root)
        if reason:
            with contextlib.suppress(OSError):
                __import__("shutil").rmtree(dest_root)
            return {"name": rel_name, "path": ""}, reason
        # 记录解包目录与 zip 本身
        zip_saved_path = (target_dir / rel_name).relative_to(workspace).as_posix()
        try:
            (target_dir / rel_name).write_bytes(data)
        except OSError:
            zip_saved_path = ""
        return (
            {
                "name": rel_name,
                "path": zip_saved_path,
                "size": len(data),
                "unzipped": dest_root.relative_to(workspace).as_posix(),
            },
            None,
        )

    # 普通文件
    target = target_dir / rel_name
    target.write_bytes(data)
    rel = target.relative_to(workspace).as_posix()
    return {"name": rel_name, "path": rel, "size": len(data)}, None


def read_workspace_text(
    workspace: Path, rel_path: object
) -> tuple[dict[str, Any] | None, str | None]:
    """读 workspace 内文本文件（≤200KB，超出截断）；返回 (payload, 错误)。"""
    target = resolve_in_dir(workspace, rel_path)
    if target is None:
        return None, "非法的文件路径"
    if not target.exists() or not target.is_file():
        return None, f"文件不存在: {rel_path}"
    try:
        raw = target.read_bytes()
    except OSError as e:
        return None, f"读取失败: {e}"
    truncated = len(raw) > FILE_READ_LIMIT
    raw = raw[:FILE_READ_LIMIT]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return (
        {
            "path": target.relative_to(workspace.resolve()).as_posix(),
            "name": target.name,
            "size": len(raw),
            "content": text,
            "truncated": truncated,
        },
        None,
    )


# ---------------------------------------------------------------------------
# SSE 事件协议表（供前端对照；GET /api/protocol 亦返回此表）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SSE 事件协议表（供前端对照；亦用于 /api/health 的版本与默认模型展示）
# ---------------------------------------------------------------------------

SSE_EVENT_PROTOCOL: list[dict[str, Any]] = [
    {"type": "step", "fields": {"n": "int，步骤号（从 1 起）"}},
    {"type": "assistant", "fields": {"text": "str，LLM 回复文本"}},
    {"type": "tool_use", "fields": {"id": "call_id", "name": "工具名", "args": "参数对象"}},
    {
        "type": "tool_result",
        "fields": {"id": "call_id", "name": "工具名", "output": "str 输出", "success": "bool"},
    },
    {"type": "final", "fields": {"text": "最终结果", "success": "bool"}, "terminal": True},
    {"type": "error", "fields": {"message": "str 错误信息"}, "terminal": True},
]


# ---------------------------------------------------------------------------
# Starlette 应用
# ---------------------------------------------------------------------------


def create_app(
    config_file: str = "trae_config.yaml",
    workspace: str | Path | None = None,
) -> "Starlette":
    """构建 Starlette 应用（路由 + 状态注入 + 延迟静态挂载）。

    Args:
        config_file: trae 配置文件路径（相对 CWD 或绝对路径）。
        workspace: 工作区目录（None 取 ./workspace，自动创建）。
    """
    import json
    from pathlib import Path as _P

    from starlette.applications import Starlette
    from starlette.datastructures import UploadFile
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Mount, Route

    ws_path = _P(workspace).resolve() if workspace is not None else (_P.cwd() / "workspace")
    ws_path.mkdir(parents=True, exist_ok=True)
    state = WebUIState(config_file=config_file, workspace=ws_path)

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------

    def _json_error(message: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    def _redacted(base_url: object) -> str | None:
        return sanitize_base_url(base_url)

    # ------------------------------------------------------------------
    # 端点
    # ------------------------------------------------------------------

    async def api_health(request):  # noqa: ANN001 - starlette handler
        """/api/health → {ok, version, default_model}"""
        try:
            default_model = state.active_model_key()
        except Exception:  # noqa: BLE001 - 健康检查不能 500
            default_model = None
        import webui

        return JSONResponse(
            {"ok": True, "version": webui.__version__, "default_model": default_model}
        )

    async def api_ui_settings(request):  # noqa: ANN001
        """GET/PUT /api/ui-settings — 主题色/品牌/工具路径持久化。"""
        if request.method == "GET":
            return JSONResponse(state.load_ui_settings())
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict):
            return _json_error("请求体必须是 JSON 对象")
        # 校验主题色格式（#RRGGBB）
        theme = body.get("theme") or {}
        if not isinstance(theme, dict):
            return _json_error("theme 必须是对象")
        import re as _re

        for k in ("primary", "secondary", "accent"):
            v = theme.get(k)
            if v is not None and not _re.fullmatch(r"#[0-9A-Fa-f]{6}", str(v)):
                return _json_error(f"theme.{k} 必须是 #RRGGBB 颜色值")
        # 工具路径：字符串值，且必须指向已存在的文件（空串=清除）
        tool_paths = body.get("tool_paths")
        if tool_paths is not None:
            if not isinstance(tool_paths, dict):
                return _json_error("tool_paths 必须是对象")
            for k, v in tool_paths.items():
                if not isinstance(v, str):
                    return _json_error(f"tool_paths.{k} 必须是字符串路径")
                if v.strip() and not Path(v).exists():
                    return _json_error(f"tool_paths.{k} 指向的路径不存在: {v}")
        try:
            saved = state.save_ui_settings(body)
        except OSError as e:
            return _json_error(f"保存失败: {e}", status=500)
        return JSONResponse({"ok": True, "settings": saved})

    async def api_fs_browse(request):  # noqa: ANN001
        """GET /api/fs/browse?path= → 列出目录内容（供设置里的「浏览选程序」）。

        read-only；path 为空 → 列出盘符。返回 {path, parent, entries:[{name, dir}]}。
        危险目录（系统级）不过滤——只用于选择已安装程序的位置。
        """
        raw = request.query_params.get("path", "").strip()
        if not raw:
            import string as _string

            drives = []
            for letter in _string.ascii_uppercase:
                d = Path(f"{letter}:\\")
                try:
                    if d.exists():
                        drives.append({"name": f"{letter}:", "dir": True})
                except OSError:
                    continue
            return JSONResponse({"path": "", "parent": "", "entries": drives})
        # 归一化（支持用户手输正/反斜杠）
        base = Path(raw.replace("/", "\\"))
        try:
            base = base.resolve(strict=False)
            if not base.is_dir():
                return _json_error(f"不是目录: {raw}")
            entries = []
            for e in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    entries.append({"name": e.name, "dir": e.is_dir()})
                except OSError:
                    continue
            # 上限保护
            if len(entries) > 2000:
                entries = entries[:2000]
            parent = str(base.parent) if str(base.parent) != str(base) else ""
            return JSONResponse({"path": str(base), "parent": parent, "entries": entries})
        except (OSError, ValueError) as e:
            return _json_error(f"无法访问: {e}")

    async def api_tools_detect(request):  # noqa: ANN001
        """POST /api/tools/detect — 一键探测全部工具或单个工具。

        body: {} 或 {tool: "...", custom_path?}。空 body / {"all": true} → 全部工具。
        """
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        tool = body.get("tool")
        all_mode = body.get("all") is True or not tool
        if all_mode:
            return JSONResponse({"tools": state.detect_all_tools()})
        tool = str(tool)
        custom = body.get("custom_path")
        if not tool:
            return _json_error(
                "缺少 tool 字段（keil|jlink|jlink_gdb|openocd|pyocd|gcc|gdb|stlink|make|ninja）"
            )
        # 自定义路径优先：存在即用
        if custom and isinstance(custom, str) and custom.strip():
            p = Path(custom.strip())
            if p.is_file():
                return JSONResponse({"found": True, "path": str(p), "hint": "自定义路径有效"})
            return JSONResponse({"found": False, "path": None, "hint": f"路径不存在: {custom}"})
        return JSONResponse(state.detect_tool(tool))

    async def api_config(request):  # noqa: ANN001
        """/api/config → models/active_model/providers/workspace/tools"""
        try:
            config = state.load_config()
        except (ConfigError, OSError) as e:
            return _json_error(f"配置加载失败: {e}", status=500)
        trae_cfg = config.trae_agent
        tools = list(trae_cfg.tools) if trae_cfg and trae_cfg.tools else []
        return JSONResponse(
            {
                "models": state.models_payload(config=config),
                "active_model": state.active_model_key(config=config),
                # model_providers 全部键（含未挂任何 model 的 provider，供前端补显示行）
                "providers": list(config.model_providers or {}),
                "workspace": str(state.workspace),
                "tools": tools,
            }
        )

    async def api_config_model(request):  # noqa: ANN001
        """POST /api/config/model {model_key} → 400 if 未知"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        model_key = body.get("model_key") if isinstance(body, dict) else None
        if not model_key or not isinstance(model_key, str):
            return _json_error("缺少 model_key 字段")
        try:
            state.switch_model(model_key)
        except KeyError:
            return _json_error(f"未知的 model_key: {model_key}")
        except (ConfigError, OSError) as e:
            return _json_error(f"配置加载失败: {e}", status=500)
        return JSONResponse({"ok": True, "active_model": model_key})

    async def api_config_credentials(request):  # noqa: ANN001
        """PUT /api/config/credentials {provider, api_key?, base_url?}（可视化配置模型）"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict) or not body.get("provider"):
            return _json_error("缺少 provider 字段")
        provider = str(body["provider"])
        api_key = body.get("api_key")
        base_url = body.get("base_url")
        if api_key is not None and not isinstance(api_key, str):
            return _json_error("api_key 必须是字符串")
        if base_url is not None and not isinstance(base_url, str):
            return _json_error("base_url 必须是字符串")
        if api_key is None and base_url is None:
            return _json_error("api_key 与 base_url 至少提供一项")
        try:
            state.update_provider_credentials(provider, api_key=api_key, base_url=base_url)
        except KeyError:
            return _json_error(
                f"未知的 provider: {provider}（在 trae_config.yaml 的 model_providers 里不存在）"
            )
        except FileNotFoundError as e:
            return _json_error(f"配置文件不可用: {e}", status=500)
        except (ConfigError, OSError, yaml.YAMLError) as e:  # type: ignore[name-defined]
            return _json_error(f"保存失败（配置已校验未写入）: {e}", status=500)
        return JSONResponse({"ok": True, "provider": provider})

    async def api_config_add_model(request):  # noqa: ANN001
        """POST /api/config/models {model_key, model, provider, max_tokens?}（新增模型条目）"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict):
            return _json_error("请求体必须是 JSON")
        model_key = body.get("model_key")
        model_name = body.get("model")
        provider = body.get("provider")
        if not model_key or not isinstance(model_key, str):
            return _json_error("缺少 model_key（如 my-glm）")
        if not model_name or not isinstance(model_name, str):
            return _json_error("缺少 model（模型名，如 zai-org/GLM-5.3）")
        if not provider or not isinstance(provider, str):
            return _json_error("缺少 provider（须为 model_providers 里已有的名字）")
        max_tokens = body.get("max_tokens")
        if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens <= 0):
            return _json_error("max_tokens 必须是正整数（或省略）")
        try:
            state.add_model(model_key, model_name, provider, max_tokens)
        except KeyError:
            return _json_error(
                f"未知的 provider: {provider}（先在配置里添加 provider 或使用现有项）"
            )
        except ValueError as e:
            return _json_error(str(e))
        except FileNotFoundError as e:
            return _json_error(f"配置文件不可用: {e}", status=500)
        except (ConfigError, OSError) as e:  # type: ignore[name-defined]
            return _json_error(f"保存失败: {e}", status=500)
        return JSONResponse({"ok": True, "model_key": model_key})

    async def api_config_add_provider(request):  # noqa: ANN001
        """POST /api/config/providers {provider, base_url, api_key?, protocol?, first_model?}（自定义 API 端点）"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict):
            return _json_error("请求体必须是 JSON")
        provider = body.get("provider")
        base_url = body.get("base_url")
        if not provider or not isinstance(provider, str):
            return _json_error("缺少 provider（唯一名称，如 my-relay）")
        if not base_url or not isinstance(base_url, str):
            return _json_error("缺少 base_url（如 https://api.example.com/v1）")
        api_key = body.get("api_key", "")
        if api_key is not None and not isinstance(api_key, str):
            return _json_error("api_key 必须是字符串")
        protocol = body.get("protocol", "openai_compatible")
        first_model = body.get("first_model")
        if first_model is not None and not isinstance(first_model, str):
            return _json_error("first_model 必须是字符串")
        try:
            model_key = state.add_provider(
                provider,
                base_url,
                api_key=api_key or "",
                protocol=protocol,
                first_model=first_model,
            )
        except ValueError as e:
            return _json_error(str(e))
        except FileNotFoundError as e:
            return _json_error(f"配置文件不可用: {e}", status=500)
        except (ConfigError, OSError) as e:
            return _json_error(f"保存失败: {e}", status=500)
        return JSONResponse({"ok": True, "provider": provider, "model_key": model_key})

    async def api_config_discover_models(request):  # noqa: ANN001
        """POST /api/config/discover-models — 用 API Key 直连 provider 拉取可用模型列表。

        body: {provider?（已配置的 provider 名）| base_url+api_key+protocol?（临时凭证）}
        返回 {models: [{id}]}。openai_compatible/openai → GET {base_url}/models；
        ollama → GET {base_url}/api/tags。
        """
        import json
        import urllib.request as _ur

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict):
            return _json_error("请求体必须是 JSON")
        provider = body.get("provider")
        base_url = body.get("base_url")
        api_key = body.get("api_key")
        protocol = body.get("protocol", "")
        if provider and isinstance(provider, str):
            # 从已保存的配置取连接信息（api_key 可被 body 覆盖，便于保存前探测）
            try:
                config = state.load_config()
            except (ConfigError, OSError) as e:
                return _json_error(f"配置加载失败: {e}", status=500)
            mp = (config.model_providers or {}).get(provider)
            if mp is None:
                return _json_error(f"未知的 provider: {provider}")
            base_url = mp.base_url or ""
            protocol = str(mp.provider)
            if not api_key:
                api_key = mp.api_key or ""
        if not base_url or not isinstance(base_url, str):
            return _json_error("缺少 base_url（或 provider 名）")
        url = base_url.rstrip("/")
        if protocol == "ollama":
            list_url = f"{url}/api/tags"
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        else:
            list_url = f"{url}/models"
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        try:
            req = _ur.Request(list_url, headers=headers, method="GET")
            with _ur.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001 - 网络/解析错误都要给中文提示
            return _json_error(f"连接失败: {e}")
        models: list[dict[str, str]] = []
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, list):
            for m in data:
                mid = m.get("id") or m.get("name") if isinstance(m, dict) else None
                if mid:
                    models.append({"id": str(mid)})
        if not models and isinstance(payload, dict):
            models_arr = payload.get("models")
            if isinstance(models_arr, list):  # ollama /api/tags
                for m in models_arr:
                    mid = m.get("name") if isinstance(m, dict) else None
                    if mid:
                        models.append({"id": str(mid)})
        models.sort(key=lambda x: x["id"].lower())
        return JSONResponse({"models": models, "total": len(models)})

    async def api_skills(request):  # noqa: ANN001
        """/api/skills → 列表（中文描述原样）"""
        skills = state.iter_skills()
        return JSONResponse(
            {
                "skills": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                        "tag": s.tag,
                        "enabled": bool(s.enabled),
                    }
                    for s in skills
                ],
                "total": len(skills),
            }
        )

    async def api_skill_detail(request):  # noqa: ANN001
        """/api/skills/{skill_id:path} → 详情（SKILL.md 全文 + 附属文件清单）"""
        skill_id = request.path_params.get("skill_id", "")
        # 前端 encodeURIComponent 可能发 %2F；starlette 已解码 path 参数，兼容两种形式
        lookup = skill_id
        candidates = {lookup}
        if "%2F" in lookup:
            candidates.add(lookup.replace("%2F", "/"))
        skills = state.iter_skills()
        matched = next((s for s in skills if s.id in candidates), None)
        if matched is None:
            return _json_error(f"未找到技能: {skill_id}", status=404)
        skill_dir = _P(matched.dir)

        def _side_files(d: _P) -> list[str]:
            out: list[str] = []

            def walk(dd: _P, prefix: str) -> None:
                try:
                    entries = sorted(dd.iterdir(), key=lambda p: p.name)
                except OSError:
                    return
                for e in entries:
                    rel = f"{prefix}/{e.name}" if prefix else e.name
                    if e.is_dir():
                        if e.name in (".git", "__pycache__"):
                            continue
                        walk(e, rel)
                    else:
                        out.append(rel)

            walk(d, "")
            return out

        body_text = ""
        try:
            body_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        except OSError as e:
            return _json_error(f"读取 SKILL.md 失败: {e}", status=500)
        files = [f for f in _side_files(skill_dir) if f != "SKILL.md"]
        return JSONResponse(
            {
                "id": matched.id,
                "name": matched.name,
                "description": matched.description,
                "tag": matched.tag,
                "frontmatter": matched.frontmatter,
                "body": body_text,
                "files": files,
            }
        )

    async def api_skills_search(request):  # noqa: ANN001
        """POST /api/skills/search {query} → match_skills 前 5"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        query = body.get("query") if isinstance(body, dict) else None
        if not isinstance(query, str) or not query.strip():
            return _json_error("缺少 query 字段")
        from trae_agent.skills_engine import match_skills

        results = match_skills(query, state.iter_skills(), limit=5)
        return JSONResponse(
            {
                "results": [
                    {"id": s.id, "name": s.name, "score": score, "why": why}
                    for s, score, why in results
                ]
            }
        )

    async def api_hardware_analyze(request):  # noqa: ANN001
        """POST /api/hardware/analyze {content?|path?, format?}"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict):
            return _json_error("请求体必须是 JSON 对象")
        content = body.get("content")
        path = body.get("path")
        fmt = body.get("format")
        if fmt is not None and not isinstance(fmt, str):
            return _json_error("format 必须是字符串")
        text: str | None = None
        filename: str | None = None
        if isinstance(content, str) and content.strip():
            text = content
            # 内嵌文本无文件名线索时用中性名（.txt 不触发后缀强判，走内容嗅探，
            # 免得把非网表文本误判成 KiCad）
            filename = body.get("filename") if isinstance(body.get("filename"), str) else None
            if not filename:
                filename = "content.txt"
        elif isinstance(path, str) and path.strip():
            target = resolve_in_dir(state.workspace, path)
            if target is None:
                return _json_error(f"非法的文件路径: {path}")
            if not target.is_file():
                return _json_error(f"文件不存在: {path}")
            if target.stat().st_size > FILE_READ_LIMIT:
                return _json_error(
                    f"网表文件过大（上限 {FILE_READ_LIMIT // 1024}KB）: {path}", status=413
                )
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return _json_error(f"读取失败: {e}")
            filename = target.name
        else:
            return _json_error("必须提供 content（网表文本）或 path（相对 workspace 的文件路径）")

        try:
            payload = analyze_hardware_payload(
                text, filename or "netlist.net", fmt, state.workspace
            )
        except ValueError as e:
            return _json_error(str(e))
        except Exception as e:  # noqa: BLE001
            return _json_error(f"解析失败: {e}", status=422)
        return JSONResponse(payload)

    async def api_files_upload(request):  # noqa: ANN001
        """POST /api/files/upload multipart(files 多值 + 可选 subdir)"""
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001
            return _json_error("请求必须是 multipart/form-data")
        subdir_raw = form.get("subdir")
        subdir = str(subdir_raw) if isinstance(subdir_raw, str) else None
        uploads: list[UploadFile] = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
        if not uploads:
            return _json_error("缺少 files 字段（multipart 文件）")
        saved: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        unzipped: list[str] = []
        for up in uploads:
            data = await up.read()
            entry, reason = save_uploaded_file(state.workspace, up.filename or "", data, subdir)
            if reason is not None:
                rejected.append({"name": entry.get("name") or up.filename or "", "reason": reason})
                continue
            if entry.get("unzipped"):
                unzipped.append(str(entry["unzipped"]))
            saved.append({k: v for k, v in entry.items() if k != "unzipped"})
        return JSONResponse(
            {"files": saved, "total": len(saved), "rejected": rejected, "unzipped": unzipped}
        )

    async def api_files(request):  # noqa: ANN001
        """/api/files?path=&recursive= → workspace 内容平铺列表"""
        rel_raw = request.query_params.get("path", "")
        recursive = request.query_params.get("recursive", "").lower() in ("1", "true", "yes")
        # '.' / '' → 根目录（前端会传 path=.）
        if rel_raw.strip() in (".", "/", ""):
            rel = ""
        else:
            rel = clean_rel_path(rel_raw)
            if rel is None:
                return _json_error(f"非法的路径: {rel_raw}")
        files = list_workspace_files(state.workspace, rel_path=rel, recursive=recursive)
        return JSONResponse({"files": files, "total": len(files)})

    async def api_files_delete(request):  # noqa: ANN001
        """POST /api/files/delete {path} → 删除 workspace 内文件或目录（含递归）。

        目录删除要求 body 带 confirm: true（前端二次确认）。
        """
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        rel_raw = body.get("path") if isinstance(body, dict) else None
        if not rel_raw or not isinstance(rel_raw, str):
            return _json_error("缺少 path 字段")
        if body.get("confirm") is not True:
            return _json_error("删除需要 confirm: true（二次确认）")
        rel = clean_rel_path(rel_raw)
        if rel is None:
            return _json_error(f"非法的路径: {rel_raw}")
        target = state.workspace / rel
        try:
            resolved = target.resolve()
            ws = state.workspace.resolve()
            if resolved == ws or ws not in resolved.parents:
                return _json_error("禁止删除 workspace 根目录")
        except OSError as e:
            return _json_error(f"路径解析失败: {e}")
        if not target.exists():
            return _json_error(f"不存在: {rel_raw}", status=404)
        import shutil

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as e:
            return _json_error(f"删除失败: {e}", status=500)
        return JSONResponse({"ok": True, "deleted": rel})

    async def api_files_download(request):  # noqa: ANN001
        """GET /api/files/download?path= → 下载文件或整个目录（目录自动打 zip 包）。"""
        from starlette.responses import Response as _Resp

        rel_raw = request.query_params.get("path", "")
        rel = clean_rel_path(rel_raw) if rel_raw.strip() else ""
        if rel is None or not rel:
            # path 为空 → 打包整个 workspace
            rel = ""
            target = state.workspace
        else:
            target = state.workspace / rel
        if not target.exists():
            return _json_error(f"不存在: {rel_raw or '(workspace)'}", status=404)
        try:
            resolved = target.resolve()
            ws = state.workspace.resolve()
            if ws != resolved and ws not in resolved.parents:
                return _json_error("路径越界")
        except OSError as e:
            return _json_error(f"路径解析失败: {e}")
        import io as _io

        if target.is_file():
            try:
                data = target.read_bytes()
            except OSError as e:
                return _json_error(f"读取失败: {e}", status=500)
            return _Resp(
                data,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{target.name}"',
                },
            )
        # 目录 → zip 流（不落盘）
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(target.rglob("*")):
                if p.is_dir():
                    continue
                if any(seg in LISTING_IGNORED_DIRS for seg in p.parts):
                    continue
                arcname = p.relative_to(target).as_posix()
                zf.write(p, arcname=arcname if rel else f"workspace/{arcname}")
        zip_name = f"{target.name}.zip" if rel else "workspace.zip"
        return _Resp(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
        )

    async def api_file(request):  # noqa: ANN001
        """GET /api/file?path= → 文本内容（≤200KB）"""
        rel_raw = request.query_params.get("path", "")
        payload, err = read_workspace_text(state.workspace, rel_raw)
        if err is not None:
            return _json_error(err, status=404 if "不存在" in err else 400)
        return JSONResponse(payload)

    async def api_chat_sse(request):  # noqa: ANN001
        """POST /api/chat {message, working_dir?, model_key?} → SSE 流"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json_error("请求体必须是 JSON")
        if not isinstance(body, dict):
            return _json_error("请求体必须是 JSON 对象")
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            return _json_error("缺少 message 字段")
        working_dir_raw = body.get("working_dir")
        if working_dir_raw is not None and not isinstance(working_dir_raw, str):
            return _json_error("working_dir 必须是字符串")
        model_key = body.get("model_key")
        if model_key is not None and not isinstance(model_key, str):
            return _json_error("model_key 必须是字符串")

        # 工作目录：默认 workspace；相对路径限制在 workspace 内；绝对路径必须是
        # 既有目录（嵌入式本机场景允许指向任意磁盘目录，如 D:\proj\blink）。
        if working_dir_raw and working_dir_raw.strip():
            wd_text = working_dir_raw.strip().replace("\\", "/")
            if _P(wd_text).is_absolute():
                wd = _P(wd_text)
            else:
                wd_resolved = resolve_in_dir(state.workspace, wd_text)
                if wd_resolved is None:
                    return _json_error(f"非法的 working_dir: {working_dir_raw}")
                wd = wd_resolved
            if not wd.is_dir():
                return _json_error(f"working_dir 不是有效目录: {working_dir_raw}")
            working_dir = str(wd.resolve())
        else:
            working_dir = str(state.workspace)

        if model_key:
            try:
                config_probe = state.load_config()
                if not config_probe.models or model_key not in config_probe.models:
                    return _json_error(f"未知的 model_key: {model_key}")
            except (ConfigError, OSError) as e:
                return _json_error(f"配置加载失败: {e}", status=500)

        run = AgentRun(
            state=state,
            task=message,
            working_dir=working_dir,
            model_key=model_key or None,
        )
        await run.start()

        async def event_source():
            """SSE 生成器：data: {json}\n\n + 15s :hb 心跳；终止事件后等待收尾。"""
            import contextlib as _cl

            heartbeat = SSE_HEARTBEAT_S

            async def _with_heartbeat(gen):
                """包一层：queue 空等超过 heartbeat 秒时发 :hb 注释行。"""
                agen = gen.__aiter__()
                while True:
                    try:
                        item = await asyncio.wait_for(agen.__anext__(), timeout=heartbeat)
                    except asyncio.TimeoutError:
                        yield ":hb\n\n"
                        continue
                    except StopAsyncIteration:
                        return
                    yield item

            try:
                async for event in _with_heartbeat(run.events()):
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                # 事件流终止（final/error 已发）：等待 execute_task 真正结束，
                # 再排空一次残余事件（执行完成后 flush 剩余事件再关流）。
                await run.wait_done()
                while not run.queue.empty():
                    with _cl.suppress(asyncio.QueueEmpty):
                        yield (
                            "data: "
                            + json.dumps(run.queue.get_nowait(), ensure_ascii=False)
                            + "\n\n"
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 流内异常转 error 事件
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "error", "message": f"内部错误: {exc}"}, ensure_ascii=False
                    )
                    + "\n\n"
                )

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def api_protocol(request):  # noqa: ANN001
        """/api/protocol → SSE 事件协议表（前端文档对照用）"""
        return JSONResponse({"events": SSE_EVENT_PROTOCOL})

    # ------------------------------------------------------------------
    # 路由表（静态挂载最后：存在则挂，不存在留 API 路由独立工作）
    # ------------------------------------------------------------------

    routes: list[Any] = [
        Route("/api/health", api_health, methods=["GET"]),
        Route("/api/config", api_config, methods=["GET"]),
        Route("/api/config/model", api_config_model, methods=["POST"]),
        Route("/api/config/credentials", api_config_credentials, methods=["PUT"]),
        Route("/api/config/models", api_config_add_model, methods=["POST"]),
        Route("/api/config/providers", api_config_add_provider, methods=["POST"]),
        Route("/api/config/discover-models", api_config_discover_models, methods=["POST"]),
        Route("/api/ui-settings", api_ui_settings, methods=["GET", "PUT"]),
        Route("/api/tools/detect", api_tools_detect, methods=["POST"]),
        Route("/api/fs/browse", api_fs_browse, methods=["GET"]),
        Route("/api/skills", api_skills, methods=["GET"]),
        Route("/api/skills/search", api_skills_search, methods=["POST"]),
        Route("/api/skills/{skill_id:path}", api_skill_detail, methods=["GET"]),
        Route("/api/hardware/analyze", api_hardware_analyze, methods=["POST"]),
        Route("/api/files/upload", api_files_upload, methods=["POST"]),
        Route("/api/files", api_files, methods=["GET"]),
        Route("/api/files/delete", api_files_delete, methods=["POST"]),
        Route("/api/files/download", api_files_download, methods=["GET"]),
        Route("/api/file", api_file, methods=["GET"]),
        Route("/api/chat", api_chat_sse, methods=["POST"]),
        Route("/api/protocol", api_protocol, methods=["GET"]),
    ]

    # 静态目录 = webui 包目录下 static/（前端队友负责；缺失时只跑 API 不崩）
    # no-cache：浏览器每次都回源验证（配 ETag），前端文件更新后刷新页面即可拿到新版，
    # 不再吃强缓存（此前 chat.js 改 xiaochen 后旧缓存仍显示 TRAE-EA 的根因）。
    static_dir = _P(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        from starlette.staticfiles import StaticFiles

        class _NoCacheStaticHeadersMiddleware:  # noqa: D401 - ASGI 中间件
            """给静态响应加 Cache-Control: no-cache（保留 ETag 协商缓存）。"""

            def __init__(self, app):  # noqa: ANN001
                self.app = app

            async def __call__(self, scope, receive, send):  # noqa: ANN001
                if scope["type"] == "http":

                    async def send_wrapper(message):  # noqa: ANN001
                        if message["type"] == "http.response.start":
                            headers = message.setdefault("headers", [])
                            headers.append((b"cache-control", b"no-cache"))
                        await send(message)

                    await self.app(scope, receive, send_wrapper)
                    return
                await self.app(scope, receive, send)

        routes.append(
            Mount(
                "/",
                app=_NoCacheStaticHeadersMiddleware(
                    StaticFiles(directory=str(static_dir), html=True)
                ),
                name="static",
            )
        )

    app = Starlette(routes=routes)
    app.state.webui = state
    state.app = app
    return app


__all__ = [
    "UPLOAD_ALLOWED_EXTS",
    "FILE_READ_LIMIT",
    "SSE_EVENT_PROTOCOL",
    "SSE_QUEUE_LIMIT",
    "SSE_HEARTBEAT_S",
    "WebUIState",
    "AgentRun",
    "default_agent_factory",
    "create_app",
    "clean_rel_path",
    "resolve_in_dir",
    "sanitize_base_url",
    "list_workspace_files",
    "analyze_hardware_payload",
    "save_uploaded_file",
    "read_workspace_text",
]
