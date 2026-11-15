# SPDX-License-Identifier: MIT

"""__main__.py — `python -m webui` 启动入口（argparse + uvicorn）。

用法：
    python -m webui [--port 8788] [--host 127.0.0.1]
                    [--config-file trae_config.yaml] [--workspace ./workspace]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    # 默认路径以项目根（webui 包的上一级）解析为绝对路径——从任何 CWD 启动都有效
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        prog="python -m webui",
        description="trae-embedded-agent Web 服务端（流式 HTTP API + 静态前端）",
    )
    parser.add_argument("--port", type=int, default=8788, help="监听端口（默认 8788）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument(
        "--config-file",
        default=str(project_root / "trae_config.yaml"),
        help="trae 配置文件（默认 <项目根>/trae_config.yaml）",
    )
    parser.add_argument(
        "--workspace",
        default=str(project_root / "workspace"),
        help="工作区目录（默认 <项目根>/workspace，自动创建）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 相对路径以 CWD 解析（--workspace 支持相对/绝对）
    sys.path.insert(0, str(Path.cwd()))  # trae_agent 所在目录可直接 import

    from webui.app import create_app

    try:
        app = create_app(config_file=args.config_file, workspace=args.workspace)
    except Exception as e:  # noqa: BLE001 - 启动失败给出中文提示
        print(f"[webui] 启动失败：{e}", file=sys.stderr)
        return 1

    state = app.state.webui
    try:
        active_model = state.active_model_key()
    except Exception:  # noqa: BLE001
        active_model = "（配置不可用）"

    print("═" * 52)
    print("  trae-embedded-agent · Web 服务端")
    print("═" * 52)
    print(f"  地址       : http://{args.host}:{args.port}")
    print(f"  工作区     : {state.workspace}")
    print(f"  配置文件   : {args.config_file}")
    print(f"  当前模型   : {active_model}")
    static_ok = (Path(__file__).resolve().parent / "static" / "index.html").is_file()
    print(f"  前端       : {'已就绪' if static_ok else '未检测到 webui/static（仅 API 模式）'}")
    print("═" * 52)
    print("  Ctrl+C 停止")
    print()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
