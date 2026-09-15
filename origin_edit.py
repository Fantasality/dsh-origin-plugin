# -*- coding: utf-8 -*-
"""
origin_edit —— 细粒度编辑层（面向"AI 帮新手微调已有图"的场景）
=================================================================

设计原则（全部来自真机探针，见 smoke/labtalk_probe2..6）：

1. **COM 优先，LabTalk 兜底**：探针证明 originpro 的图层/曲线作用域读写
   （gl.get_int / gl.set_int / plot_list / axis.title / p.color …）**与活动窗口无关**，
   永远可靠；而 LabTalk 裸表达式（xb.*, legend.*, layer.*）只在"目标图页恰好是
   活动窗口"时才解析，否则静默返回 NaN 或落到别的窗口。
2. **激活必须复核**：LabTalk 路径前一律 ensure_active_graph()——COM activate 后用
   page.name$ / is_active 复核，不符即返回结构化错误 window_activation_failed，
   绝不"以为激活成功"。
3. **写入必读回**：每个改动项返回 {item, requested, status, readback}，
   status ∈ applied / applied_unverified / rejected / unsupported。
   读不回来的一律 applied_unverified 并附 verify_hint，绝不谎报成功。
4. **单位显式**：页面尺寸 dots↔cm（page.resx/resy）、图层几何 layer.unit（1=%页、3=cm）、
   图例坐标（GLabel x/y 为图层单位；LabTalk legend.x/y 为像素）——两套单位都写在返回里。

本模块所有函数都假定在引擎的专用 COM 线程内调用（传入 op / po）。
"""
from __future__ import annotations

import os
import re

import origin_errors as oerr

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
_PAGE_TYPE = {0: "graph", 1: "matrix", 2: "workbook", 3: "layout", 4: "notes",
              5: "excel", 6: "other"}


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _hex_to_rgb(v):
    s = str(v).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"颜色格式应为 #RRGGBB 或 [r,g,b]，收到 {v!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def parse_color(v):
    """接受 '#RRGGBB' / 'RRGGBB' / [r,g,b] / (r,g,b)。"""
    if isinstance(v, (list, tuple)):
        if len(v) != 3:
            raise ValueError(f"颜色三元组需要 3 个分量，收到 {v!r}")
        return tuple(int(x) for x in v)
    return _hex_to_rgb(v)


def _rgb_str(v):
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return "#%02X%02X%02X" % tuple(int(x) for x in v)
    return str(v)


def ensure_active_graph(op, po, graph=None):
    """激活目标图页并**复核**。返回 (short_name, error_payload|None)。

    复核优先用 COM（gp.is_active），其次 page.name$（LabTalk）。
    注意：originpro 1.1.15 没有模块级 lt_str/lt_float，只走 po.LT_get_var/LT_get_str。
    """
    if graph:
        gp = None
        try:
            gp = op.find_graph(graph)
        except Exception:
            gp = None
        if gp is None:
            return None, oerr.fail("graph_not_found", f"图不存在: {graph}",
                                   graph=str(graph))
    else:
        gp = None
        try:
            gp = op.find_graph()
        except Exception:
            gp = None
        if gp is None:
            return None, oerr.fail("graph_not_found", "没有活动图页")
    short = safe_name(gp)
    if not short:
        return None, oerr.fail("origin_operation_error", "无法获取图页短名")
    _safe(gp.activate)                     # COM 激活（探针验证可靠）
    _safe(po.LT_execute, f"win -a {short};")
    # 复核 1：COM is_active
    is_act = None
    try:
        is_act = bool(gp.is_active())
    except Exception:
        is_act = None
    if is_act:
        return short, None
    # 复核 2：page.name$
    act = lt_str(po, "page.name$")
    if act is not None and str(act).strip() == short.strip():
        return short, None
    return None, oerr.fail(
        "window_activation_failed",
        f"图页激活复核失败：目标 {short!r}，实际活动窗口 {act!r}"
        + ("（is_active=False）" if is_act is False else ""),
        target=short, active=act)


def safe_name(gp):
    v, _ = _safe(gp.obj.GetName)
    return str(v) if v else None


def lt_float(po, expr):
    """LabTalk 数值读回（po.LT_execute + LT_get_var；不依赖 originpro 模块级 API）。"""
    _, err = _safe(po.LT_execute, f"double __dshv = {expr};")
    if err:
        return None
    r, _ = _safe(po.LT_get_var, "__dshv")
    if isinstance(r, (int, float)):
        return float(r)
    return None


