# SPDX-License-Identifier: MIT

"""server.py — Starlette 应用入口（create_app 的薄封装 + app 单例约定）。

用法：
    uv run python -m webui            # 默认入口（见 __main__.py）
    uvicorn webui.server:app          # ASGI 直接拉起（app 惰性构建）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from webui.app import create_app as _create_app_impl
from webui.static import static_mount as static_mount_obj

__all__ = ["create_app", "app", "build_static_router"]


def create_app(
    config_file: str = "trae_config.yaml",
    workspace: str | Path | None = None,
) -> Any:
    """构建 Starlette 应用（参数与 webui.app.create_app 一致）。"""
    return _create_app_impl(config_file=config_file, workspace=workspace)


class _LazyApp:
    """ASGI 惰性单例：uvicorn 引用 `webui.server.app` 时才构建应用。"""

    def __init__(self) -> None:
        self._app: Any = None

    def _ensure(self) -> Any:
        if self._app is None:
            self._app = create_app()
        return self._app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._ensure()(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ensure(), name)


#: uvicorn 可直接拉起：`uvicorn webui.server:app`
app = _LazyApp()

#: 重新导出静态挂载检查（目录存在返回 StaticFiles，否则 None）
build_static_router = static_mount_obj
