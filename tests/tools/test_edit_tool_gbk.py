# SPDX-License-Identifier: MIT

"""edit_tool 的 GBK 编码回退测试（中文 Windows 源码场景）。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from trae_agent.tools.base import ToolCallArguments
from trae_agent.tools.edit_tool import TextEditorTool


class TestEditToolGbkFallback(unittest.IsolatedAsyncioTestCase):
    """read_file: UTF-8 优先，GBK 自动回退；str_replace 对 GBK 文件同样可用。"""

    def setUp(self):
        self.tool = TextEditorTool()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name: str, data: bytes) -> Path:
        p = Path(self.tmp.name) / name
        p.write_bytes(data)
        return p

    async def test_view_gbk_file(self):
        gbk = "蜂鸣器初始化 GPIO_Pin_8 推挽输出".encode("gbk")
        p = self._write("beep_gbk.c", gbk)
        result = await self.tool.execute(ToolCallArguments({"command": "view", "path": str(p)}))
        self.assertIsNone(result.error)
        self.assertIn("蜂鸣器初始化", result.output)

    async def test_view_utf8_file_still_clean(self):
        p = self._write("utf8.c", "// 蜂鸣器\nint main(void){}\n".encode("utf-8"))
        result = await self.tool.execute(ToolCallArguments({"command": "view", "path": str(p)}))
        self.assertIsNone(result.error)
        self.assertIn("蜂鸣器", result.output)

    async def test_str_replace_on_gbk_file(self):
        gbk = "LED0=0;\t// 点灯".encode("gbk")
        p = self._write("main_gbk.c", gbk)
        result = await self.tool.execute(
            ToolCallArguments(
                {
                    "command": "str_replace",
                    "path": str(p),
                    "old_str": "LED0=0;",
                    "new_str": "LED0=1;",
                }
            )
        )
        self.assertIsNone(result.error)
        # 修改后写回为 UTF-8（策略：统一写 UTF-8）
        self.assertIn("LED0=1;", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