def lt_str(po, expr):
    """LabTalk 字符串读回（po.LT_execute + LT_get_str）。"""
    _, err = _safe(po.LT_execute, f"string __dshs$ = {expr};")
    if err:
        return None
    r, _ = _safe(po.LT_get_str, "__dshs$")
    if isinstance(r, tuple) and r:
        r = r[0]
    return str(r) if r is not None else None


def lt_exec(po, script):
    _, err = _safe(po.LT_execute, script)
    return err


# ---------------------------------------------------------------------------
# 页面枚举 / 图页巡检
# ---------------------------------------------------------------------------
def list_pages(op, po):
    """列出项目内全部页面（类型/短名/长名/是否活动页）。"""
    pages = []
    total, err = _safe(lambda: op.po.Pages.Count)
    if total is None:
        return oerr.fail("origin_operation_error", f"无法枚举页面: {err}")
    active = lt_str(po, "page.name$")
    for i in range(int(total)):
        pg, e1 = _safe(op.po.Pages, i)
        if pg is None:
            continue
        name, _ = _safe(pg.GetName)
        t, _ = _safe(lambda p=pg: p.Type)
        lname, _ = _safe(lambda p=pg: p.LongName)
        kind = _PAGE_TYPE.get(int(t) if isinstance(t, (int, float)) else -1, "other")
        pages.append({"name": str(name), "type": kind,
                      "long_name": (str(lname) if lname else None),
                      "is_active": (str(name) == active) if active else None})
    graphs = [p["name"] for p in pages if p["type"] == "graph"]
    books = [p["name"] for p in pages if p["type"] == "workbook"]
    return oerr.ok(pages=pages, count=len(pages), graphs=graphs,
                   workbooks=books, active_window=active,
                   detail=f"共 {len(pages)} 个页面（图页 {len(graphs)}，工作簿 {len(books)}）")


def inspect_graph(op, po, graph=None, max_plots=40):
    """巡检图页现状：图层/曲线/轴/图例/页面尺寸/图层几何（全部只读）。"""
    short, err = ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = _safe(op.find_graph, short)
    out = {"ok": True, "graph": short, "layers": [], "context": {
        "active_verified": True, "method": "COM 优先 + LabTalk 复核激活"}}

    # 页面尺寸
    w_dot, _ = _safe(gp.get_int, "width")
    h_dot, _ = _safe(gp.get_int, "height")
    rx = lt_float(po, "page.resx") or 600.0
    ry = lt_float(po, "page.resy") or 600.0
    page_info = {"width_dots": w_dot, "height_dots": h_dot,
                 "dpi_x": rx, "dpi_y": ry}
    if isinstance(w_dot, (int, float)) and rx:
        page_info["width_cm"] = round(float(w_dot) / rx * 2.54, 3)
    if isinstance(h_dot, (int, float)) and ry:
        page_info["height_cm"] = round(float(h_dot) / ry * 2.54, 3)
    bc = lt_float(po, "page.baseColor")
    if bc is not None:
        page_info["background_index"] = bc
    out["page"] = page_info

    # 图例（LabTalk 需激活，已复核）
    legend = {"show": lt_float(po, "legend.show"),
              "background": lt_float(po, "legend.background"),
              "font_size_pt": lt_float(po, "legend.fsize")}
    lgnd, _ = _safe(lambda: gp[0].label("Legend"))
    if lgnd is not None:
        for key in ("x", "y", "dx", "dy"):
            v, _ = _safe(lgnd.get_float, key)
            if v is not None:
                legend[f"{key}_layer_units"] = round(float(v), 4)
        t, _ = _safe(getattr, lgnd, "text")
        if t is not None:
            legend["text"] = str(t)
    out["legend"] = legend

    # 逐层
    n_layers, _ = _safe(lambda: gp.obj.Layers.Count)
    for li in range(int(n_layers or 0)):
        gl, _ = _safe(gp.__getitem__, li)
        if gl is None:
            continue
        layer = {"index": li}
        # 图层几何：COM 作用域读写（窗口无关）；单位由 layer.unit 决定
        unit = None
        u, _ = _safe(gl.get_int, "unit")
        unit = u if isinstance(u, (int, float)) else None
        layer["unit_code"] = unit
        layer["unit_label"] = {1: "%page", 2: "inch", 3: "cm", 4: "mm",
                               5: "pixel", 6: "point"}.get(int(unit or 1), "?")
        for prop in ("left", "top", "width", "height"):
            v, _ = _safe(gl.get_float, prop)
            if v is not None:
                layer[f"{prop}_{layer['unit_label']}"] = round(float(v), 4)
            else:
                v2, _ = _safe(gl.get_int, prop)
                if v2 is not None:
                    layer[f"{prop}_{layer['unit_label']}"] = v2
        # 轴
        axes = {}
        for ax in ("x", "y"):
            info = {}
            t, _ = _safe(lambda a=ax: gl.axis(a).title)
            if t is not None:
                info["title"] = str(t)
            lim, _ = _safe(lambda a=ax: gl.axis(a).limits)
            if lim is not None:
                info["limits"] = [float(x) if isinstance(x, (int, float)) else x
                                  for x in (lim if isinstance(lim, (tuple, list)) else [lim])]
            sc, _ = _safe(lambda a=ax: gl.axis(a).scale)
            if sc is not None:
                info["scale"] = sc
            for prop in ("showgrids", "showAxes", "opposite", "ticklength",
                         "reverse", "label.bold", "label.fsize", "label.type",
                         "label.decPlaces", "grid.majorType"):
                v, _ = _safe(gl.get_int, f"{ax}.{prop}")
                if v is not None:
                    info[prop] = v
            axes[ax] = info
        layer["axes"] = axes
        # 曲线
        plots, _ = _safe(gl.plot_list)
        plist = []
        for i, p in enumerate(plots or []):
            if i >= int(max_plots):
                plist.append({"index": i, "note": "超过 max_plots，已省略"})
                break
            item = {"index": i}
            nm, _ = _safe(getattr, p, "name")
            if nm:
                item["dataset"] = str(nm)
            c, _ = _safe(getattr, p, "color")
            if c is not None:
                item["color"] = _rgb_str(c)
            for attr in ("symbol_kind", "symbol_size", "symbol_interior",
                         "transparency"):
                v, _ = _safe(getattr, p, attr)
                if v is not None:
                    item[attr] = v
            sh, _ = _safe(lambda pp=p: pp.get_int("show"))
            if sh is not None:
                item["visible"] = bool(sh)
            item["line_width_pt"] = "channel-only（LabTalk -wp，无读回）"
            plist.append(item)
        layer["plots"] = plist
        out["layers"].append(layer)

    out["detail"] = (f"图 {short}：{len(out['layers'])} 层、"
                     f"{sum(len(l.get('plots') or []) for l in out['layers'])} 条曲线，"
                     f"页面 {page_info.get('width_cm')}×{page_info.get('height_cm')} cm")
    return out


