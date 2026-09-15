# -*- coding: utf-8 -*-
"""
labtalk_probe7 —— 曲线隐藏通道 + 图例坐标单位（细粒度编辑收尾探针）
=====================================================================

要解决：
1. 隐藏单条曲线：p.show(0) 是否有效？get_int('show') 是否反映？还有哪些通道？
2. 图例坐标单位：LabTalk legend.x/y（文档说像素）与 GLabel.get_float('x'/'y') 各自
   是什么单位？如何换算成"右上角"这类锚点定位？
"""
from __future__ import annotations

import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as engine  # noqa: E402
import origin_edit as oedit     # noqa: E402

R = {}


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    ok, _ = engine._connect_impl()
    if not ok:
        sys.exit(2)
    op = engine._origin_app
    po = op.po

    w = op.new_sheet("w")
    w.from_list(0, [1.0, 2, 3, 4, 5], lname="x")
    w.from_list(1, [1.0, 4, 9, 16, 25], lname="a")
    w.from_list(2, [2.0, 3, 5, 7, 11], lname="b")
    gp = op.new_graph(lname="P7")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="l")
    gl.rescale()
    short = gp.obj.GetName()
    po.LT_execute(f"win -a {short};")
    plots = gl.plot_list()
    p0, p1 = plots[0], plots[1]

    # ---------- 1. 隐藏曲线：可用通道 ----------
    R["plot_show_signature"] = str(safe(inspect.signature, p0.show)[0])
    R["plot_dir_showish"] = [a for a in dir(p0) if "show" in a.lower() or "hide" in a.lower()]
    # 通道 A: p.show(0)
    R["A_show0_err"] = str(safe(p0.show, 0)[1])
    R["A_get_int_show"] = safe(p0.get_int, "show")[0]
    R["A_getattr_show"] = repr(safe(getattr, p0, "show")[0])[:60]
    # 通道 B: LabTalk 掩码 set %C -md 0
    oedit.lt_exec(po, "layer -p 1;")   # 让第 1 条成为活动曲线（若支持）
    R["B_set_md0_err"] = str(oedit.lt_exec(po, "set %C -md 0;"))
    R["B_get_int_show"] = safe(p0.get_int, "show")[0]
    # 通道 C: 恢复并对比
    oedit.lt_exec(po, "set %C -md 1;")
    R["C_after_md1_get_int_show"] = safe(p0.get_int, "show")[0]
    # 用"图层可见曲线数"做独立判据：隐藏后导出的图里应该少一条（用画布像素太贵，改看 DataPlots 计数）
    R["datplots_count"] = safe(lambda: gl.obj.DataPlots.Count)[0]

    # ---------- 2. 图例坐标单位 ----------
    lgnd, _ = safe(lambda: gl.label("Legend"))
    lt_x = oedit.lt_float(po, "legend.x")
    lt_y = oedit.lt_float(po, "legend.y")
    gx = safe(lambda: lgnd.get_float("x"))[0]
    gy = safe(lambda: lgnd.get_float("y"))[0]
    dx = safe(lambda: lgnd.get_float("dx"))[0]
    dy = safe(lambda: lgnd.get_float("dy"))[0]
    lx = safe(lambda: lgnd.get_int("left"))[0]
    ly = safe(lambda: lgnd.get_int("top"))[0]
    pw, ph = oedit.lt_float(po, "page.width"), oedit.lt_float(po, "page.height")
    rx, ry = oedit.lt_float(po, "page.resx"), oedit.lt_float(po, "page.resy")
    geo = {}
    for unit in (1, 3, 5):
        gl.set_int("unit", unit)
        geo[unit] = {k: safe(lambda kk=k: gl.get_float(kk))[0]
                     for k in ("left", "top", "width", "height")}
    gl.set_int("unit", 1)
    R["legend_units_before"] = {
        "LT_legend_x_y": [lt_x, lt_y], "GLabel_x_y": [gx, gy],
        "GLabel_dx_dy": [dx, dy], "GLabel_left_top": [lx, ly],
        "page_dots": [pw, ph], "page_dpi": [rx, ry], "layer_geo_by_unit": geo}
    # 写 LabTalk legend.x=100 → 看两通道各自变成什么
    oedit.lt_exec(po, "legend.x = 100;")
    R["after_LT_set_x100"] = {"LT_legend_x": oedit.lt_float(po, "legend.x"),
                              "GLabel_x": safe(lambda: lgnd.get_float("x"))[0]}
    # 写 GLabel x=100 → 看两通道
    safe(lambda: lgnd.set_float("x", 100.0))
    R["after_GLabel_set_x100"] = {"LT_legend_x": oedit.lt_float(po, "legend.x"),
                                  "GLabel_x": safe(lambda: lgnd.get_float("x"))[0]}
    # left/top 通道（物理坐标）是否存在对应关系
    safe(lambda: lgnd.set_int("left", 500))
    safe(lambda: lgnd.set_int("top", 400))
    R["after_left500_top400"] = {"LT_legend_x": oedit.lt_float(po, "legend.x"),
                                 "LT_legend_y": oedit.lt_float(po, "legend.y"),
                                 "GLabel_left_top": [safe(lambda: lgnd.get_int("left"))[0],
                                                      safe(lambda: lgnd.get_int("top"))[0]]}
    # 图例自身尺寸（LabTalk legend.width/height 文档说只读）
    R["legend_size_channels"] = {
        "LT_legend_width": oedit.lt_float(po, "legend.width"),
        "LT_legend_height": oedit.lt_float(po, "legend.height")}

    print(json.dumps(R, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
