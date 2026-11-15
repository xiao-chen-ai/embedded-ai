# SPDX-License-Identifier: MIT

"""keil_tool 单元测试：参数校验 + 路径检查（不真正启动 UV4）。"""

import unittest

from trae_agent.tools.base import ToolCallArguments
from trae_agent.tools.keil_tool import KeilBuildFlashTool


class TestKeilTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tool = KeilBuildFlashTool()

    def test_schema(self):
        self.assertEqual(self.tool.get_name(), "keil_build_flash")
        params = {p.name for p in self.tool.get_parameters()}
        self.assertIn("action", params)
        self.assertIn("project", params)
        self.assertIn("uv4_path", params)

    async def test_bad_action(self):
        r = await self.tool.execute(
            ToolCallArguments({"action": "nope", "project": "x", "uv4_path": "y"})
        )
        self.assertIsNotNone(r.error)

    async def test_missing_project(self):
        r = await self.tool.execute(ToolCallArguments({"action": "build"}))
        self.assertIsNotNone(r.error)

    async def test_project_not_found(self):
        r = await self.tool.execute(
            ToolCallArguments({"action": "build", "project": r"X:\nope\a.uvprojx", "uv4_path": "y"})
        )
        self.assertIsNotNone(r.error)
        self.assertIn("不存在", r.error)

    async def test_uv4_missing(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            proj = pathlib.Path(d) / "p.uvprojx"
            proj.write_text("x")
            r = await self.tool.execute(
                ToolCallArguments({"action": "build", "project": str(proj), "uv4_path": ""})
            )
            self.assertIsNotNone(r.error)
            self.assertIn("uv4_path", r.error)


if __name__ == "__main__":
    unittest.main()