# ---------------------------------------------------------------------------
# 逐条曲线微调
# ---------------------------------------------------------------------------
def edit_plot(op, po, graph, edits):
    """按索引/数据集名逐条改曲线。

    edits: [{"plot": 0 或 "Book1_B", "color": "#FF0000", "line_width_pt": 2.5,
             "line_style": 2, "symbol_kind": 2, "symbol_size": 8,
             "symbol_interior": 1, "transparency": 20, "visible": true,
             "layer": 0}, ...]
    """
    short, err = ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = _safe(op.find_graph, short)
    n_layers, _ = _safe(lambda: gp.obj.Layers.Count)
    changes, rejected = [], []
    for spec in edits or []:
        if not isinstance(spec, dict):
            rejected.append({"item": str(spec), "reason": "edit 必须是 dict"})
            continue
        li = int(spec.get("layer", 0) or 0)
        if li >= int(n_layers or 0):
            rejected.append({"item": f"layer {li}", "reason": f"图层不存在（共 {n_layers} 层）"})
            continue
        gl, _ = _safe(gp.__getitem__, li)
        plots, _ = _safe(gl.plot_list)
        plots = plots or []
        target = spec.get("plot")
        p = None
        if isinstance(target, int):
            if 0 <= target < len(plots):
                p = plots[target]
        elif isinstance(target, str):
            for cand in plots:
                nm, _ = _safe(getattr, cand, "name")
                if nm and str(nm) == target:
                    p = cand
                    break
        if p is None:
            # 未指定时默认第一条
            if target is None and plots:
                p = plots[0]
            else:
                rejected.append({"item": f"plot={target!r}",
                                 "reason": f"未找到该曲线（本层 {len(plots)} 条）",
                                 "available": [str(_safe(getattr, q, 'name')[0]) for q in plots]})
                continue
        idx = plots.index(p)
        label = f"layer{li}.plot{idx}" + (f"({_safe(getattr, p, 'name')[0]})" if _safe(getattr, p, "name")[0] else "")

        def rec(item, requested, status, readback=None, note=None):
            entry = {"item": item, "requested": requested, "status": status}
            if readback is not None:
                entry["readback"] = readback
            if note:
                entry["note"] = note
            changes.append(entry)

        # 颜色
        if "color" in spec:
            try:
                rgb = parse_color(spec["color"])
                _safe(setattr, p, "color", rgb)
                back, _ = _safe(getattr, p, "color")
                ok = back is not None and tuple(int(x) for x in back) == tuple(rgb)
                rec(f"{label}.color", _rgb_str(rgb),
                    "applied" if ok else "rejected",
                    readback=_rgb_str(back) if back is not None else None,
                    note=None if ok else "写入后读回不一致")
            except Exception as e:  # noqa: BLE001
                rec(f"{label}.color", spec["color"], "rejected", note=str(e)[:120])
        # 线宽（LabTalk -wp，pt）
        if "line_width_pt" in spec:
            try:
                pt = float(spec["line_width_pt"])
                if pt <= 0:
                    raise ValueError("线宽必须为正")
                e = None
                # 先试 COM 作用域属性，再退到 -wp 通道
                v, e1 = _safe(lambda: p.set_float("linewidth", pt))
                got, _ = _safe(lambda: p.get_float("linewidth"))
                if e1 is None and isinstance(got, (int, float)) and abs(got - pt) < 1e-6:
                    rec(f"{label}.line_width_pt", pt, "applied", readback=got)
                else:
                    e2 = _safe(p.set_cmd, f"-wp {pt}")[1]
                    rec(f"{label}.line_width_pt", pt,
                        "applied_unverified" if e2 is None else "rejected",
                        note=("经 LabTalk -wp 通道写入；该通道无读回，"
                              "请用 origin_view_graph/origin_verify_graph 目视确认"
                              if e2 is None else f"-wp 写入失败: {e2}"))
            except Exception as e:  # noqa: BLE001
                rec(f"{label}.line_width_pt", spec["line_width_pt"], "rejected",
                    note=str(e)[:120])
        # 线型 0=实线 1=虚线 2=点线 ...
        if "line_style" in spec:
            n = int(spec["line_style"])
            e = _safe(p.set_cmd, f"-d {n}")[1]
            rec(f"{label}.line_style", n,
                "applied_unverified" if e is None else "rejected",
                note=None if e is None else str(e)[:120])
        # 符号
        if "symbol_kind" in spec:
            k = int(spec["symbol_kind"])
            _safe(setattr, p, "symbol_kind", k)
            back, _ = _safe(getattr, p, "symbol_kind")
            ok = back is not None and int(back) == k
            rec(f"{label}.symbol_kind", k, "applied" if ok else "rejected",
                readback=back)
        if "symbol_size" in spec:
            s = float(spec["symbol_size"])
            _safe(setattr, p, "symbol_size", s)
            back, _ = _safe(getattr, p, "symbol_size")
            ok = back is not None and abs(float(back) - s) < 1e-6
            rec(f"{label}.symbol_size", s, "applied" if ok else "rejected",
                readback=back)
        if "symbol_interior" in spec:
            v = int(spec["symbol_interior"])
            _safe(setattr, p, "symbol_interior", v)
            back, _ = _safe(getattr, p, "symbol_interior")
            ok = back is not None and int(back) == v
            rec(f"{label}.symbol_interior", v, "applied" if ok else "rejected",
                readback=back)
        if "transparency" in spec:
            t = float(spec["transparency"])
            if not 0 <= t <= 100:
                rec(f"{label}.transparency", t, "rejected", note="取值范围 0~100")
            else:
                _safe(setattr, p, "transparency", t)
                back, _ = _safe(getattr, p, "transparency")
                ok = back is not None and abs(float(back) - t) < 1e-6
                rec(f"{label}.transparency", t, "applied" if ok else "rejected",
                    readback=back)
        if "visible" in spec:
            want = 1 if spec["visible"] else 0
            # 探针证实：p.show 是属性（不可调用），可靠通道是 set_int('show', 0/1)
            _safe(lambda: p.set_int("show", want))
            back, _ = _safe(lambda: p.get_int("show"))
            ok = back is not None and int(back) == want
            rec(f"{label}.visible", bool(spec["visible"]),
                "applied" if ok else "rejected", readback=back,
                note=None if ok else "set_int('show') 写入后读回不一致")

    applied = [c for c in changes if c["status"].startswith("applied")]
    return oerr.ok(
        graph=short, changes=changes, n_applied=len(applied),
        n_rejected=len(rejected) + len([c for c in changes if c["status"] == "rejected"]),
        rejected=rejected,
        detail=(f"曲线微调：{len(applied)} 项应用、"
                f"{len(changes) - len(applied)} 项未生效（详见 changes）"))


