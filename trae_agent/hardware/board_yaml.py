# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""board-yaml.py — Embedded AI Studio 规范板级文件解析 → HardwareModel(自 embedded-ai-studio/core/hardware/board-yaml.js 移植)。

只解析规范子集:key: value / key:(缩进子块)/ “- ” 列表项(项为 map 首键值对,后续行同缩进延续)/ # 注释 / 值可带引号。
非通用 YAML(无锚点/flow/多文档),保持简单可控。
"""

import re

from trae_agent.hardware.model import empty_model


def _strip_comment(line: str) -> str:
    """行内注释剥离(引号内的 # 保留)。"""
    in_q: str | None = None
    for i, c in enumerate(line):
        if in_q:
            if c == in_q:
                in_q = None
            continue
        if c in ('"', "'"):
            in_q = c
        elif c == "#":
            return line[:i]
    return line


def _unquote(s: object) -> str:
    """去首尾引号。"""
    t = str(s if s is not None else "").strip()
    if len(t) >= 2 and ((t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'")):
        return t[1:-1]
    return t


def _find_colon(s: str) -> int:
    """找到引号外的第一个冒号;返回下标或 -1。"""
    in_q: str | None = None
    for i, c in enumerate(s):
        if in_q:
            if c == in_q:
                in_q = None
            continue
        if c in ('"', "'"):
            in_q = c
        elif c == ":":
            return i
    return -1


def _is_key_value_text(s: str) -> bool:
    """是不是 “key: value” 形态文本。"""
    return _find_colon(s) > 0


def _preprocess(text: object) -> list[dict]:
    """行预处理:去注释(引号内 # 不算)、去空白行、记录缩进。"""
    out: list[dict] = []
    raw = re.sub(r"\r\n?", "\n", str(text if text is not None else ""))
    lines = raw.split("\n")
    for i, line in enumerate(lines):
        no_comment = _strip_comment(line)
        if not no_comment.strip():
            continue
        out.append(
            {
                "indent": len(no_comment) - len(no_comment.lstrip(" ")),
                "text": no_comment.strip(),
                "line": i + 1,
            }
        )
    return out


class _Entry(dict):
    """缩进块 Entry:{ key, value, block, isListItem, scalar, line }。"""


def _parse_block(ctx: dict, i: int, indent: int) -> dict:
    """递归解析缩进块。

    块 = 若干 Entry:普通键值行 / “- ” 列表项(构造成 isListItem=True,block 为该条目 map 的键值对列表)。
    返回 { entries, next }。
    """
    entries: list[_Entry] = []
    lines: list[dict] = ctx["lines"]
    warn = ctx["warn"]
    while i < len(lines):
        entry_line = lines[i]
        if entry_line["indent"] < indent:
            break
        if entry_line["indent"] > indent:
            warn(f"第 {entry_line['line']} 行缩进过深,已并入上一级")
            i += 1
            continue
        if entry_line["text"].startswith("-"):
            r = _parse_dash_item(ctx, i, entry_line)
            entries.append(r["entry"])
            i = r["next"]
            continue
        # 普通键值行
        ci = _find_colon(entry_line["text"])
        if ci <= 0:
            warn(
                f'第 {entry_line["line"]} 行不是合法的 key: value 或列表项,已跳过:"{entry_line["text"][:40]}"'
            )
            i += 1
            continue
        key = _unquote(entry_line["text"][:ci])
        raw_value = entry_line["text"][ci + 1 :].strip()
        if raw_value != "":
            entries.append(
                _Entry(
                    key=key,
                    value=_unquote(raw_value),
                    block=None,
                    isListItem=False,
                    line=entry_line["line"],
                )
            )
            i += 1
            continue
        # key: 无值 → 子块(更深缩进)或同缩进 "-" 列表或空标量
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is not None and nxt["indent"] > entry_line["indent"]:
            child = _parse_block(ctx, i + 1, nxt["indent"])
            entries.append(
                _Entry(
                    key=key,
                    value=None,
                    block=child["entries"],
                    isListItem=False,
                    line=entry_line["line"],
                )
            )
            i = child["next"]
            continue
        if (
            nxt is not None
            and nxt["indent"] == entry_line["indent"]
            and nxt["text"].startswith("-")
        ):
            child = _parse_block(ctx, i + 1, entry_line["indent"])
            entries.append(
                _Entry(
                    key=key,
                    value=None,
                    block=child["entries"],
                    isListItem=False,
                    line=entry_line["line"],
                )
            )
            i = child["next"]
            continue
        entries.append(
            _Entry(key=key, value=None, block=None, isListItem=False, line=entry_line["line"])
        )
        warn(f'第 {entry_line["line"]} 行的字段 "{key}" 无值也无子块')
        i += 1
    return {"entries": entries, "next": i}


def _parse_dash_item(ctx: dict, i: int, entry_line: dict) -> dict:
    """解析一个 “- item” 列表项(dash 行内容可为空/裸标量/首键值对,后续同项行更深的缩进延续)。"""
    lines: list[dict] = ctx["lines"]
    text = entry_line["text"]
    content = "" if text == "-" else text[1:].strip()
    entry = _Entry(
        key=None, value=None, block=None, isListItem=True, scalar=None, line=entry_line["line"]
    )
    nxt = i + 1

    if content == "":
        # 内容完全依赖后续更深行
        deeper = lines[nxt] if nxt < len(lines) else None
        if deeper is not None and deeper["indent"] > entry_line["indent"]:
            child = _parse_block(ctx, nxt, deeper["indent"])
            entry["block"] = child["entries"]
            nxt = child["next"]
        return {"entry": entry, "next": nxt}
    if not _is_key_value_text(content):
        entry["scalar"] = _unquote(content)
        return {"entry": entry, "next": nxt}
    # 首键值对在 dash 行;该项的延续键值行缩进必须比 dash 更深
    map_entries: list[_Entry] = []
    first = _parse_kv_with_block(ctx, content, entry_line, nxt, entry_line["indent"])
    map_entries.append(first["entry"])
    nxt = first["next"]
    # 同一 item 的后续键值行(更深的缩进),直到遇到同级新 “- ” 项或更浅的行
    while (
        nxt < len(lines)
        and lines[nxt]["indent"] > entry_line["indent"]
        and not lines[nxt]["text"].startswith("-")
    ):
        cont = lines[nxt]
        if not _is_key_value_text(cont["text"]):
            warn = ctx["warn"]
            warn(f'第 {cont["line"]} 行不是合法的 key: value,已跳过:"{cont["text"][:40]}"')
            nxt += 1
            continue
        r = _parse_kv_with_block(ctx, cont["text"], cont, nxt + 1, cont["indent"])
        map_entries.append(r["entry"])
        # 防御:确保每轮至少前进一行,绝不无限循环
        nxt = max(r["next"], nxt + 1)
    entry["block"] = map_entries
    return {"entry": entry, "next": nxt}


def _parse_kv_with_block(
    ctx: dict, text: str, entry_line: dict, start_idx: int, parent_indent: int
) -> dict:
    """解析一行 “key: value”(或 “key:” + 子块)。

    start_idx:该行之后第一个待处理行;parent_indent:延续行必须比这个更深。
    """
    ci = _find_colon(text)
    key = _unquote(text[:ci])
    raw_value = text[ci + 1 :].strip()
    if raw_value != "":
        return {
            "entry": _Entry(
                key=key,
                value=_unquote(raw_value),
                block=None,
                isListItem=False,
                line=entry_line["line"],
            ),
            "next": start_idx,
        }
    lines: list[dict] = ctx["lines"]
    nxt = lines[start_idx] if start_idx < len(lines) else None
    if nxt is not None and nxt["indent"] > parent_indent:
        child = _parse_block(ctx, start_idx, nxt["indent"])
        return {
            "entry": _Entry(
                key=key,
                value=None,
                block=child["entries"],
                isListItem=False,
                line=entry_line["line"],
            ),
            "next": child["next"],
        }
    if nxt is not None and nxt["indent"] == parent_indent and nxt["text"].startswith("-"):
        child = _parse_block(ctx, start_idx, parent_indent)
        return {
            "entry": _Entry(
                key=key,
                value=None,
                block=child["entries"],
                isListItem=False,
                line=entry_line["line"],
            ),
            "next": child["next"],
        }
    ctx["warn"](f'第 {entry_line["line"]} 行的字段 "{key}" 无值也无子块')
    return {
        "entry": _Entry(key=key, value=None, block=None, isListItem=False, line=entry_line["line"]),
        "next": start_idx,
    }


def parse_board_yaml(text: object) -> dict:
    """parseBoardYaml(text) -> HardwareModel。

    顶层键:board / mcu(ref,part,package,pins 可选)/ components / nets。
    """
    model = empty_model("board-yaml")
    raw = str(text if text is not None else "")
    if not raw.strip():
        model["warnings"].append("board.yaml 文本为空")
        return model
    lines = _preprocess(raw)
    if len(lines) == 0:
        model["warnings"].append("board.yaml 只包含注释/空白行")
        return model

    def warn(msg: str) -> None:
        if len(model["warnings"]) < 50:
            model["warnings"].append(msg)

    ctx = {"lines": lines, "warn": warn}
    top = _parse_block(ctx, 0, 0)["entries"]
    _apply_top(top, model)
    return model


def _list_of(node: _Entry | None) -> list[_Entry]:
    """取 key 下的列表型子块(仅 isListItem 条目)。"""
    if node is None:
        return []
    block = node.get("block")
    if not block:
        return []
    return [e for e in block if e.get("isListItem")]


def _apply_top(top: list[_Entry], model: dict) -> None:
    """顶层语义映射到模型。"""
    warnings = model["warnings"]

    def get(key: str) -> _Entry | None:
        for n in top:
            if n.get("key") == key:
                return n
        return None

    board = get("board")
    if board is not None and board.get("value"):
        model["boardName"] = board["value"]
    else:
        warnings.append("缺少 board 字段:板名未知")

    # ---- mcu ----
    mcu_node = get("mcu")
    if mcu_node is not None and mcu_node.get("block"):
        m: dict[str, _Entry] = {}
        for e in mcu_node["block"]:
            if e.get("key") and not e.get("isListItem"):
                m[e["key"]] = e
        model["mcu"] = {
            "ref": m["ref"].get("value") if "ref" in m else None,
            "part": m["part"].get("value") if "part" in m else None,
            "package": m["package"].get("value") if "package" in m else None,
        }
        # mcu.pins:ref.part 形式(map)→ 生成/合并进 U1 组件
        if "pins" in m and m["pins"].get("block"):
            mcu_comp = _ensure_component(
                model, model["mcu"]["ref"] or "U1", model["mcu"]["part"] or ""
            )
            for p in m["pins"]["block"]:
                if p.get("key"):
                    mcu_comp["pins"].append({"pin": p["key"], "net": p.get("value")})
    elif mcu_node is not None and isinstance(mcu_node.get("value"), str):
        model["mcu"] = {"ref": None, "part": mcu_node["value"], "package": None}
    else:
        model["mcu"] = None
        warnings.append("缺少 mcu 段:外设推断将退化为启发式 MCU 搜索")

    # ---- components ----
    components_node = get("components")
    comp_items = _list_of(components_node)
    if len(comp_items) == 0:
        warnings.append("components: 段缺失或为空")
    for item in comp_items:
        comp = _item_to_component(item, model)
        if comp is not None:
            model["components"].append(comp)

    # ---- nets ----
    nets_node = get("nets")
    net_items = _list_of(nets_node)
    if nets_node is not None and len(net_items) == 0:
        warnings.append("nets: 段为空,网络连接表将由 components 的 pins 反向推导")
    for item in net_items:
        entries = item.get("block") or []
        name_node = next(
            (e for e in entries if e.get("key") == "name" and not e.get("isListItem")), None
        )
        name = name_node.get("value") if name_node is not None else None
        if not name:
            warnings.append(f"一条 nets 记录(行 {item.get('line', '?')})缺少 name,已跳过")
            continue
        entry: dict = {"name": name, "comment": None, "connections": []}
        comment_node = next(
            (e for e in entries if e.get("key") == "comment" and not e.get("isListItem")), None
        )
        if comment_node is not None and comment_node.get("value"):
            entry["comment"] = comment_node["value"]
        pins_node = next(
            (e for e in entries if e.get("key") == "pins" and not e.get("isListItem")), None
        )
        if pins_node is not None:
            pin_refs: list[str] = []
            if pins_node.get("block"):
                for p in pins_node["block"]:
                    if p.get("isListItem"):
                        if p.get("scalar") is not None:
                            pin_refs.append(p["scalar"])
                        elif p.get("block"):
                            # 非规范:列表项为键值 → 取第一个值
                            first = p["block"][0] if p["block"] else None
                            if first is not None and first.get("value") is not None:
                                pin_refs.append(first["value"])
                    elif p.get("key"):
                        pin_refs.append(p["key"])  # 容错:pinName: netName 形式时,key 本身是 REF.PIN
            for pr in pin_refs:
                parsed = _split_ref_pin(pr)
                if parsed is None:
                    warnings.append(f'nets "{name}" 的引脚引用 "{pr}" 不合法(应为 REF.PIN),已跳过')
                    continue
                entry["connections"].append(parsed)
        model["nets"].append(entry)

    # ---- 一致化 ----
    # nets 的连接补全 components 的 pins(双向一致)
    for net in model["nets"]:
        for conn in net["connections"]:
            comp = _ensure_component(model, conn["ref"], "")
            if not any(p["pin"] == conn["pin"] for p in comp["pins"]):
                comp["pins"].append({"pin": conn["pin"], "net": net["name"]})
    # components 的 pins(pinName → netName)反推生成缺失的 nets
    net_by_name = {n["name"]: n for n in model["nets"]}
    for comp in model["components"]:
        for p in comp["pins"]:
            if p.get("net") is None or p["net"] == "":
                continue
            net = net_by_name.get(p["net"])
            if net is None:
                net = {"name": p["net"], "connections": []}
                model["nets"].append(net)
                net_by_name[p["net"]] = net
            if not any(
                c["ref"] == comp["ref"] and c["pin"] == p["pin"] for c in net["connections"]
            ):
                net["connections"].append({"ref": comp["ref"], "pin": p["pin"]})
    # board.yaml 约定:网络名直接是 MCU 端口引脚名(PA5/PB0 等)时,该网络即连接 MCU 对应引脚
    if model.get("mcu") and model["mcu"].get("ref"):
        mcu_ref = model["mcu"]["ref"]
        mcu_comp = next((c for c in model["components"] if c["ref"] == mcu_ref), None)
        if mcu_comp is None:
            mcu_comp = {"ref": mcu_ref, "value": model["mcu"].get("part") or "", "pins": []}
            model["components"].append(mcu_comp)
        if not mcu_comp.get("value") and model["mcu"].get("part"):
            mcu_comp["value"] = model["mcu"]["part"]
        for net in model["nets"]:
            if not re.fullmatch(r"P[A-K]\d{1,2}", net["name"], re.I):
                continue
            pin_name = net["name"].upper()
            if not any(c["ref"] == mcu_ref for c in net["connections"]):
                net["connections"].append({"ref": mcu_ref, "pin": pin_name})
            if not any(str(p["pin"]).upper() == pin_name for p in mcu_comp["pins"]):
                mcu_comp["pins"].append({"pin": pin_name, "net": net["name"]})


def _ensure_component(model: dict, ref: str, value: str) -> dict:
    """取或创建组件(nets/mcu.pins 引用未声明器件时)。"""
    comp = next((c for c in model["components"] if c.get("ref") == ref), None)
    if comp is None:
        comp = {"ref": ref, "value": value or "", "pins": []}
        model["components"].append(comp)
        if value:
            model["warnings"].append(f"器件 {ref} 由 mcu/nets 声明创建(未出现在 components 列表)")
    if not comp.get("value") and value:
        comp["value"] = value
    return comp


def _item_to_component(item: _Entry, model: dict) -> dict | None:
    """列表项 → component { ref, value, type, pins:[{pin,net}] }。"""
    if item.get("scalar") is not None:
        model["warnings"].append(f'components 项 "{item["scalar"]}" 是裸字符串而非键值结构,已跳过')
        return None
    entries = item.get("block") or []
    m: dict[str, _Entry] = {}
    for e in entries:
        if e.get("key") and not e.get("isListItem"):
            m[e["key"]] = e
    ref = m["ref"].get("value") if "ref" in m else None
    if not ref:
        model["warnings"].append(f"一条 components 记录(行 {item.get('line', '?')})缺少 ref,已跳过")
        return None
    comp: dict = {"ref": ref, "value": "", "type": None, "pins": []}
    comp["value"] = m["value"].get("value") if "value" in m else ""
    if not comp["value"] and "name" in m and m["name"].get("value"):
        comp["value"] = m["name"]["value"]  # name 兼作 value 别名
    if "type" in m and m["type"].get("value"):
        comp["type"] = m["type"]["value"]
    if "footprint" in m and m["footprint"].get("value"):
        comp["footprint"] = m["footprint"]["value"]
    pins_node = m.get("pins")
    if pins_node is not None and pins_node.get("block"):
        for p in pins_node["block"]:
            if p.get("key"):
                comp["pins"].append({"pin": p["key"], "net": p.get("value")})
    elif pins_node is None:
        model["warnings"].append(f'components "{ref}" 没有 pins 映射')
    return comp


def _split_ref_pin(s: object) -> dict | None:
    """ "U1.PA5" → {ref:'U1', pin:'PA5'};失败返回 None。"""
    t = str(s if s is not None else "").strip()
    if not t:
        return None
    i = t.find(".")
    if i <= 0 or i >= len(t) - 1:
        return None
    return {"ref": t[:i], "pin": t[i + 1 :]}
