# SPDX-License-Identifier: MIT

"""loader.py — 技能发现与 SKILL.md 解析（JS 版 core/skills/loader.js + core/util/yaml.js 的移植）。

技能位于 `<root>/<tag>/<skillDir>/SKILL.md`（技能根目录下两级）。scan_skills
合并多个 roots 并按 id 去重（先出现者胜，重复记录为 warning）。

frontmatter 解析是 core/util/yaml.js parseFrontmatter 的 Python 等价实现：
仅支持 SKILL.md 真实用法子集（key: value、块标量 >/</|/-、引号、
bool/数字/null、行内数组），未知语法一律存原始字符串，绝不抛异常。
不使用 pyyaml——保持零依赖、与 JS 版容错行为一致。
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# 与技能目录顶层平级的额外文件（loader.js 的 EXTRA_FILE_NAMES）
EXTRA_FILE_NAMES = [".source.json", "README.md", "config.json"]

# skill dir 名 / tag 名允许的字符（sanitizeSkillName 同款规则在导入器里，这里只做防御）
_KEY_RE = re.compile(r"^([\w.\-]+):(.*)$")  # JS /^([\w.\-]+):\s?(.*)$/：\s? 至多吃掉一格空白
_BLOCK_RE = re.compile(r"^([>|])([+-]?)$")


# CJK 统一表意文字范围（\u4e00-\u9fff，matcher 使用；loader 预留同款判断）
def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


@dataclass
class Skill:
    """一个已发现的技能（JS 版 loader.js 的 Skill 对象）。"""

    id: str  # 形如 "mp/tdd" / "zh/serial"
    tag: str  # 一级目录名（JS 版叫 source，语义相同；本项目统一叫 tag）
    name: str
    description: str = ""
    dir: str = ""  # 技能目录绝对路径（字符串，Windows 下为反斜杠）
    entry_path: str = ""  # SKILL.md 绝对路径
    frontmatter: dict[str, object] = field(default_factory=dict)
    extra_files: list[str] = field(default_factory=list)
    enabled: bool = True


# ---------------------------------------------------------------------------
# frontmatter 解析（yaml.js 移植）
# ---------------------------------------------------------------------------


def _strip_cr(line: str) -> str:
    return line[:-1] if line.endswith("\r") else line


def parse_frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    """解析文本的 frontmatter 与正文；无 frontmatter 时返回 (None, 原文)。

    语义对齐 JS core/util/yaml.js：
    - 去 BOM；第一行（trim 后）必须是 ``---``
    - 闭合定界行 ``---`` 或 ``...``（trim 后）；未闭合视为无 frontmatter
    - 空行与 ``#`` 注释行跳过；无法识别的行直接跳过（保底不抛）
    - 正文按原文字符切片，不改动换行风格
    """
    raw = text if isinstance(text, str) else ""
    src = raw[1:] if raw.startswith("\ufeff") else raw  # 去 BOM
    raw_lines = src.split("\n")
    lines = [_strip_cr(line) for line in raw_lines]
    if not lines or lines[0].strip() != "---":
        return None, raw
    fm: dict[str, object] = {}
    end = -1  # 闭合定界行下标
    i = 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped in ("---", "..."):
            end = i
            break
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        m = _KEY_RE.match(line)
        if not m:  # 无法识别的行直接跳过（保底不抛）
            i += 1
            continue
        key = m.group(1)
        rest = m.group(2)
        if rest.startswith(" ") and not rest.startswith("  "):
            # 对齐 JS \s?：值首恰一格空白被分隔符吃掉（多格保留原样）
            rest = rest[1:]
        elif rest.startswith("\t"):
            rest = rest[1:]
        indent = len(line) - len(line.lstrip())
        block = _BLOCK_RE.match(rest.strip())
        if block:
            # 显式块标量：> 折叠 / | 保留
            value, i = _read_block(lines, i + 1, indent, block.group(1), block.group(2))
            fm[key] = value
            continue
        if rest.strip() == "" and _next_deeper(lines, i + 1, indent):
            # 未知嵌套结构（如嵌套 map）：该 key 存原始字符串
            value, i = _read_block(lines, i + 1, indent, "plain", "")
            fm[key] = value
            continue
        fm[key] = _parse_scalar(rest.strip())
        i += 1
    if end < 0:
        return None, raw  # 未闭合 → 不算 frontmatter
    body_start = 0
    for k in range(end + 1):  # 0..end 共 end+1 行，+1 为 '\n' 本身
        body_start += len(raw_lines[k]) + 1
    return fm, src[body_start:]


def _next_deeper(lines: list[str], start: int, base_indent: int) -> bool:
    """判断 start 起后续行是否有比 base_indent 更深的缩进内容。"""
    for i in range(start, len(lines)):
        t = lines[i].strip()
        if t in ("---", "..."):
            return False
        if t == "":
            continue
        return len(lines[i]) - len(lines[i].lstrip()) > base_indent
    return False


def _read_block(
    lines: list[str], start: int, base_indent: int, style: str, chomp: str
) -> tuple[str, int]:
    """读取块标量/嵌套块：收集缩进行，dedent 后按 style 聚合，返回 (value, next)。"""
    content: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        t = line.strip()
        if t in ("---", "..."):
            break
        if t != "" and len(line) - len(line.lstrip()) <= base_indent:
            break
        content.append(line)
        i += 1
    if chomp != "+":
        while content and content[-1].strip() == "":
            content.pop()
    min_indent = None
    for line in content:
        if line.strip() != "":
            ind = len(line) - len(line.lstrip())
            min_indent = ind if min_indent is None else min(min_indent, ind)
    if min_indent is None:
        min_indent = 0
    out = ["" if line.strip() == "" else line[min_indent:] for line in content]
    if style == "plain":
        value = "\n".join(out)
    elif style == "|":
        value = "\n".join(out) + ("\n" if chomp == "" and out else "")
    else:
        # '>' 折叠：非空行间以空格相连，空行转为换行，尾部空白剥掉
        parts: list[str] = []
        cur: list[str] = []
        for line in out:
            if line == "":
                if cur:
                    parts.append(" ".join(cur))
                    cur = []
                parts.append("\n")
            else:
                cur.append(line.strip())
        if cur:
            parts.append(" ".join(cur))
        value = re.sub(r"[\n ]+$", "", "".join(parts))
    return value, i


def _parse_scalar(s: str) -> object:
    """解析单值：引号字符串 / 数组 / 布尔 / 数字 / 原样字符串。"""
    if s == "":
        return ""
    if len(s) >= 2 and s[0] == '"' and s.endswith('"'):
        return _double_quoted(s)
    if len(s) >= 2 and s[0] == "'" and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    if s[0] == "[" and s.endswith("]"):
        return _parse_inline_array(s)
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?", s):
        return float(s)
    return s  # 未知语法：存原始字符串


def _double_quoted(s: str) -> str:
    r"""双引号字符串（支持 \\ \" \n \t \r \0 转义；未知转义取字面）。"""
    inner = s[1:-1]
    out_chars: list[str] = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c != "\\" or i == len(inner) - 1:
            out_chars.append(c)
            i += 1
            continue
        i += 1
        e = inner[i]
        out_chars.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(e, e))
        i += 1
    return "".join(out_chars)