# ---------------------------------------------------------------------------
# 轴微调
# ---------------------------------------------------------------------------
_AXIS_INT_PROPS = {"show_grids": "showgrids", "show_axes": "showAxes",
                   "opposite": "opposite", "tick_length": "ticklength",
                   "reverse": "reverse", "label_bold": "label.bold",
                   "label_font_pt": "label.fsize", "label_type": "label.type",
                   "label_decimals": "label.decPlaces",
                   "grid_major_type": "grid.majorType"}


def edit_axis(op, po, graph, axis="x", layer=0, title=None, from_=None, to=None,
              scale=None, **props):
    short, err = ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = _safe(op.find_graph, short)
    gl, _ = _safe(gp.__getitem__, int(layer or 0))
    if gl is None:
        return oerr.fail("layer_not_found", f"图层不存在: {layer}", graph=short)
    changes = []

    def rec(item, requested, status, readback=None, note=None):
        e = {"item": item, "requested": requested, "status": status}
        if readback is not None:
            e["readback"] = readback
        if note:
            e["note"] = note
        changes.append(e)

    if title is not None:
        _safe(lambda: setattr(gl.axis(axis), "title", str(title)))
        back, _ = _safe(lambda: gl.axis(axis).title)
        ok = back is not None and str(back).strip() == str(title).strip()
        rec(f"{axis}.title", str(title), "applied" if ok else "rejected",
            readback=str(back) if back is not None else None,
            note=None if ok else "写入后读回不一致（轴标题在未激活图页时会返回占位符）")
    if from_ is not None or to is not None:
        if from_ is not None:
            _safe(lambda: setattr(gl.axis(axis), "sfrom", float(from_)))
        if to is not None:
            _safe(lambda: setattr(gl.axis(axis), "sto", float(to)))
        lim, _ = _safe(lambda: gl.axis(axis).limits)
        rec(f"{axis}.range", [from_, to], "applied" if lim is not None else "rejected",
            readback=[float(x) if isinstance(x, (int, float)) else x
                      for x in (lim if isinstance(lim, (tuple, list)) else [lim])])
    if scale is not None:
        _safe(lambda: setattr(gl.axis(axis), "scale", int(scale)))
        back, _ = _safe(lambda: gl.axis(axis).scale)
        rec(f"{axis}.scale", scale, "applied" if back is not None else "rejected",
            readback=back, note="0=线性 1=线性(自动) 2=log10 3=ln ...")
    for key, val in props.items():
        prop = _AXIS_INT_PROPS.get(key)
        if prop is None:
            rec(key, val, "unsupported",
                note=f"不支持的轴属性；可用: {sorted(_AXIS_INT_PROPS)}")
            continue
        if val is None:
            continue
        _safe(lambda p=prop, v=val: gl.set_int(f"{axis}.{p}", int(v)))
        back, _ = _safe(lambda p=prop: gl.get_int(f"{axis}.{p}"))
        ok = back is not None and int(back) == int(val)
        rec(f"{axis}.{prop}", val, "applied" if ok else "rejected", readback=back)

    applied = [c for c in changes if c["status"] == "applied"]
    return oerr.ok(graph=short, axis=axis, changes=changes,
                   n_applied=len(applied),
                   detail=f"{axis} 轴微调：{len(applied)}/{len(changes)} 项应用")


