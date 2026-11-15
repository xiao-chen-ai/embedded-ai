# SPDX-License-Identifier: MIT

"""matcher 测试：打分权重（100/80/token×25/关键词×12/2-gram Jaccard×15）、
中文 2-gram、排序、glob 转换——语义对齐 skills-matcher.test.js。
"""

from trae_agent.skills_engine.loader import Skill
from trae_agent.skills_engine.matcher import (
    extract_query_terms,
    glob_matches_everything,
    glob_to_regex,
    match_any_glob,
    match_skills,
)


def _skill(sid: str, name: str, description: str) -> Skill:
    tag = sid.split("/", 1)[0]
    return Skill(id=sid, tag=tag, name=name, description=description)


SKILLS = [
    _skill(
        "zh/serial",
        "serial",
        "嵌入式串口调试工具，用于扫描串口、实时监控、发送数据、记录日志和 Hex 查看。"
        "当用户提到串口、COM 口、UART、AT 命令调试、波特率时自动触发。",
    ),
    _skill(
        "zh/gcc",
        "gcc",
        "GCC 嵌入式工程构建工具（CMake + arm-none-eabi-gcc），用于扫描 CMake 型嵌入式工程、"
        "列出预设、配置、编译、重建、清理和分析 ELF 大小。当用户提到 GCC、arm-none-eabi、"
        "CMake 嵌入式编译、交叉编译时自动触发。",
    ),
    _skill(
        "mp/tdd",
        "tdd",
        "Test-driven development. Use when the user wants to build features or fix bugs "
        "test-first, mentions red-green-refactor, or wants integration tests.",
    ),
    _skill(
        "zh/openocd",
        "openocd",
        "OpenOCD 烧录调试工具。当用户提到 openocd、烧录固件、flash、调试器、JTAG、SWD 时自动触发。",
    ),
    _skill(
        "zh/jlink",
        "jlink",
        "J-Link 烧录调试工具。当用户提到 jlink、JLinkExe、仿真器烧录时自动触发。",
    ),
]


def test_exact_name_match_scores_100_first() -> None:
    r = match_skills("serial", SKILLS)
    assert r[0][0].id == "zh/serial"
    assert r[0][1] == 100


def test_query_containing_full_name_scores_80() -> None:
    r = match_skills("请帮我用 serial 工具看看串口", SKILLS)
    assert r[0][0].id == "zh/serial"
    assert r[0][1] == 80


def test_chinese_query_serial_debug_hits_serial_skill() -> None:
    r = match_skills("帮我调试串口", SKILLS)
    assert r[0][0].id == "zh/serial"
    assert len(r[0][2]) > 0  # why 非空中文说明


def test_chinese_query_build_firmware_hits_gcc_top() -> None:
    r = match_skills("编译固件", SKILLS, limit=5)
    assert len(r) > 0
    assert r[0][0].id == "zh/gcc"
    assert any(x[0].id == "zh/gcc" for x in r[:5])


def test_english_test_first_hits_tdd() -> None:
    r = match_skills("I want to fix this bug test-first", SKILLS)
    assert any(x[0].id == "mp/tdd" for x in r)


def test_flash_query_ranks_openocd_before_serial() -> None:
    r = match_skills("烧录固件到板子上", SKILLS, limit=5)
    ids = [x[0].id for x in r]
    assert "zh/openocd" in ids
    assert "zh/serial" not in ids or ids.index("zh/openocd") < ids.index("zh/serial")


def test_no_match_returns_low_scores_only() -> None:
    r = match_skills("今天天气怎么样", SKILLS)
    # 天气可能弱 2-gram 命中，但不应有高分
    assert all(x[1] < 25 for x in r)


def test_limit_respected() -> None:
    r = match_skills("serial", SKILLS, limit=2)
    assert len(r) <= 2


def test_empty_query_or_skills_returns_empty() -> None:
    assert match_skills("", SKILLS) == []
    assert match_skills("serial", []) == []


def test_why_is_chinese_explanation() -> None:
    r = match_skills("帮我调试串口", SKILLS)
    assert len(r) > 0
    skill, score, why = r[0]
    assert isinstance(why, str) and len(why) > 0


def test_name_token_overlap_weight_25() -> None:
    # 无 name 直接命中时，name 词元重叠 ×25：id 不含查询词，排除 id 关键词干扰
    skills = [_skill("mp/zz", "code-review", "无描述内容")]
    r = match_skills("please code review this", skills)
    assert r[0][0].id == "mp/zz"
    # "code" + "review" 两个词元 → 2 × 25 = 50
    assert r[0][1] == 50


