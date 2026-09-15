# -*- coding: utf-8 -*-
"""
origin_verify —— 出图后确定性反读（与 origin_view_graph 互补的双保险）
=======================================================================

两层校验各司其职：

- origin_view_graph = 模型视觉自查（看渲染图：图型/配色/遮挡等主观项）；
- origin_verify_graph = 确定性反读（把 Origin 图页对象属性读回来逐项比对：
  图层/曲线数、轴标题文本、轴标题字号、图层几何、图例状态、交付文件完整性）。

每个检查项返回 {"name", "status", "detail", ...}，status ∈
pass / fail / warn / unreadable —— 读不到一律标 unreadable 并说明原因，
绝不因版本差异崩溃（失败项汇总进 issues，passed=False）。

LabTalk 读回采用「项目变量中转」：LT_execute 赋值 → originpro.lt_*/LT_get_var
读回，双通道兼容不同运行时；全部读回包 try/except。
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

    # 激活窗口（LabTalk layer/legend 对象读取依赖活动窗口）
    try:
        po.LT_execute(f"win -a {short};")
    except Exception:
        pass

    # 图层数
    nlay = _lt_float(op, po, "page.nlayers")
    if nlay is None:
        add("layer_count", "unreadable", "page.nlayers 不可读（版本差异，可忽略）")
    elif nlay >= 1:
        add("layer_count", "pass", f"图页共 {int(nlay)} 层", value=int(nlay))
    else:
        add("layer_count", "fail", f"page.nlayers={nlay} 异常", value=nlay)

    # 曲线数（与期望比对）
    try:
        nplot = len(gp[0].plot_list() or [])
    except Exception:
        nplot = None
    if nplot is None:
        add("series_count", "unreadable", "plot_list 不可读")
    else:
        exp_series = expected.get("series")
        if exp_series is not None and nplot != int(exp_series):
            add("series_count", "fail", f"图内 {nplot} 条曲线，期望 {exp_series}",
                value=nplot, expected=exp_series)
        elif nplot == 0:
            add("series_count", "warn", "图内没有曲线（空模板？）", value=0)
        else:
            add("series_count", "pass", f"图内 {nplot} 条曲线", value=nplot)

    # 轴标题文本：优先 GLayer.axis().title（真机验证可靠），回退 %X/%Y 寄存器
    for ax, key, reg in (("x", "x_title", "%X"), ("y", "y_title", "%Y")):
        text = None
        try:
            text = gp[0].axis(ax).title
        except Exception:
            text = None
        if text is None:
            text = _lt_str(op, po, reg)
        exp_v = expected.get(key)
        if text is None:
            add(f"axis_{ax}_title", "unreadable",
                "GLayer.axis 与 LabTalk 寄存器均读不到轴标题")
        else:
            t = str(text).strip()
            if exp_v is not None and t != str(exp_v).strip():
                add(f"axis_{ax}_title", "fail",
                    f"{ax} 轴标题={t!r}，期望 {exp_v!r}", value=t, expected=exp_v)
            elif not t:
                add(f"axis_{ax}_title", "warn", f"{ax} 轴标题为空", value="")
            else:
                add(f"axis_{ax}_title", "pass", f"{ax} 轴标题={t!r}", value=t)

    # 轴标题字号（xb=x 底部轴标题对象 / yl=y 左轴标题对象）
    min_pt = expected.get("min_font_pt")
    for obj, label in (("xb", "x_title_font_pt"), ("yl", "y_title_font_pt")):
        v = _lt_float(op, po, f"{obj}.fsize")
        if v is None:
            add(label, "unreadable", f"{obj}.fsize 不可读（版本差异，可忽略）")
        elif min_pt is not None and v < float(min_pt):
            add(label, "fail", f"字号 {v}pt 低于要求 {min_pt}pt",
                value=v, min_required=min_pt)
        else:
            add(label, "pass", f"字号 {v}pt" + (f"（要求 >= {min_pt}pt）" if min_pt else ""),
                value=v)

    # 图层几何（layer.left/top/right/bottom 为占页百分比 0~100）
    bounds = {prop: _lt_float(op, po, f"layer.{prop}")
              for prop in ("left", "top", "right", "bottom")}
    if any(v is None for v in bounds.values()):
        add("layer_bounds", "unreadable", "layer.left/top/right/bottom 部分不可读")
    else:
        inb = (0 <= bounds["left"] < bounds["right"] <= 100
               and 0 <= bounds["top"] < bounds["bottom"] <= 100)
        add("layer_bounds", "pass" if inb else "warn",
            f"图层占页百分比 {bounds}" + ("" if inb else "（越界/重叠，人工确认）"),
            **bounds)

    # 图例状态（legend.show；读不到标 unreadable）
    legend = _lt_float(op, po, "legend.show")
    if legend is None:
        add("legend", "unreadable", "legend.show 不可读（图例状态未核）")
    else:
        visible = legend > 0
        exp_leg = expected.get("legend_visible")
        if exp_leg is True and not visible:
            add("legend", "fail", "期望显示图例，但 legend.show=0", value=legend)
        elif exp_leg is False and visible:
            add("legend", "warn", "期望隐藏图例，但 legend.show=1（可在 OPJU 手动隐藏）",
                value=legend)
        else:
            add("legend", "pass", f"图例{'可见' if visible else '隐藏'}", value=legend)

    # 交付文件完整性
    for f in files or []:
        bn = os.path.basename(str(f))
        if os.path.exists(str(f)) and os.path.getsize(str(f)) > 0:
            add(f"file:{bn}", "pass", f"{bn} 存在且非空（{os.path.getsize(str(f))}B）")
        else:
            add(f"file:{bn}", "fail", f"{bn} 不存在或为空", path=str(f))

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_unread = sum(1 for c in checks if c["status"] == "unreadable")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    issues = [c for c in checks if c["status"] == "fail"]
    return oerr.ok(
        graph=short, checks=checks, issues=issues, passed=(n_fail == 0),
        n_pass=sum(1 for c in checks if c["status"] == "pass"),
        n_fail=n_fail, n_warn=n_warn, n_unreadable=n_unread,
        note="unreadable 项多为 Origin 版本差异，不判失败；fail 项需修复后复核",
        detail=f"确定性反读完成：{len(checks)} 项检查，{n_fail} fail / {n_warn} warn / {n_unread} unreadable")