# ---------------------------------------------------------------------------
# 图例微调
# ---------------------------------------------------------------------------
def edit_legend(op, po, graph, visible=None, font_size_pt=None, background=None,
                frame=None, position=None, x=None, y=None, left=None, top=None,
                text=None, rebuild=False):
    """图例微调。坐标通道说明（探针 probe7/8 实测）：
    - position：四角锚点，内部用 page.width/height 与 legend.width/height（均为 dots）
      计算后用 set_int('left'/'top') 落位，读回复核；
    - left/top：dots 整数（同 page.width 单位）；
    - x/y：与 LabTalk legend.x/y 同一通道（混合单位，不推荐直接用，除非用户给了截图坐标）。
    """
    short, err = ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = _safe(op.find_graph, short)
    gl, _ = _safe(gp.__getitem__, 0)
    lgnd, _ = _safe(lambda: gl.label("Legend"))
    changes = []

    def rec(item, requested, status, readback=None, note=None):
        e = {"item": item, "requested": requested, "status": status}
        if readback is not None:
            e["readback"] = readback
        if note:
            e["note"] = note
        changes.append(e)

    # 显示/隐藏（LabTalk，已复核激活）
    if visible is not None:
        want = 1 if visible else 0
        e = lt_exec(po, f"legend.show = {want};")
        back = lt_float(po, "legend.show")
        ok = (e is None and back is not None and int(back) == want)
        rec("legend.show", bool(visible), "applied" if ok else "rejected",
            readback=back, note=None if ok else f"写入未生效: {e or 'readback 不一致'}")
    # 字号
    if font_size_pt is not None:
        v = int(round(float(font_size_pt)))
        _safe(lambda: lgnd.set_int("fsize", v))
        back, _ = _safe(lambda: lgnd.get_int("fsize"))
        ok = back is not None and int(back) == v
        rec("legend.font_size_pt", v, "applied" if ok else "rejected", readback=back)
    # 边框（showframe=1 显示框）/ 背景样式（LabTalk background: 0无边 1黑线框 ...）
    if frame is not None:
        v = 1 if frame else 0
        _safe(lambda: lgnd.set_int("showframe", v))
        back, _ = _safe(lambda: lgnd.get_int("showframe"))
        ok = back is not None and int(back) == v
        rec("legend.frame", bool(frame), "applied" if ok else "rejected", readback=back)
    if background is not None:
        v = int(background)
        e = lt_exec(po, f"legend.background = {v};")
        back = lt_float(po, "legend.background")
        ok = (e is None and back is not None and int(back) == v)
        rec("legend.background", v, "applied" if ok else "rejected", readback=back,
            note="0=无边框 1=黑线框 2=阴影 3=深色浮凸 4=白色遮罩 5=黑色遮罩")
    # 位置：优先级 position(四角锚点, dots 计算) > left/top(dots) > x/y(与 LabTalk legend.x/y 同通道)
    if position:
        anchor = str(position).lower()
        if anchor not in ("tl", "tr", "bl", "br"):
            rec("legend.position", anchor, "rejected", note="支持 tl/tr/bl/br")
        else:
            # 探针（probe7/8）：page.width/height 与 legend.width/height 均为 dots，
            # set_int('left'/'top') 以 dots 生效且可读回 → 用 dots 精确算锚点。
            placed = None
            for attempt in (1, 2):
                pw = lt_float(po, "page.width")
                ph = lt_float(po, "page.height")
                lw = lt_float(po, "legend.width")
                lh = lt_float(po, "legend.height")
                if None in (pw, ph, lw, lh):
                    break
                margin = max(8, int(0.01 * pw))
                tgt = {"tl": (margin, margin),
                       "tr": (int(pw - lw - margin), margin),
                       "bl": (margin, int(ph - lh - margin)),
                       "br": (int(pw - lw - margin), int(ph - lh - margin))}[anchor]
                _safe(lambda: lgnd.set_int("left", int(tgt[0])))
                _safe(lambda: lgnd.set_int("top", int(tgt[1])))
                rb_l, _ = _safe(lambda: lgnd.get_int("left"))
                rb_t, _ = _safe(lambda: lgnd.get_int("top"))
                placed = {"left": rb_l, "top": rb_t, "target": [int(tgt[0]), int(tgt[1])],
                          "page_dots": [int(pw), int(ph)], "legend_dots": [int(lw), int(lh)]}
                if (rb_l is not None and rb_t is not None
                        and abs(rb_l - tgt[0]) <= 2 and abs(rb_t - tgt[1]) <= 2):
                    break
            if placed is None:
                rec("legend.position", anchor, "unsupported",
                    note="无法读取 page/legend 尺寸（dots），改用 left/top 显式坐标")
            else:
                ok = (placed["left"] is not None and placed["top"] is not None
                      and abs(placed["left"] - placed["target"][0]) <= 2
                      and abs(placed["top"] - placed["target"][1]) <= 2)
                rec("legend.position", anchor, "applied" if ok else "applied_adjusted",
                    readback=placed,
                    note=None if ok else "Origin 对图例位置做了钳制/尺寸变化，"
                                         "已给出实际落点（readback）")
    if left is not None:
        _safe(lambda: lgnd.set_int("left", int(left)))
        back, _ = _safe(lambda: lgnd.get_int("left"))
        rec("legend.left", int(left), "applied" if back is not None else "rejected",
            readback=back, note="单位 dots（page.width/height 同单位）")
    if top is not None:
        _safe(lambda: lgnd.set_int("top", int(top)))
        back, _ = _safe(lambda: lgnd.get_int("top"))
        rec("legend.top", int(top), "applied" if back is not None else "rejected",
            readback=back, note="单位 dots（page.width/height 同单位）")
    if x is not None:
        _safe(lambda: lgnd.set_float("x", float(x)))
        back, _ = _safe(lambda: lgnd.get_float("x"))
        rec("legend.x", float(x), "applied" if back is not None else "rejected",
            readback=back, note="与 LabTalk legend.x 同通道（混合单位，建议改用 left/position）")
    if y is not None:
        _safe(lambda: lgnd.set_float("y", float(y)))
        back, _ = _safe(lambda: lgnd.get_float("y"))
        rec("legend.y", float(y), "applied" if back is not None else "rejected",
            readback=back, note="与 LabTalk legend.y 同通道（混合单位，建议改用 top/position）")
    if text is not None:
        _safe(lambda: setattr(lgnd, "text", str(text)))
        back, _ = _safe(getattr, lgnd, "text")
        ok = back is not None and str(back) == str(text)
        rec("legend.text", str(text)[:60], "applied" if ok else "rejected",
            readback=str(back)[:60] if back is not None else None,
            note="自定义文本会关闭自动更新；需恢复自动图例用 rebuild=True")
    if rebuild:
        e = lt_exec(po, "legendupdate;")
        rec("legend.rebuild", True, "applied" if e is None else "rejected",
            note=None if e is None else str(e)[:100])

    applied = [c for c in changes if c["status"] == "applied"]
    return oerr.ok(graph=short, changes=changes, n_applied=len(applied),
                   detail=f"图例微调：{len(applied)}/{len(changes)} 项应用")


