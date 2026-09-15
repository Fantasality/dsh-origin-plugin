# -*- coding: utf-8 -*-
"""
origin_verify —— 出图后确定性反读（COM 优先 + 激活复核 + 跨层统计）
====================================================================

两层校验各司其职：
- origin_view_graph = 模型视觉自查（看渲染图：图型/配色/遮挡等主观项）；
- origin_verify_graph = 确定性反读（把图页对象属性读回来逐项比对）。

**真机探针结论（smoke/labtalk_probe6，D1/D2 修复依据）**：
- COM 作用域通道（plot_list / Layers.Count / gl.get_int / set_int / axis 属性）
  与活动窗口**无关**，任何时候都可靠 → 本模块一律优先使用；
- LabTalk 裸表达式（xb.* / legend.* / layer.*）**只在目标图页恰好是活动窗口时
  才解析**，否则静默返回 NaN 或落到别的窗口 → 必须在 ensure_active_graph()
  复核通过后才使用，否则该项标 unreadable，**绝不判 fail**；
- 曲线数必须**跨全部图层**统计（多层图只用第 0 层会误报 0 条 → 曾经的"主动骗人"缺陷）。

每个检查项返回 {"name", "status", "detail", ...}，status ∈
pass / fail / warn / unreadable / unreliable —— 只有"上下文可信且与期望不符"
才判 fail；读不到或上下文不可信一律不判失败，并在 unclear 中单独列示。
"""
from __future__ import annotations

import os

import origin_errors as oerr


# ---------------------------------------------------------------------------
# LabTalk 读回（项目变量中转，双通道）
# ---------------------------------------------------------------------------
def _lt_float(op, po, expr):
    try:
        po.LT_execute(f"double __dshv = {expr};")
    except Exception:
        return None
    try:
        v = op.lt_float("__dshv")
        if v is not None:
            return float(v)
    except Exception:
        pass
    try:
        v = po.LT_get_var("__dshv")
        if v is not None:
            return float(v)
    except Exception:
        pass
    return None


def _lt_str(op, po, expr):
    try:
        po.LT_execute(f'string __dshvs$ = {expr};')
    except Exception:
        return None
    try:
        v = op.lt_str("__dshvs$")
        if v is not None:
            return str(v)
    except Exception:
        pass
    try:
        v = po.LT_get_str("__dshvs$")
        if isinstance(v, tuple) and v:
            v = v[0]
        if v is not None:
            return str(v)
    except Exception:
        pass
    return None


