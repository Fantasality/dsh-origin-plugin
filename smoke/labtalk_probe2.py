# -*- coding: utf-8 -*-
"""
labtalk_probe2 —— 细粒度编辑能力真机探针（只读优先，写入必读回）
====================================================================

目的：在真实 Origin 上验证"微调"所需的全部属性/方法名，输出 JSON 报告。
只有探针证明可用的属性，才会进入 origin_edit.py 的实现（沿用"未验证不落笔"原则）。

覆盖：
- 页面枚举与类型识别（图页/工作簿/矩阵）、关闭/重命名/激活
- 图层的 plot_list 与逐条曲线的颜色/线宽/线型/符号/透明度
- 图层几何（layer.unit / left / top / width / height）与单位换算
- 轴对象（layer.x/y: from/to/type/reverse/grids/ticks/字体）
- 图例对象（legend.show/background/fsize/x/y/left/top/text$）
- 页面对象（page.width/height/resx/resy）+ GPage.set_int 写入与读回

用法（需 Origin 运行）:
    <venv python> -X utf8 smoke\labtalk_probe2.py [输出json路径]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT = {"read": {}, "write": {}, "api": {}, "errors": []}


def rec(section, key, value):
    REPORT[section][key] = value


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    import originpro as op
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import origin_engine as engine

    ok, conn = engine._connect_impl()
    rec("api", "connect_ok", ok)
    if not ok:
        rec("errors", "connect", str(conn)[:300])
        return
    po = op.po

    def lt_read(expr):
        v, err = safe(engine._lt_read_float, expr)
        if v is not None:
            return v
        s, err2 = safe(engine._lt_read_str, expr)
        return s

    def lt_write(expr):
        return safe(po.LT_execute, expr)

    # ---------------- 0. 页面枚举与类型识别 ----------------
    try:
        n_pages = po.Pages.Count
        pages = []
        for i in range(n_pages):
            pg = po.Pages(i)
            item = {"name": safe(pg.GetName)[0]}
            for attr in ("Type", "IsGraph", "IsWorkbook", "IsMatrix", "LongName"):
                v, err = safe(getattr, pg, attr)
                if err is None:
                    try:
                        item[attr] = pg.__getattribute__(attr)() if callable(v) else v
                    except Exception:
                        item[attr] = repr(v)[:40]
            pages.append(item)
        rec("api", "pages", pages)
        rec("api", "pages_count", n_pages)
    except Exception as e:
        rec("errors", "pages", str(e))

    # ---------------- 1. 建测试数据 + 双序列图 ----------------
    w = op.new_sheet("w")
    w.from_list(0, [1.0, 2, 3, 4, 5], lname="x")
    w.from_list(1, [1.0, 4, 9, 16, 25], lname="alpha")
    w.from_list(2, [2.0, 3, 5, 7, 11], lname="beta")
    gp = op.new_graph(lname="ProbeEdit")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="l")
    gl.rescale()
    short = gp.obj.GetName()
    rec("api", "probe_graph", short)
    po.LT_execute(f"win -a {short};")

    # ---------------- 2. plot_list / Plot 属性 ----------------
    plots, err = safe(gl.plot_list)
    rec("api", "plot_list_len", len(plots) if plots else err)
    if plots:
        p0 = plots[0]
        rec("api", "plot_dir", [a for a in dir(p0) if not a.startswith("_")])
        for attr in ("color", "linewidth", "symbol_kind", "symbol_size",
                     "transparency", "line_style", "connect", "dataset"):
            v, e = safe(getattr, p0, attr)
            rec("read", f"plot.{attr}", repr(v)[:60] if e is None else f"ERR {e}")
        # 写入探针：颜色 / 线宽 / 符号 / 透明度（读回验证）
        p0.color = (255, 0, 0)
        v, e = safe(getattr, p0, "color")
        rec("write", "plot.color=<255,0,0> readback", repr(v)[:60] if e is None else f"ERR {e}")
        for attr, val in (("linewidth", 3.0), ("symbol_kind", 2), ("symbol_size", 9)):
            v0, e0 = safe(getattr, p0, attr)
            setattr_res, e1 = safe(setattr, p0, attr, val)
            v1, e2 = safe(getattr, p0, attr)
            rec("write", f"plot.{attr}", {
                "before": repr(v0)[:40] if e0 is None else None,
                "set_err": str(e1)[:80] if e1 else None,
                "after": repr(v1)[:40] if e2 is None else None})
        t, et = safe(getattr, p0, "transparency")
        if et is None:
            e_t = safe(setattr, p0, "transparency", 30)
            t2, _ = safe(getattr, p0, "transparency")
            rec("write", "plot.transparency", {"before": t, "after": t2})
        else:
            rec("write", "plot.transparency", f"attr missing: {et}")
        # set_cmd 通道（LabTalk 兜底）
        sc, e = safe(p0.set_cmd, "-w 2000", "-d 1")
        rec("write", "plot.set_cmd('-w 2000','-d 1')", "ok" if e is None else str(e)[:80])
        v, _ = safe(getattr, p0, "linewidth")
        rec("write", "plot.linewidth after -w 2000", repr(v)[:40])

    # ---------------- 3. 图层几何与单位 ----------------
    for expr in ("layer.unit", "layer.left", "layer.top", "layer.width",
                 "layer.height", "layer.color", "layer.border",
                 "layer.x.from", "layer.x.to", "layer.x.type", "layer.x.reverse",
                 "layer.x.showAxes", "layer.x.showGrids", "layer.x.ticklength",
                 "layer.x.label.fsize", "layer.x.label.bold", "layer.x.label.font",
                 "layer.x.label.color", "layer.x.label.type", "layer.x.label.decPlaces",
                 "layer.y.label.fsize", "layer.y2.type", "layer.plot",
                 "page.width", "page.height", "page.resx", "page.resy",
                 "legend.show", "legend.background", "legend.fsize",
                 "legend.x", "legend.y", "page.nlayers", "page.active"):
        rec("read", f"LT:{expr}", lt_read(expr))
    rec("read", "LT:legend.text$", lt_read("legend.text$"))

    # 写入探针：图例字号 / 轴范围 / 轴刻度字号 / 图层几何（cm）
    lt_write("legend.fsize = 16;")
    rec("write", "legend.fsize=16 readback", lt_read("legend.fsize"))
    lt_write("layer.x.from = 1.5; layer.x.to = 4.5;")
    rec("write", "layer.x.from/to=1.5/4.5 readback",
        [lt_read("layer.x.from"), lt_read("layer.x.to")])
    lt_write("layer.x.label.fsize = 14;")
    rec("write", "layer.x.label.fsize=14 readback", lt_read("layer.x.label.fsize"))
    lt_write("layer.x.reverse = 1;")
    rec("write", "layer.x.reverse=1 readback", lt_read("layer.x.reverse"))
    lt_write("layer.x.reverse = 0;")
    lt_write("legend.x = 300; legend.y = 120;")
    rec("write", "legend.x/y readback", [lt_read("legend.x"), lt_read("legend.y")])
    lt_write("legend.background = 1;")
    rec("write", "legend.background=1 readback", lt_read("legend.background"))
    # 图层几何：切 cm 单位写宽高
    old_unit = lt_read("layer.unit")
    lt_write("layer.unit = 3;")     # cm
    lt_write("layer.width = 8; layer.height = 6;")
    rec("write", "layer cm geometry", {
        "unit_before": old_unit,
        "width": lt_read("layer.width"), "height": lt_read("layer.height"),
        "left": lt_read("layer.left"), "top": lt_read("layer.top")})
    lt_write(f"layer.unit = {int(old_unit) if old_unit else 1};")

    # 页面尺寸：dots + dpi 换算
    pw, ph = lt_read("page.width"), lt_read("page.height")
    rx, ry = lt_read("page.resx"), lt_read("page.resy")
    rec("read", "page size(dots)+dpi", {"w": pw, "h": ph, "resx": rx, "resy": ry,
                                        "w_in": (pw / rx) if (pw and rx) else None})
    gpage_set, e = safe(gp.set_int, "width", int(pw or 1000) + 200)
    v, _ = safe(gp.get_int, "width")
    rec("write", "GPage.set_int('width')", {"err": str(e)[:80] if e else None,
                                            "readback": v})

    # ---------------- 4. 图例对象（originpro GLabel 通道） ----------------
    lgnd, e = safe(gl.label, "Legend")
    rec("api", "gl.label('Legend')", "ok" if lgnd is not None and not e else str(e)[:100])
    if lgnd is not None:
        rec("api", "label_dir", [a for a in dir(lgnd) if not a.startswith("_")])
        for key, val, cast in (("fsize", 18, "int"), ("showframe", 0, "int"),
                               ("left", 1200, "int"), ("top", 800, "int")):
            fn = lgnd.set_float if cast == "float" else lgnd.set_int
            rf = lgnd.get_float if cast == "float" else lgnd.get_int
            _, e1 = safe(fn, key, val)
            v, e2 = safe(rf, key)
            rec("write", f"legend.{key}", {"set_err": str(e1)[:60] if e1 else None,
                                           "readback": v if e2 is None else str(e2)[:60]})
        # 位置与尺寸读回
        for key in ("x", "y", "dx", "dy"):
            v, e3 = safe(lgnd.get_float, key)
            rec("read", f"legend.{key}", v if e3 is None else str(e3)[:60])
        txt, e4 = safe(getattr, lgnd, "text")
        rec("read", "legend.text(get)", repr(txt)[:80] if e4 is None else str(e4)[:60])

    # ---------------- 5. 轴对象（originpro Axis 通道） ----------------
    ax, e = safe(gl.axis, "x")
    rec("api", "axis_dir", [a for a in dir(ax) if not a.startswith("_")] if ax is not None else str(e)[:80])
    if ax is not None:
        for attr, val in (("sfrom", 1.2), ("sto", 4.8), ("scale", 1), ("title", "Probe X")):
            _, e1 = safe(setattr, ax, attr, val)
            v, e2 = safe(getattr, ax, attr) if attr != "set_limits" else (None, None)
            rec("write", f"axis.x.{attr}", {"set_err": str(e1)[:60] if e1 else None,
                                            "readback": repr(v)[:40] if e2 is None else str(e2)[:60]})
        v, _ = safe(getattr, ax, "limits")
        rec("read", "axis.x.limits", repr(v)[:60])
        v, _ = safe(getattr, ax, "title")
        rec("read", "axis.x.title(get)", repr(v)[:60])

    # ---------------- 6. 图层枚举 / 增删 ----------------
    rec("read", "LT:page.nlayers(after)", lt_read("page.nlayers"))
    gl2, e2 = safe(gp.add_layer)
    rec("api", "GPage.add_layer", "ok" if gl2 is not None else str(e2)[:100])
    rec("read", "LT:page.nlayers(after add)", lt_read("page.nlayers"))

    # ---------------- 7. 窗口管理（关闭/重命名/激活） ----------------
    tmp_gp = op.new_graph(lname="ProbeTmpClose")
    tmp_name = tmp_gp.obj.GetName()
    r, e = safe(po.LT_execute, f"window -c {tmp_name};")
    gone, _ = safe(op.find_graph, tmp_name)
    rec("write", "window -c (close page)", {"err": str(e)[:60] if e else None,
                                            "still_exists": gone is not None})
    # 重命名
    rn_gp = op.new_graph(lname="ProbeRename")
    rn_name = rn_gp.obj.GetName()
    r, e = safe(po.LT_execute, f"window -r {rn_name} ProbeRenamed;")
    rec("write", "window -r (rename)", {"err": str(e)[:60] if e else None,
                                        "find_new": safe(op.find_graph, "ProbeRenamed")[0] is not None})
    # 激活
    r, e = safe(po.LT_execute, f"win -a {short};")
    rec("write", "win -a (activate)", "ok" if e is None else str(e)[:60])
    # 隐藏
    r, e = safe(po.LT_execute, f"window -h 1 {short};")
    rec("write", "window -h 1 (hide)", "ok" if e is None else str(e)[:60])
    safe(po.LT_execute, f"window -h 0 {short};")

    # ---------------- 8. 文字/标注对象 ----------------
    r, e = safe(po.LT_execute, 'label -p 2 20 "probe text";')
    rec("write", "label -p (add text)", "ok" if e is None else str(e)[:80])
    n_txt = lt_read("page.nlabels")
    rec("read", "LT:page.nlabels", n_txt)

    rec("api", "done", True)


if __name__ == "__main__":
    main()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.gettempdir(), "labtalk_probe2_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh, ensure_ascii=False, indent=1, default=str)
    print("PROBE REPORT ->", out)
    # 控制台摘要：只打印写入段与错误
    print("\n=== WRITE RESULTS ===")
    for k, v in REPORT["write"].items():
        print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:200]}")
    if REPORT["errors"]:
        print("\n=== ERRORS ===")
        for k, v in REPORT["errors"].items():
            print(f"{k}: {v[:200]}")