def _parse_inline_array(s: str) -> object:
    """行内数组 [a, b]：项支持引号与标量；嵌套/复杂结构返回原字符串。"""
    inner = s[1:-1].strip()
    if inner == "":
        return []
    if "[" in inner or "{" in inner:  # 嵌套不支持 → 原样
        return s
    items: list[str] = []
    cur = ""
    quote: str | None = None
    for ch in inner:
        if quote is not None:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            cur += ch
        elif ch == ",":
            items.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip() != "" or items:
        items.append(cur)
    return [_parse_scalar(x.strip()) for x in items if x.strip() != ""]


# ---------------------------------------------------------------------------
# SKILL.md 解析（loader.js parseSkillMd）
# ---------------------------------------------------------------------------


def parse_skill_md(text: str) -> dict[str, object]:
    """把 SKILL.md 文本解析为 {name, description, frontmatter, body}。

    name 为 None 表示 frontmatter 缺失（或非字符串/空）name 字段；
    description 缺失时为空串，非字符串转 str。
    """
    frontmatter, body = parse_frontmatter(text)
    fm = frontmatter if isinstance(frontmatter, dict) else {}
    name_v = fm.get("name")
    name = name_v.strip() if isinstance(name_v, str) and name_v.strip() != "" else None
    if "description" not in fm or fm.get("description") is None:
        description = ""
    else:
        d = fm["description"]
        description = d if isinstance(d, str) else str(d)
    return {"name": name, "description": description, "frontmatter": fm, "body": body}


# ---------------------------------------------------------------------------
# 目录扫描（loader.js loadSkillDir + scanRoot + scanSkills）
# ---------------------------------------------------------------------------


