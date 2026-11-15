# SPDX-License-Identifier: MIT

"""static.py — 静态资源挂载辅助（本包目录下 webui/static 延迟挂载）。

主应用在 webui/app.py 的 create_app 里已经做了「存在才挂」的延迟静态挂载；
本模块提供独立检查函数，供前端联调与测试确认静态目录状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: 静态目录 = webui 包目录下的 static/（前端队友负责写入，允许暂缺）。
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"


def static_dir() -> Path:
    """静态目录路径（不检查存在性）。"""
    return STATIC_DIR


def static_mount(name: str = "static") -> Any | None:
    """目录存在时返回 StaticFiles 挂载 app；不存在返回 None（不崩主应用）。"""
    if not STATIC_DIR.is_dir():
        return None
    from starlette.staticfiles import StaticFiles

    return StaticFiles(directory=str(STATIC_DIR), html=True)


__all__ = ["STATIC_DIR", "static_dir", "static_mount"]