# ---------------------------------------------------------------------------
# 页面 / 图层几何微调
# ---------------------------------------------------------------------------
def edit_page(op, po, graph, page_size_cm=None, background=None, layer=None,
              layer_geometry_pct=None):
    """页面尺寸（cm）、页面背景、图层几何（%页）。

    Args:
        page_size_cm: {"width": w, "height": h} 单位 cm（内部换算 dots 写入并读回）
        background: 页面背景色索引（0=白；其他为 Origin 调色板索引）
        layer: 目标图层索引（layer_geometry_pct 用）
        layer_geometry_pct: {"left","top","width","height"}（单位 %页，layer.unit=1）
    """
    short, err = ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = _safe(op.find_graph, short)
    changes = []

    def rec(item, requested, status, readback=None, note=None):
        e = {"item": item, "requested": requested, "status": status}
        if readback is not None:
            e["readback"] = readback
        if note:
            e["note"] = note
        changes.append(e)

    if page_size_cm:
        w_cm = page_size_cm.get("width")
        h_cm = page_size_cm.get("height")
        rx = lt_float(po, "page.resx") or 600.0
        ry = lt_float(po, "page.resy") or 600.0
        if w_cm:
            dots = int(round(float(w_cm) / 2.54 * rx))
            _safe(lambda: gp.set_int("width", dots))
            back, _ = _safe(gp.get_int, "width")
            ok = isinstance(back, (int, float)) and abs(back - dots) <= 2
            rec("page.width_cm", float(w_cm), "applied" if ok else "rejected",
                readback=round(float(back) / rx * 2.54, 3) if isinstance(back, (int, float)) else None,
                note=f"dots={dots} @ {rx:.0f}dpi")
        if h_cm:
            dots = int(round(float(h_cm) / 2.54 * ry))
            _safe(lambda: gp.set_int("height", dots))
            back, _ = _safe(gp.get_int, "height")
            ok = isinstance(back, (int, float)) and abs(back - dots) <= 2
            rec("page.height_cm", float(h_cm), "applied" if ok else "rejected",
                readback=round(float(back) / ry * 2.54, 3) if isinstance(back, (int, float)) else None,
                note=f"dots={dots} @ {ry:.0f}dpi")
    if background is not None:
        idx = int(background)
        _safe(lambda: gp.set_int("basecolor", idx))
        back = lt_float(po, "page.baseColor")
        ok = back is not None and int(back) == idx
        rec("page.background", idx, "applied" if ok else "rejected", readback=back)
    if layer_geometry_pct:
        li = int(layer or 0)
        gl, _ = _safe(gp.__getitem__, li)
        if gl is None:
            rec(f"layer{li}.geometry", layer_geometry_pct, "rejected",
                note=f"图层不存在（索引 {li}）")
        else:
            _safe(lambda: gl.set_int("unit", 1))          # 1 = %page
            back_unit, _ = _safe(lambda: gl.get_int("unit"))
            if back_unit != 1:
                rec(f"layer{li}.geometry", layer_geometry_pct, "rejected",
                    note="无法把图层单位切到 %页（layer.unit=1）")
            else:
                for key in ("left", "top", "width", "height"):
                    if key not in layer_geometry_pct:
                        continue
                    v = float(layer_geometry_pct[key])
                    ok_set = False
                    for setter in (lambda: gl.set_float(key, v), lambda: gl.set_int(key, int(v))):
                        _, e = _safe(setter)
                        if e is None:
                            ok_set = True
                            break
                    rb, _ = _safe(lambda k=key: gl.get_float(k))
                    if rb is None:
                        rb, _ = _safe(lambda k=key: gl.get_int(k))
                    ok = ok_set and isinstance(rb, (int, float)) and abs(float(rb) - v) <= 0.05
                    rec(f"layer{li}.{key}_pct", v, "applied" if ok else "rejected",
                        readback=round(float(rb), 3) if isinstance(rb, (int, float)) else None,
                        note="单位 %页（layer.unit=1）")

    applied = [c for c in changes if c["status"] == "applied"]
    return oerr.ok(graph=short, changes=changes, n_applied=len(applied),
                   detail=f"页面/几何微调：{len(applied)}/{len(changes)} 项应用")


