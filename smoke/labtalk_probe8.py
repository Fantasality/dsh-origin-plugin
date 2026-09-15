# -*- coding: utf-8 -*-
"""
labtalk_probe8 —— 曲线隐藏通道 + 图例 dots 锚点方案验证
=========================================================

1. 曲线隐藏候选：p.show 是属性（不可调用）→ 测 set_int('show',0) / obj 层方法 /
   LabTalk 掩码（先 layer.plot = n 设活动曲线再 set %C -md 0）
2. 图例锚点方案：用 page.width/height（dots）+ legend.width/height（dots）
   + set_int('left'/'top') 计算右上角 → 读回复核
"""
from __future__ import annotations

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
    gp = op.new_graph(lname="P8")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="l")
    gl.rescale()
    short = gp.obj.GetName()
    po.LT_execute(f"win -a {short};")
    p0, p1 = gl.plot_list()[0], gl.plot_list()[1]

    # ---------- 1. 曲线隐藏通道 ----------
    R["obj_dir_visibleish"] = [a for a in dir(p0.obj)
                               if any(k in a.lower() for k in ("show", "visible", "hide", "mask"))]
    # A: set_int('show', 0)
    _, e = safe(p0.set_int, "show", 0)
    R["A_set_int_show0"] = {"err": str(e)[:60] if e else None,
                            "readback": safe(p0.get_int, "show")[0]}
    safe(p0.set_int, "show", 1)
    # B: obj 层（OriginExt DataPlot）
    for meth in ("SetShow", "SetVisible", "Show", "SetProperty"):
        if hasattr(p0.obj, meth):
            R[f"B_obj_has_{meth}"] = True
    # C: LabTalk 掩码：先设活动曲线
    R["C_layer_plot_read"] = oedit.lt_float(po, "layer.plot")
    R["C_set_layer_plot_2"] = oedit.lt_exec(po, "layer.plot = 2;")
    R["C_after_set_active_plot"] = oedit.lt_float(po, "layer.plot")
    R["C_set_md0"] = oedit.lt_exec(po, "set %C -md 0;")
    R["C_plot1_get_int_show"] = safe(p1.get_int, "show")[0]
    R["C_plot0_get_int_show"] = safe(p0.get_int, "show")[0]
    # 掩码读回候选
    for expr in ("%C.md", "layer.plot", "plot.md"):
        R[f"C_read_{expr}"] = oedit.lt_float(po, expr)
    oedit.lt_exec(po, "set %C -md 1;")
    R["C_restored_plot1_show"] = safe(p1.get_int, "show")[0]

    # ---------- 2. 图例锚点方案（dots）----------
    lgnd, _ = safe(lambda: gl.label("Legend"))
    pw = oedit.lt_float(po, "page.width")
    ph = oedit.lt_float(po, "page.height")
    lw = oedit.lt_float(po, "legend.width")
    lh = oedit.lt_float(po, "legend.height")
    margin = int(0.01 * (pw or 6000))
    target = {"left": int(pw - lw - margin), "top": margin}
    for k, v in target.items():
        safe(lambda kk=k, vv=v: lgnd.set_int(kk, vv))
    R["legend_anchor_tr"] = {
        "page_dots": [pw, ph], "legend_dots": [lw, lh], "margin": margin,
        "target": target,
        "readback": {k: safe(lambda kk=k: lgnd.get_int(kk))[0] for k in ("left", "top")}}
    # 再测左下角
    target_bl = {"left": margin, "top": int((ph or 4000) - lh - margin)}
    for k, v in target_bl.items():
        safe(lambda kk=k, vv=v: lgnd.set_int(kk, vv))
    R["legend_anchor_bl"] = {
        "target": target_bl,
        "readback": {k: safe(lambda kk=k: lgnd.get_int(kk))[0] for k in ("left", "top")}}

    print(json.dumps(R, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
