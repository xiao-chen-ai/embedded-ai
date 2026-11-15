# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""sexp.py — KiCad 6+ S 表达式 tokenizer/解析器(自 embedded-ai-studio/core/hardware/sexp.js 移植)。

纯函数、零依赖;多行安全,支持 "..." 字符串转义。
行为与 JS 版一致:括号不配对/内容被截断 → 尽力补全 + 中文警告,绝不抛异常。
"""

from dataclasses import dataclass

OPEN_PAREN = "("
CLOSE_PAREN = ")"

# 未闭合字符串的哨兵后缀(与 JS 版相同的 \u0000UNCLOSED 标记)。
UNCLOSED_MARK = "\x00UNCLOSED"

_WHITESPACE = " \t\r\n"


@dataclass
class SexpToken:
    """词法 token:t 为 '(' / ')' / 'atom' / 'str',v 为词值,pos 为起始位置。"""

    t: str
    v: str
    pos: int


@dataclass
class SexpStr:
    """字符串节点:s = 值,q = 是否为引号字符串(atom 时 False)。"""

    s: str
    q: bool


def tokenize_sexp(text: object) -> list[SexpToken]:
    """S 表达式文本分词。

    - 字符串字面量 "...",支持 \\\\ 与 \\" 转义(KiCad 转义规则),可跨多行(原样保留换行)。
    - atom:任何非空白、非括号的连续序列。
    - 截断不抛异常,由 parse_sexp 记录警告。
    """
    src = str(text if text is not None else "")
    toks: list[SexpToken] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == OPEN_PAREN:
            toks.append(SexpToken("(", "(", i))
            i += 1
            continue
        if ch == CLOSE_PAREN:
            toks.append(SexpToken(")", ")", i))
            i += 1
            continue
        if ch == '"':
            v = ""
            i += 1  # 跳过开引号
            closed = False
            while i < n:
                c = src[i]
                if c == "\\" and i + 1 < n:
                    # 转义:保留 \ 后的原字符(常见 \" 与 \\)
                    v += src[i + 1]
                    i += 2
                    continue
                if c == '"':
                    closed = True
                    i += 1
                    break
                v += c
                i += 1
            if not closed:
                # 字符串未闭合:按原文收下(尽力解析),由上层决定是否告警。
                v += UNCLOSED_MARK
            toks.append(SexpToken("str", v, i))
            continue
        if ch in _WHITESPACE:
            i += 1
            continue
        # atom
        v = ""
        start = i
        while i < n:
            c = src[i]
            if c in _WHITESPACE or c in (OPEN_PAREN, CLOSE_PAREN, '"'):
                break
            v += c
            i += 1
        if v:
            toks.append(SexpToken("atom", v, start))
    return toks


def is_sexp_leaf(node: object) -> bool:
    """节点辅助:是否为裸字符串(atom/引号字符串)。"""
    return isinstance(node, SexpStr)


def sexp_head(node: object) -> str:
    """取列表节点的头符号(第一个子节点为 atom 时);否则返回 ''。"""
    if isinstance(node, list) and len(node) > 0 and isinstance(node[0], SexpStr):
        return node[0].s
    return ""


def sexp_child(node: object, head: str) -> list | None:
    """按头符号取第一个子列表;找不到返回 None。"""
    if not isinstance(node, list):
        return None
    for c in node:
        if isinstance(c, list) and sexp_head(c) == head:
            return c
    return None


def sexp_children(node: object, head: str) -> list[list]:
    """按头符号取全部子列表。"""
    if not isinstance(node, list):
        return []
    out: list[list] = []
    for c in node:
        if isinstance(c, list) and sexp_head(c) == head:
            out.append(c)
    return out


def sexp_tagged_value(node: object, key: str) -> str | None:
    """在兄弟序列(如 (comp ...) 内部)找 (key "value") 形态的取值,兼容字段乱序。

    若 key 后不是子列表而是 `key value` 裸 atom 对,同样支持。
    找不到返回 None;(key) → ''。
    """
    if not isinstance(node, list):
        return None
    for c in node:
        if isinstance(c, list) and len(c) > 0 and isinstance(c[0], SexpStr) and c[0].s == key:
            # (key "value") 或 (key value);(key) → ''
            if len(c) >= 2 and isinstance(c[1], SexpStr):
                return c[1].s
            if len(c) >= 2 and isinstance(c[1], list):
                return None  # 取值是子列表而非标量
            return ""
    return None


def parse_sexp(text: object) -> tuple[list | None, list[str]]:
    """解析为嵌套列表树:'(' → [头符号, 子节点, ...],子节点为 SexpStr 或下一层列表。

    若文本为空/无法识别则 root 为 None。
    健壮性:括号不配对/内容被截断 → 尽力补全 + 中文警告,绝不抛异常。
    返回 (root, warnings)。
    """
    warnings: list[str] = []
    toks = tokenize_sexp(text)
    if len(toks) == 0:
        return None, warnings

    stack: list[list] = []  # 元素为节点数组
    root: list | None = None

    def mark_unclosed() -> None:
        if not any("未闭合的字符串" in w for w in warnings):
            warnings.append("网表存在未闭合的字符串字面量,已按原文尽力解析")

    for tok in toks:
        if tok.t == "(":
            node: list = []
            if len(stack) == 0:
                if root is not None:
                    # 多个顶层表达式:网表只应有一个 export,其余忽略并告警
                    if not any("多个顶层表达式" in w for w in warnings):
                        warnings.append("存在多个顶层表达式,仅解析第一个")
                    break  # 快进丢弃其余 token
                root = node
            else:
                stack[-1].append(node)
            stack.append(node)
            continue
        if tok.t == ")":
            if len(stack) == 0:
                if not any(w.startswith("多余的右括号") for w in warnings):
                    warnings.append("网表中存在未匹配的多余右括号,已忽略")
                continue
            stack.pop()
            continue
        # atom / str
        if len(stack) == 0:
            continue  # 括号外的裸文本:忽略
        v = tok.v
        if tok.t == "str" and v.endswith(UNCLOSED_MARK):
            v = v[: -len(UNCLOSED_MARK)]
            mark_unclosed()
        stack[-1].append(SexpStr(v, tok.t == "str"))
    if len(stack) > 0:
        warnings.append(f"表达式未完全闭合(缺少 {len(stack)} 个右括号),网表可能被截断,已尽力解析")
    return root, warnings
