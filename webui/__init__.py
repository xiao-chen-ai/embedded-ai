# SPDX-License-Identifier: MIT

"""webui — trae-agent 的 Web 服务端（Starlette 流式 HTTP API）。

把 trae-agent 引擎包装成 SSE 聊天 + 技能/硬件/文件 HTTP 端点；
只 import trae_agent，不修改其任何现有代码。
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
