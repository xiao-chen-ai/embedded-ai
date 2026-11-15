# SPDX-License-Identifier: MIT

"""frontmatter 解析子集测试：覆盖 embedded-ai-studio/test/unit/util-yaml.test.js 的全部刁钻样例语义。
目标是行为与 JS 版 core/util/yaml.js 完全一致（含不抛异常契约）。
"""

from trae_agent.skills_engine.loader import parse_frontmatter, parse_skill_md


def test_no_frontmatter_returns_none_and_body() -> None:
    text = "# 只是正文\n没有定界行\n"
    fm, body = parse_frontmatter(text)
    assert fm is None
    assert body == text


def test_unclosed_frontmatter_is_not_frontmatter() -> None:
    text = "---\nname: x\n正文没有结束定界\n"
    fm, body = parse_frontmatter(text)
    assert fm is None
    assert body == text


def test_tdd_sample_single_line_values() -> None:
    # mattpocock/skills 格式：单行 name / description
    text = (
        "---\n"
        "name: tdd\n"
        "description: Test-driven development. Use when the user wants to build features test-first.\n"
        "---\n\n# Test-Driven Development\n"
    )
    fm, body = parse_frontmatter(text)
    assert fm["name"] == "tdd"
    assert fm["description"] == (
        "Test-driven development. Use when the user wants to build features test-first."
    )
    assert body == "\n# Test-Driven Development\n"


def test_can_sample_folded_multiline_chinese() -> None:
    # embeddedskills 格式：>- 折叠中文多行 + 单行 argument-hint
    text = (
        "---\n"
        "name: can\n"
        "description: >-\n"
        "  嵌入式 CAN / CAN-FD 调试工具，用于扫描接口、监控报文。\n"
        "  当用户提到 CAN、CAN-FD、DBC 解码、总线抓包时自动触发，\n"
        "  也兼容 /can 显式调用。\n"
        'argument-hint: "[scan|monitor|send|log|decode|stats] ..."\n'
        "---\n\n# CAN\n"
    )
    fm, body = parse_frontmatter(text)
    assert fm["name"] == "can"
    assert fm["description"] == (
        "嵌入式 CAN / CAN-FD 调试工具，用于扫描接口、监控报文。"
        " 当用户提到 CAN、CAN-FD、DBC 解码、总线抓包时自动触发，"
        " 也兼容 /can 显式调用。"
    )
    assert fm["argument-hint"] == "[scan|monitor|send|log|decode|stats] ..."
    assert body == "\n# CAN\n"


def test_folded_block_scalar_no_trailing_newline() -> None:
    fm, _ = parse_frontmatter("---\ndescription: >\n  line one\n  line two\n---\nbody")
    assert fm["description"] == "line one line two"


def test_literal_block_preserves_newlines() -> None:
    fm, _ = parse_frontmatter("---\ntext: |\n  a\n  b\n---\n")
    assert fm["text"] == "a\nb\n"
    fm2, _ = parse_frontmatter("---\ntext: |-\n  a\n  b\n---\n")
    assert fm2["text"] == "a\nb"


def test_folded_blank_line_becomes_newline() -> None:
    fm, _ = parse_frontmatter("---\ntext: >-\n  a\n\n  b\n---\n")
    assert fm["text"] == "a\nb"


def test_deeper_indent_kept_after_dedent_common_prefix() -> None:
    fm, _ = parse_frontmatter("---\ntext: |-\n  def f():\n      return 1\n---\n")
    assert fm["text"] == "def f():\n    return 1"


def test_double_quoted_escapes() -> None:
    fm, _ = parse_frontmatter('---\ndesc: "line\\nbreak and\\ttab"\n---\n')
    assert fm["desc"] == "line\nbreak and\ttab"


def test_single_quoted_double_quote_escape() -> None:
    fm, _ = parse_frontmatter("---\ndesc: 'it''s fine'\n---\n")
    assert fm["desc"] == "it's fine"