def test_description_keyword_weight_12() -> None:
    # 无 name 命中、无词元重叠，但描述含查询关键词：1 hit × 12
    skills = [_skill("zh/keil", "keil", "Keil MDK 工程编译工具，编译说的是 keil5 build。")]
    r = match_skills("编译固件", skills)
    assert r[0][0].id == "zh/keil"
    assert r[0][1] == 12  # 「编译」命中 1 次；「固件」不在描述中


def test_bigram_fallback_weight() -> None:
    # 无任何直接命中（name/词元/关键词）→ 2-gram Jaccard 兜底（0 < 分 ≤ 15）
    skills = [_skill("mp/qq", "qq", "xyz")]
    r = match_skills("xyza", skills)
    assert len(r) == 1
    assert 0 < r[0][1] <= 15
    # 查询与 name+description 无字符重叠 → 不返回
    r2 = match_skills("zzzz", skills)
    assert r2 == [] or all(x[1] < 25 for x in r2)


def test_tie_break_by_id_ascending() -> None:
    # 同分时按 id 升序（JS 版 a.skill.id < b.skill.id）
    skills = [
        _skill("zz/beta", "beta", "命中关键词"),
        _skill("aa/beta", "beta", "命中关键词"),
    ]
    r = match_skills("beta", skills)
    assert [x[0].id for x in r] == ["aa/beta", "zz/beta"]
    assert r[0][1] == 100  # 两个都精确命中


def test_score_does_not_mutate_skills() -> None:
    import copy

    copy_list = copy.deepcopy(SKILLS)
    match_skills("编译固件", SKILLS)
    assert copy_list == SKILLS


def test_extract_query_terms_english_and_cjk_bigrams() -> None:
    terms = extract_query_terms("帮我调试 serial port 串口")
    assert "调试" in terms
    assert "串口" in terms
    assert "serial" in terms
    assert "port" in terms


def test_extract_query_terms_single_cjk_char() -> None:
    # 单个 CJK 字符（不成对）保底收录
    terms = extract_query_terms("烧")
    assert "烧" in terms


# ---------------- glob → regex（JS 版 globToRegExp 语义） ----------------


def test_glob_double_star_crosses_segments() -> None:
    assert glob_to_regex("skills/engineering/**").match("skills/engineering/tdd")
    assert glob_to_regex("skills/engineering/**").match("skills/engineering/a/b/SKILL.md")
    assert glob_to_regex("skills/*").match("skills/tdd")
    assert not glob_to_regex("skills/*").match("a/b/c")


def test_glob_single_star_within_segment() -> None:
    assert glob_to_regex("*.c").match("main.c")
    assert not glob_to_regex("*.c").match("src/main.c")
    assert glob_to_regex("**/*.c").match("src/main.c")


def test_glob_brace_alternation() -> None:
    assert glob_to_regex("**/*.{c,h}").match("src/board.h")
    assert glob_to_regex("**/*.{c,h}").match("src/board.c")
    assert not glob_to_regex("**/*.{c,h}").match("src/board.cpp")


def test_glob_star_matches_bare_name_not_slash() -> None:
    assert glob_to_regex("*").match("serial")
    assert glob_to_regex("*").match("gcc")
    assert not glob_to_regex("*").match("a/b")
    assert glob_to_regex("**").match("a/b/c")


def test_glob_escapes_regex_metachars() -> None:
    assert glob_to_regex("a.b+").match("a.b+")
    assert not glob_to_regex("a.b").match("axb")


def test_glob_unbalanced_brace_literal() -> None:
    # 未闭合花括号按字面量处理
    assert glob_to_regex("a{b").match("a{b")


def test_match_any_glob() -> None:
    assert match_any_glob("skills/engineering/tdd", ["skills/engineering/**"])
    assert not match_any_glob(
        "skills/in-progress/loop-me", ["skills/engineering/**", "skills/productivity/**"]
    )
    assert match_any_glob("docs/intro.md", ["docs/**"])
    assert not match_any_glob("gcc", ["docs/**", ".github/**"])
    assert match_any_glob("gcc", ["*"])
    assert not match_any_glob("x", [])


def test_glob_matches_everything() -> None:
    assert glob_matches_everything([])
    assert glob_matches_everything(None)
    assert glob_matches_everything(["*"])
    assert glob_matches_everything(["**", "**/*"])
    assert not glob_matches_everything(["docs/**"])
    assert not glob_matches_everything(["", "src/**"])
