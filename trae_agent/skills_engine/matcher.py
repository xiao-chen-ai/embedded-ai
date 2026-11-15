# SPDX-License-Identifier: MIT

"""matcher.py — 技能自然语言匹配（JS 版 core/skills/matcher.js 的移植）。

打分（与 JS 版一致）：
    name === query                    → 100
    query 包含完整 name               → 80
    name 词元重叠（按 _/- 拆分）       → overlap × 25
    description 关键词命中             → hits × 12
    兜底：2-gram Jaccard × 15          （仅当上面全为 0 时）
零分技能不返回；中文用相邻双字符 2-gram。
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注用，避免运行时循环导入
    from trae_agent.skills_engine.loader import Skill

OVERLAP_WEIGHT = 25
KEYWORD_WEIGHT = 12
BIGRAM_WEIGHT = 15

# CJK 统一表意文字
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
# JS 版 [^a-z0-9]+ 拆英文词（已 lowercase 后无 A-Z）
_SPLIT_RE = re.compile(r"[^a-z0-9]+")
# 词元字符白名单：小写字母/数字/CJK
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
# 2-gram 前的清洗：仅保留字母数字+CJK
_KEEP_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]")


# ---------------------------------------------------------------------------
# 词元 / 关键词抽取
# ---------------------------------------------------------------------------


def tokenize_name(name: str) -> list[str]:
    """技能名拆词：按非 [a-z0-9 CJK] 字符切分（_ - . 空格等）。"""
    return [t for t in _TOKEN_SPLIT_RE.split(str(name).lower()) if t]


def extract_query_terms(query: str) -> list[str]:
    """从任意文本抽取英文词（≥2 字符）+ CJK 2-gram，小写去重（保序）。"""
    q = str(query if query is not None else "").lower()
    terms: dict[str, None] = {}  # dict 保插入序（等价 JS Set 的有序语义）
    for w in _SPLIT_RE.split(q):
        if len(w) >= 2:
            terms[w] = None
    for run in _CJK_RUN_RE.findall(q):
        for i in range(len(run) - 1):
            terms[run[i : i + 2]] = None
        if len(run) == 1:
            terms[run] = None
    return list(terms)


def _bigrams(s: object) -> set[str]:
    """字符串的 2-gram 集合（限定字母数字+CJK 字符，小写）。"""
    t = _KEEP_RE.sub("", str(s if s is not None else "").lower())
    gram_set: set[str] = set()
    if len(t) == 1:
        gram_set.add(t)
    for i in range(len(t) - 1):
        gram_set.add(t[i : i + 2])
    return gram_set


def _jaccard(a_set: set[str], b_set: set[str]) -> float:
    if not a_set or not b_set:
        return 0.0
    inter = len(a_set & b_set)
    return inter / (len(a_set) + len(b_set) - inter)


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------


def _score_skill(
    skill: "Skill", query: str, q_norm: str, terms: list[str]
) -> tuple[float, list[str]]:
    name = str(skill.name if skill.name is not None else "").lower()
    score = 0.0
    hits: list[str] = []

    if name != "" and name == q_norm:
        score = 100
        hits.append("技能名与查询完全一致")
        return score, hits

    if name != "" and name in q_norm:
        score = 80
        hits.append(f'查询中包含技能名 "{skill.name}"')
        return score, hits

    # name 词元重叠
    name_tokens = tokenize_name(skill.name)
    q_tokens = set(tokenize_name(query))
    overlapped = [t for t in name_tokens if t in q_tokens]
    if overlapped:
        score += len(overlapped) * OVERLAP_WEIGHT
        hits.append(f"技能名包含查询关键词 ({', '.join(overlapped)})")

    # description 关键词命中（查询的英文词 + CJK 2-gram）
    desc = str(skill.description if skill.description is not None else "").lower()
    id_lower = str(skill.id if skill.id is not None else "").lower()
    desc_hit_terms: list[str] = []
    if desc != "" and terms:
        for t in terms:
            if len(t) >= 2 and (t in desc or (t in id_lower and len(t) >= 2)):
                desc_hit_terms.append(t)
    if desc_hit_terms:
        score += len(desc_hit_terms) * KEYWORD_WEIGHT
        hits.append(f"描述命中查询关键词 ({', '.join(desc_hit_terms[:5])})")

    # 兜底：query 与 name+description 的 2-gram Jaccard
    if score == 0:
        q_grams = _bigrams(query)
        s_grams = _bigrams(
            f"{skill.name if skill.name is not None else ''} "
            f"{skill.description if skill.description is not None else ''}"
        )
        j = _jaccard(q_grams, s_grams)
        if j > 0:
            # JS Math.round(x*100)/100 —— 四舍五入到偶数差异只在 .5 边界，语义取 Python round 即对齐
            score = round(j * BIGRAM_WEIGHT * 100) / 100
            hits.append(f"整体相似度 {round(j * 100)}%")

    return score, hits


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def match_skills(
    query: str, skills: "list[Skill]", limit: int = 5
) -> "list[tuple[Skill, float, str]]":
    """matchSkills(query, skills, limit=5) → [(skill, score, why)]。

    零分技能排除；全部为 0 时返回 []。排序：score 降序，同分按 id 升序。
    """
    q_norm = str(query if query is not None else "").strip().lower()
    if q_norm == "" or not skills:
        return []
    terms = extract_query_terms(query)
    results: list[tuple[Skill, float, str]] = []
    for skill in skills:
        score, hits = _score_skill(skill, query, q_norm, terms)
        if score > 0:
            results.append((skill, score, "；".join(hits) if hits else "命中"))
    if not results:
        return []
    results.sort(key=lambda r: (-r[1], r[0].id))
    return results[:limit]


# ---------------------------------------------------------------------------
# glob → 正则
# ---------------------------------------------------------------------------

_RE_CHARS_TO_ESCAPE = re.compile(r"[.+^$()|[\]\\{}]")


def _escape_literal(c: str) -> str:
    return _RE_CHARS_TO_ESCAPE.sub(lambda m: "\\" + m.group(0), c)


def _build_glob_regex_source(pat: str) -> str:
    re_src = ""
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "{":
            # 找配对的闭花括号
            depth = 0
            close = -1
            for k in range(i, len(pat)):
                if pat[k] == "{":
                    depth += 1
                elif pat[k] == "}":
                    depth -= 1
                    if depth == 0:
                        close = k
                        break
            if close == -1:
                re_src += "\\{"
                i += 1
                continue
            inner = pat[i + 1 : close]
            if "," in inner:
                alts = [_build_glob_regex_source(a) for a in inner.split(",")]
            else:
                alts = [_build_glob_regex_source(inner)]
            if len(alts) == 1 and alts[0] == "":
                i = close + 1
                continue
            re_src += "(?:" + "|".join(alts) + ")"
            i = close + 1
            continue
        if c == "*":
            if i + 1 < len(pat) and pat[i + 1] == "*":
                # `**` — 含分隔符的任意字符；容忍后跟 '/'
                j = i + 2
                while j < len(pat) and pat[j] == "*":
                    j += 1
                if j < len(pat) and pat[j] == "/":
                    j += 1
                re_src += "(?:.*)"
                i = j
            else:
                re_src += "[^/]*"
                i += 1
        elif c == "?":
            re_src += "[^/]"
            i += 1
        else:
            re_src += _escape_literal(c)
            i += 1
    return re_src


def glob_to_regex(pattern: object) -> re.Pattern[str]:
    """globToRegExp(pattern) — 支持 ``**``（跨段）、``*``（单段）、``?``、{a,b}。

    返回大小写敏感、两端锚定的已编译正则。
    """
    return re.compile(
        "^" + _build_glob_regex_source(str(pattern if pattern is not None else "")) + "$"
    )


def match_any_glob(rel_path: str, patterns: "list[str] | None") -> bool:
    """posix 风格相对路径对一组 glob 的匹配。空列表/空串模式不匹配。"""
    if not isinstance(patterns, list) or len(patterns) == 0:
        return False
    for p in patterns:
        if not isinstance(p, str) or p == "":
            continue
        if glob_to_regex(p).match(rel_path) is not None:
            return True
    return False


def glob_matches_everything(patterns: "list[str] | None") -> bool:
    """语义为空的 glob 数组（['*'] / ['**'] / ['**/*'] 匹配一切）判定。"""
    lst = [p for p in (patterns if patterns is not None else []) if isinstance(p, str) and p != ""]
    if len(lst) == 0:
        return True
    return all(p in ("*", "**", "**/*") for p in lst)