def test_scalar_types_bool_number_null() -> None:
    fm, _ = parse_frontmatter("---\na: true\nb: false\nc: null\nd: 42\ne: -3.14\nf: 0\n---\n")
    assert fm["a"] is True
    assert fm["b"] is False
    assert fm["c"] is None
    assert fm["d"] == 42
    assert fm["e"] == -3.14
    assert fm["f"] == 0


def test_inline_array() -> None:
    fm, _ = parse_frontmatter("---\ntags: [a, b, 3, true, \"x, y\", 'z']\nempty: []\n---\n")
    assert fm["tags"] == ["a", "b", 3, True, "x, y", "z"]
    assert fm["empty"] == []


def test_unknown_syntax_kept_as_string_never_raises() -> None:
    fm, body = parse_frontmatter(
        "---\nweird: {a: 1, b: [2]}\nnested:\n  allowShell: false\n  next: 1\ndash: >2\n  text\n---\n"
    )
    assert fm["weird"] == "{a: 1, b: [2]}"  # 花括号对象：原样
    assert fm["nested"] == "allowShell: false\nnext: 1"  # 嵌套 map：原样字符串
    assert fm["dash"] == ">2"  # 未支持的块指示：原样
    assert body == ""  # 正文在闭合 ---\n 后为空串


def test_crlf_and_comment_lines() -> None:
    fm, body = parse_frontmatter("---\r\n# 注释\r\nname: can\r\n---\r\nbody line\r\n")
    assert fm["name"] == "can"
    assert body == "body line\r\n"


def test_bom_prefix_still_parses() -> None:
    fm, body = parse_frontmatter("\ufeff---\nname: x\n---\nB")
    assert fm["name"] == "x"
    assert body == "B"


def test_never_raises_on_garbage() -> None:
    # 任意畸形输入不抛异常（保底契约）
    for text in [
        "",
        "---",
        "---\n:",
        "---\n: value\n---\n",
        "---\n\0x: y\n---\n",
        "----\nname: x\n",
    ]:
        fm, body = parse_frontmatter(text)
        assert fm is None or isinstance(fm, dict)
        assert isinstance(body, str)


# ---------------- parse_skill_md（loader.js parseSkillMd 语义） ----------------


def test_parse_skill_md_full_shape() -> None:
    text = (
        "---\n"
        "name: serial\n"
        "description: >-\n"
        "  嵌入式串口调试工具，用于扫描串口、实时监控。\n"
        "  当用户提到串口、COM 口、UART 时自动触发。\n"
        'argument-hint: "[scan|monitor] ..."\n'
        "---\n"
        "\n"
        "# Serial\n"
        "\n"
        "Body text here."
    )
    parsed = parse_skill_md(text)
    assert parsed["name"] == "serial"
    assert "串口调试工具" in parsed["description"]
    assert "UART 时自动触发" in parsed["description"]
    assert parsed["frontmatter"]["argument-hint"] == "[scan|monitor] ..."
    # yaml.js 生产语义：body 从闭合 --- 行之后的原始字符开始（保留前导空行）
    assert parsed["body"].startswith("\n# Serial")
    assert "Body text here." in parsed["body"]


def test_parse_skill_md_missing_frontmatter_falls_back() -> None:
    parsed = parse_skill_md("# Just a body\n\nNo frontmatter here.")
    assert parsed["name"] is None
    assert parsed["description"] == ""
    assert "No frontmatter" in parsed["body"]


def test_parse_skill_md_quotes_and_booleans() -> None:
    text = (
        "---\n"
        'name: "tdd"\n'
        "description: 'Test-driven development'\n"
        "disable-model-invocation: true\n"
        "tags: [a, b]\n"
        "---\n"
        "body"
    )
    parsed = parse_skill_md(text)
    assert parsed["name"] == "tdd"
    assert parsed["description"] == "Test-driven development"
    assert parsed["frontmatter"]["disable-model-invocation"] is True
    assert parsed["frontmatter"]["tags"] == ["a", "b"]


def test_parse_skill_md_non_string_description_casts() -> None:
    # description 为数字时转 str；缺失 name → None
    parsed = parse_skill_md("---\ndescription: 42\n---\nbody")
    assert parsed["name"] is None
    assert parsed["description"] == "42"