def _load_skill_dir(
    tag: str, skill_dir: Path, rel_id: str
) -> tuple[Skill | None, str | None, str | None]:
    """加载一个技能目录；返回 (skill, warning, error)。error 非 None 表示跳过。"""
    entry = skill_dir / "SKILL.md"
    try:
        text = entry.read_text(encoding="utf-8")
    except OSError as err:
        return None, None, f"无法读取 {rel_id}: {err}"
    try:
        parsed = parse_skill_md(text)
    except Exception as err:  # 理论上 parse 不抛；防御性兜底
        return None, None, f"解析 {rel_id} 失败: {err}"
    name = parsed["name"] if isinstance(parsed["name"], str) else None
    if name is None:
        name = skill_dir.name
    # 技能目录顶层的额外文件
    extra_files: list[str] = []
    try:
        names = {e.name for e in os_scandir_names(skill_dir)}
    except OSError:
        names = set()
    for extra in EXTRA_FILE_NAMES:
        if extra in names:
            extra_files.append(extra)
    skill = Skill(
        id=rel_id,
        tag=tag,
        name=name,
        description=parsed["description"] if isinstance(parsed["description"], str) else "",
        dir=str(skill_dir),
        entry_path=str(entry),
        frontmatter=parsed["frontmatter"] if isinstance(parsed["frontmatter"], dict) else {},
        extra_files=extra_files,
        enabled=True,
    )
    warning = None
    if parsed["name"] is None:
        warning = f'{rel_id}: frontmatter 缺失 name 字段，使用目录名 "{name}" 作为技能名'
    return skill, warning, None


def os_scandir_names(d: Path) -> list["os.DirEntry"]:
    """os.scandir 的薄包装（便于容错），返回目录项列表。"""
    return list(os.scandir(d))


_IGNORED_DIRS = {".git", "node_modules"}


def _scan_root(root: Path, warnings: list[str]) -> list[Skill]:
    """扫描单个技能根目录（两级 <tag>/<skillDir>/SKILL.md）。"""
    out: list[Skill] = []
    try:
        level1 = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as err:
        warnings.append(f"无法读取技能根目录 {root}: {err}")
        return out
    for tag_path in level1:
        if not tag_path.is_dir():
            continue
        tag = tag_path.name
        if tag in _IGNORED_DIRS:
            continue
        try:
            level2 = sorted(tag_path.iterdir(), key=lambda p: p.name)
        except OSError as err:
            warnings.append(f"无法读取 {tag_path}: {err}")
            continue
        for skill_path in level2:
            if not skill_path.is_dir() or skill_path.name in _IGNORED_DIRS:
                continue
            if not (skill_path / "SKILL.md").exists():
                continue
            skill_id = f"{tag}/{skill_path.name}"
            skill, warning, error = _load_skill_dir(tag, skill_path, skill_id)
            if error is not None:
                warnings.append(error)
                continue
            if warning is not None:
                warnings.append(warning)
            assert skill is not None
            out.append(skill)
    return out


def scan_skills(
    roots: list[Path] | str | Path, disabled: set[str] | list[str] | None = None
) -> tuple[list[Skill], list[str]]:
    """扫描技能根目录集合；返回 (skills, warnings)。

    - roots 是单个根或根列表（JS 版支持两者，这里同样兼容）
    - 同 id 去重：先出现者胜，重复记录 warning
    - disabled 里的 id 对应技能 enabled=False
    - 结果按 id 排序（JS 版 a.id < b.id 升序）
    """
    root_list = [roots] if isinstance(roots, (str, Path)) else list(roots)
    disabled_set: set[str] = set(disabled if disabled is not None else [])
    all_skills: list[Skill] = []
    warnings: list[str] = []
    for root in root_list:
        root_path = Path(str(root)).resolve()
        all_skills.extend(_scan_root(root_path, warnings))
    # 按 id 去重：先出现者胜
    by_id: dict[str, Skill] = {}
    for skill in all_skills:
        if skill.id in by_id:
            warnings.append(
                f"技能 {skill.id} 重复（{skill.dir}），保留先出现的 {by_id[skill.id].dir}"
            )
            continue
        by_id[skill.id] = skill
    skills = [
        Skill(**{**skill.__dict__, "enabled": skill.id not in disabled_set})
        for skill in by_id.values()
    ]
    skills.sort(key=lambda s: s.id)
    return skills, warnings