def _looks_like_placeholder(text):
    """LabTalk 未激活时 axis.title 会返回 '%(?X)' 之类的占位符。"""
    t = str(text or "")
    return "%(" in t and ")" in t


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def verify_graph(op, po, graph, expected=None, files=None):
    """对指定图页做确定性反读核验。

    Args:
        op: originpro 模块（引擎 _origin_app）。
        po: OriginExt ApplicationSI 对象（引擎 op.po）。
        graph: 图页短名（调用方已确认存在）。
        expected: 可选期望值 {x_title, y_title, min_font_pt, series, legend_visible}。
        files: 可选交付文件路径列表（核验存在且非空）。
    """
    expected = expected or {}
    checks = []

    def add(name, status, detail, **kw):
        item = {"name": name, "status": status, "detail": detail}
        item.update(kw)
        checks.append(item)

    gp = op.find_graph(graph)
    if gp is None:
        return oerr.fail("graph_not_found", f"图不存在: {graph}", graph=str(graph))
    short = str(gp.obj.GetName())
    add("graph_exists", "pass", f"图页 {short} 存在")

    # --- 激活复核（LabTalk 通道的前提） ---
    import origin_edit as oedit
    _, aerr = oedit.ensure_active_graph(op, po, short)
    activation_verified = aerr is None
    active_now = _lt_str(op, po, "page.name$")
    add("window_context",
        "pass" if activation_verified else "warn",
        (f"已激活并复核：{active_now!r}" if activation_verified
         else f"激活复核失败（当前活动窗口 {active_now!r}）→ LabTalk 类检查将标 unreadable，"
              "COM 类检查仍然可信"),
        activation_verified=activation_verified, active_window=active_now)

    # --- 图层数（COM，窗口无关） ---
    n_layers = None
    try:
        n_layers = int(gp.obj.Layers.Count)
    except Exception:
        n_layers = None
    if n_layers is None:
        add("layer_count", "unreadable", "无法读取 Layers.Count")
    elif n_layers >= 1:
        add("layer_count", "pass", f"图页共 {n_layers} 层", value=n_layers)
    else:
        add("layer_count", "fail", f"Layers.Count={n_layers} 异常", value=n_layers)

    # --- 曲线数：跨全部图层（COM，窗口无关） ---
    per_layer, total = [], 0
    read_ok = True
    layers = []
    try:
        for i in range(n_layers or 0):
            gl = gp.__getitem__(i)
            layers.append(gl)
            n = len(gl.plot_list() or [])
            per_layer.append(n)
            total += n
    except Exception as e:  # noqa: BLE001
        read_ok = False
        add("series_count", "unreadable", f"读取曲线失败: {e}")
    if read_ok:
        exp_series = expected.get("series")
        if exp_series is not None and total != int(exp_series):
            recount = None
            try:
                recount = sum(len(gp.__getitem__(i).plot_list() or [])
                              for i in range(n_layers or 0))
            except Exception:
                recount = None
            if recount is not None and recount == total:
                add("series_count", "fail",
                    f"图内共 {total} 条曲线（逐层 {per_layer}），期望 {exp_series}",
                    value=total, expected=exp_series, per_layer=per_layer)
            else:
                add("series_count", "unreliable",
                    f"两次统计不一致（{total} vs {recount}），不判失败",
                    value=total, recount=recount, per_layer=per_layer)
        elif total == 0:
            add("series_count", "warn", "图内没有曲线（空模板？）",
                value=0, per_layer=per_layer)
        else:
            add("series_count", "pass",
                f"图内共 {total} 条曲线（逐层 {per_layer}）",
                value=total, per_layer=per_layer)

    # --- 轴标题（COM setter/getter；未激活时会返回占位符） ---
    gl0 = layers[0] if layers else None
    for ax, key in (("x", "x_title"), ("y", "y_title")):
        text = None
        if gl0 is not None:
            try:
                text = gl0.axis(ax).title
            except Exception:
                text = None
        if text is None or _looks_like_placeholder(text):
            fallback = (_lt_str(op, po, "%X" if ax == "x" else "%Y")
                        if activation_verified else None)
            if fallback and not _looks_like_placeholder(fallback):
                text = fallback
            else:
                add(f"axis_{ax}_title", "unreadable",
                    ("未激活图页时 axis.title 返回占位符" if text is not None
                     else "无法读取轴标题"),
                    raw=str(text)[:40] if text is not None else None)
                continue
        t = str(text).strip()
        exp_v = expected.get(key)
        if exp_v is not None and t != str(exp_v).strip():
            add(f"axis_{ax}_title", "fail", f"{ax} 轴标题={t!r}，期望 {exp_v!r}",
                value=t, expected=exp_v)
        elif not t:
            add(f"axis_{ax}_title", "warn", f"{ax} 轴标题为空", value="")
        else:
            add(f"axis_{ax}_title", "pass", f"{ax} 轴标题={t!r}", value=t)

    # --- 轴标题字号：COM 作用域属性（窗口无关），比 LabTalk xb.fsize 可靠 ---
    min_pt = expected.get("min_font_pt")
    for ax, label in (("x", "x_title_font_pt"), ("y", "y_title_font_pt")):
        v = None
        if gl0 is not None:
            try:
                v = gl0.get_int(f"{ax}.label.fsize")
            except Exception:
                v = None
        if v is None:
            add(label, "unreadable",
                f"gl.get_int('{ax}.label.fsize') 不可读（版本差异，可忽略）")
        elif min_pt is not None and float(v) < float(min_pt):
            add(label, "fail", f"字号 {v}pt 低于要求 {min_pt}pt",
                value=v, min_required=min_pt)
        else:
            add(label, "pass",
                f"字号 {v}pt" + (f"（要求 >= {min_pt}pt）" if min_pt else ""), value=v)

    # --- 图层几何：COM 作用域读写（窗口无关；单位见 layer.unit） ---
    unit_label = {1: "%page", 2: "inch", 3: "cm", 4: "mm", 5: "pixel", 6: "point"}
    for i, gl in enumerate(layers):
        unit = None
        try:
            unit = gl.get_int("unit")
        except Exception:
            unit = None
        vals = {}
        for prop in ("left", "top", "width", "height"):
            v = None
            try:
                v = gl.get_float(prop)
            except Exception:
                v = None
            if v is None:
                try:
                    v = gl.get_int(prop)
                except Exception:
                    v = None
            vals[prop] = v
        if any(v is None for v in vals.values()):
            add(f"layer{i}_geometry", "unreadable", f"图层 {i} 几何部分不可读")
            continue
        ul = unit_label.get(int(unit or 1), f"unit={unit}")
        if int(unit or 1) == 1:      # %页：可判越界
            inb = (0 <= vals["left"] < 100 and 0 <= vals["top"] < 100
                   and vals["width"] > 0 and vals["height"] > 0
                   and vals["left"] + vals["width"] <= 101
                   and vals["top"] + vals["height"] <= 101)
            if not inb:
                add(f"layer{i}_geometry", "warn",
                    f"几何越界/重叠：left={vals['left']:.2f} top={vals['top']:.2f} "
                    f"w={vals['width']:.2f} h={vals['height']:.2f} ({ul})", **vals)
            else:
                add(f"layer{i}_geometry", "pass",
                    f"几何正常：left={vals['left']:.2f} top={vals['top']:.2f} "
                    f"w={vals['width']:.2f} h={vals['height']:.2f} ({ul})", **vals)
        else:
            add(f"layer{i}_geometry", "pass",
                f"几何 left={vals['left']:.3f} top={vals['top']:.3f} "
                f"w={vals['width']:.3f} h={vals['height']:.3f} ({ul})", **vals)

    # --- 图例（LabTalk，需激活复核） ---
    if not activation_verified:
        add("legend", "unreadable",
            "图例状态需 LabTalk（legend.show），当前激活未复核 → 不判失败")
    else:
        legend = _lt_float(op, po, "legend.show")
        if legend is None:
            add("legend", "unreadable", "legend.show 不可读")
        else:
            visible = legend > 0
            exp_leg = expected.get("legend_visible")
            if exp_leg is True and not visible:
                add("legend", "fail", "期望显示图例，但 legend.show=0", value=legend)
            elif exp_leg is False and visible:
                add("legend", "warn", "期望隐藏图例，但 legend.show=1", value=legend)
            else:
                add("legend", "pass", f"图例{'可见' if visible else '隐藏'}", value=legend)

    # --- 交付文件完整性 ---
    for f in files or []:
        bn = os.path.basename(str(f))
        if os.path.exists(str(f)) and os.path.getsize(str(f)) > 0:
            add(f"file:{bn}", "pass", f"{bn} 存在且非空（{os.path.getsize(str(f))}B）")
        else:
            add(f"file:{bn}", "fail", f"{bn} 不存在或为空", path=str(f))

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_unread = sum(1 for c in checks if c["status"] == "unreadable")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    n_unrel = sum(1 for c in checks if c["status"] == "unreliable")
    issues = [c for c in checks if c["status"] == "fail"]
    unclear = [c for c in checks if c["status"] in ("unreadable", "unreliable")]
    return oerr.ok(
        graph=short, checks=checks, issues=issues, unclear=unclear,
        passed=(n_fail == 0),
        n_pass=sum(1 for c in checks if c["status"] == "pass"),
        n_fail=n_fail, n_warn=n_warn, n_unreadable=n_unread, n_unreliable=n_unrel,
        context={"activation_verified": activation_verified,
                 "active_window": active_now,
                 "channels": "COM 作用域优先（窗口无关）；LabTalk 仅在激活复核通过后使用"},
        note=("只有 fail 项需要修复；unreadable/unreliable 是读不回来或上下文不可信，"
              "不代表图有问题"),
        detail=(f"确定性反读完成：{len(checks)} 项检查，{n_fail} fail / {n_warn} warn / "
                f"{n_unread} unreadable / {n_unrel} unreliable"))