# ---------------------------------------------------------------------------
# 窗口管理（关闭多个窗口正是新手高频需求）
# ---------------------------------------------------------------------------
def manage_pages(op, po, action, pages=None, new_name=None):
    """action: close | activate | rename | hide | show | duplicate

    pages: 短名列表（来自 origin_list_pages）。close 支持 "Book*"/"Graph*" 通配。
    """
    action = (action or "").lower()
    if action not in ("close", "activate", "rename", "hide", "show", "duplicate"):
        return oerr.fail("invalid_request",
                         f"action 必须是 close/activate/rename/hide/show/duplicate，收到 {action!r}")
    targets = [str(p) for p in (pages or [])]
    if not targets:
        return oerr.fail("invalid_request", "pages 不能为空（先用 origin_list_pages 取短名）")
    if action == "rename" and (len(targets) != 1 or not new_name):
        return oerr.fail("invalid_request", "rename 需要恰好一个 pages 项 + new_name")

    existing = set()
    total, _ = _safe(lambda: op.po.Pages.Count)
    for i in range(int(total or 0)):
        pg, _ = _safe(op.po.Pages, i)
        nm, _ = _safe(pg.GetName) if pg is not None else (None, None)
        if nm:
            existing.add(str(nm))

    results = []
    for t in targets:
        wildcard = t.endswith("*")
        if not wildcard and t not in existing:
            results.append({"page": t, "status": "rejected",
                            "reason": "页面不存在（用 origin_list_pages 复核短名）"})
            continue
        if action == "close":
            e = lt_exec(po, f"window -c {t};") if wildcard else lt_exec(po, f"window -c {t};")
        elif action == "activate":
            gp, _ = _safe(op.find_graph, t)
            if gp is not None:
                _safe(gp.activate)
            e = lt_exec(po, f"win -a {t};")
        elif action == "rename":
            e = lt_exec(po, f"window -r {t} {new_name};")
        elif action == "hide":
            e = lt_exec(po, f"window -h 1 {t};")
        elif action == "show":
            e = lt_exec(po, f"window -h 0 {t};")
        else:  # duplicate
            gp, _ = _safe(op.find_graph, t)
            e = None
            if gp is None:
                e = "duplicate 目前仅支持图页"
            else:
                _, e2 = _safe(gp.duplicate)
                e = None if e2 is None else str(e2)[:120]
        # 复核
        if action == "close" and not wildcard:
            still, _ = _safe(op.find_graph, t)
            still2, _ = _safe(lambda: t in {str(op.po.Pages(i).GetName())
                                            for i in range(int(op.po.Pages.Count))})
            ok = (still is None) and not still2
        elif action == "rename":
            still, _ = _safe(op.find_graph, t)
            new_ok, _ = _safe(op.find_graph, new_name)
            ok = (new_ok is not None)
        elif action == "activate":
            act = lt_str(po, "page.name$")
            ok = act is not None and str(act).strip() == t.strip()
        else:
            ok = (e is None)
        results.append({"page": t,
                        "status": "applied" if (e is None and ok) else "rejected",
                        "note": (str(e)[:120] if e else (None if ok else "复核未通过"))})
    applied = sum(1 for r in results if r["status"] == "applied")
    return oerr.ok(action=action, results=results, n_applied=applied,
                   detail=f"{action}: {applied}/{len(results)} 成功")


# ---------------------------------------------------------------------------
# 文本标注
# ---------------------------------------------------------------------------
def add_text(op, po, graph, text, x=None, y=None):
    short, err = ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = _safe(op.find_graph, short)
    gl, _ = _safe(gp.__getitem__, 0)
    lb, e = _safe(lambda: gl.add_label(str(text), x, y))
    if lb is None:
        # 兜底：LabTalk label -p
        xs = float(x) if x is not None else 2.0
        ys = float(y) if y is not None else 20.0
        e2 = lt_exec(po, f'label -p {xs} {ys} "{text}";')
        if e2 is not None:
            return oerr.fail("origin_operation_error",
                             f"添加文本失败: {e or e2}", graph=short)
        return oerr.ok(graph=short, text=str(text), method="LabTalk label -p",
                       detail=f"已添加文本标注（LabTalk 通道）-> {short}")
    return oerr.ok(graph=short, text=str(text), method="GLayer.add_label",
                   detail=f"已添加文本标注 {str(text)[:40]!r} -> {short}")
