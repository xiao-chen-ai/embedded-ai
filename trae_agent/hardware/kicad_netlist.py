# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""kicad-netlist.py — KiCad 6/7/8 S 表达式网表解析 → HardwareModel(自 embedded-ai-studio/core/hardware/kicad-netlist.js 移植)。

处理真实 export 结构:字段可乱序可缺失、node 子字段可选;
网名可含中文、/、-、() 等字符(直接取引号字符串原文)。
"""

from trae_agent.hardware.model import empty_model
from trae_agent.hardware.sexp import (
    parse_sexp,
    sexp_child,
    sexp_children,
    sexp_head,
    sexp_tagged_value,
)


def parse_kicad_netlist(text: object) -> dict:
    """parseKiCadNetlist(text) -> HardwareModel。

    健壮性:空文本 / 只有注释 / 截断的 S 表达式 → 不抛异常,返回尽力解析的部分模型 + 中文警告。
    """
    model = empty_model("kicad")
    src = str(text if text is not None else "")
    if not src.strip():
        model["warnings"].append("网表文本为空")
        return model
    root, warnings = parse_sexp(src)
    model["warnings"].extend(warnings)
    if root is None:
        model["warnings"].append("未找到有效的 S 表达式(应以 (export ...) 开头)")
        return model
    head = sexp_head(root)
    if head and head.lower() != "export":
        model["warnings"].append(f"顶层表达式是 ({head}),预期为 (export ...)")

    # boardName:KiCad 网表本身不含板名(source 是原理图文件名),保持 None 由上层覆盖
    design = sexp_child(root, "design")
    if design is not None:
        src_file = sexp_tagged_value(design, "source")
        if src_file:
            model["boardName"] = None  # 不从 design.source 推断板名

    components = sexp_child(root, "components")
    if components is not None:
        for comp in sexp_children(components, "comp"):
            _parse_comp(comp, model)
    else:
        model["warnings"].append("网表缺少 (components ...) 段")

    nets = sexp_child(root, "nets")
    if nets is not None:
        for net in sexp_children(nets, "net"):
            _parse_net(net, model)
    else:
        model["warnings"].append("网表缺少 (nets ...) 段")

    _reconcile(model)
    return model


def _parse_comp(comp: list, model: dict) -> None:
    """解析单个 (comp ...) 节点:字段乱序/缺失均可。"""
    ref = sexp_tagged_value(comp, "ref") or ""
    if not ref:
        model["warnings"].append("一条 component 记录缺少 ref,已跳过")
        return
    item: dict = {
        "ref": ref,
        "value": sexp_tagged_value(comp, "value") or "",
        "footprint": sexp_tagged_value(comp, "footprint"),
        "type": None,
        "pins": [],
    }
    # libsource.part 作为 value 的补充(部分网表缺 value 字段)
    lib = sexp_child(comp, "libsource")
    if lib is not None:
        part = sexp_tagged_value(lib, "part")
        if not item["value"] and part:
            item["value"] = part
    model["components"].append(item)


def _parse_net(net: list, model: dict) -> None:
    """解析单个 (net ...) 节点。"""
    name = sexp_tagged_value(net, "name")
    code = sexp_tagged_value(net, "code")
    if name is None:
        # 无名网络:KiCad 真实输出必有 name;缺失时回退用 code 构造
        model["warnings"].append(
            f"网络 code={code if code is not None else '?'} 缺少 name,回退命名为 N{code if code is not None else len(model['nets']) + 1}"
        )
    net_name = (
        name if name is not None else f"N{code if code is not None else len(model['nets']) + 1}"
    )
    entry: dict = {"name": net_name, "connections": []}
    for node in sexp_children(net, "node"):
        nref = sexp_tagged_value(node, "ref") or ""
        npin = sexp_tagged_value(node, "pin") or ""
        if not nref or not npin:
            model["warnings"].append(f'网络 "{net_name}" 的一条 node 记录缺少 ref/pin,已跳过')
            continue
        # pinfunction / pintype / order / tstamp 均为可选字段,分析用不到
        entry["connections"].append({"ref": nref, "pin": npin})
    model["nets"].append(entry)


def _reconcile(model: dict) -> None:
    """一致化:components[].pins 通常为空(KiCad 连接信息都在 nets 里),

    这里把“某器件有哪些引脚”从 nets 反填到 components,
    使 peripherals.py 能看到每个器件的引脚数(用于 MCU 兜底选择与引脚冲突检测)。
    """
    comp_by_ref = {c.get("ref"): c for c in model["components"]}
    for net in model["nets"]:
        for conn in net["connections"]:
            comp = comp_by_ref.get(conn["ref"])
            if comp is None:
                # node 引用了未在 components 声明的器件:自动创建(部分网表省略无源器件)
                auto = {"ref": conn["ref"], "value": "", "pins": []}
                model["components"].append(auto)
                comp_by_ref[conn["ref"]] = auto
                model["warnings"].append(
                    f"网络引用了 components 中未声明的器件 {conn['ref']},已自动创建"
                )
    # 按 ref 去重(components 重复时保留第一个,合并 value)
    seen: set[str] = set()
    dedup: list[dict] = []
    for c in model["components"]:
        if c.get("ref") in seen:
            model["warnings"].append(f"重复的器件位号 {c.get('ref')},保留第一个(如有 value 则合并)")
            first = next((x for x in dedup if x.get("ref") == c.get("ref")), None)
            if first is not None and not first.get("value") and c.get("value"):
                first["value"] = c["value"]
            continue
        seen.add(c.get("ref"))
        dedup.append(c)
    model["components"] = dedup

    # 从 nets 连接反填 components[].pins(pin + 所在网络名)
    for c in model["components"]:
        c["pins"] = []
    for net in model["nets"]:
        for conn in net["connections"]:
            comp = comp_by_ref.get(conn["ref"])
            if comp is None:
                continue
            if not any(p["pin"] == conn["pin"] for p in comp["pins"]):
                comp["pins"].append({"pin": conn["pin"], "net": net["name"]})
