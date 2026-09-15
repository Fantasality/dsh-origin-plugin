# -*- coding: utf-8 -*-
"""
labtalk_probe3 —— 细粒度编辑第二批探针（补齐线宽/线型/图例排除/轴网格/页面背景/批量关窗）
=========================================================================================

第二批只在第一批已验证的基础上补未定项：
- 曲线的线宽/线型读写通道（originpro get_float/set_cmd vs LabTalk -w/-d）
- 符号内部填充/边缘色、图例中排除单条曲线
- 轴的网格/刻度/对侧轴/次轴、轴标签加粗与格式
- 页面背景、页面高度写入、图层显示/隐藏
- 工作簿/矩阵页面的批量关闭（window -c 通配）
- 文本对象创建/枚举/删除

用法: <venv python> -X utf8 smoke\labtalk_probe3.py [输出json路径]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

R = {"read": {}, "write": {}, "api": {}, "errors": {}}


def rec(sec, k, v):
    R[sec][k] = v


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    import originpro as op
    import origin_engine as engine

    ok, conn = engine._connect_impl()
    rec("write", "connect", ok)
    if not ok:
        return
    po = op.po

    def lt_read(expr):
        v, _ = safe(engine._lt_read_float, expr)
        if v is not None:
            return v
        s, _ = safe(engine._lt_read_str, expr)
        return s

    w = op.new_sheet("w")
    w.from_list(0, [1.0, 2, 3, 4, 5], lname="x")
    w.from_list(1, [1.0, 4, 9, 16, 25], lname="alpha")
    w.from_list(2, [2.0, 3, 5, 7, 11], lname="beta")
    gp = op.new_graph(lname="ProbeEdit3")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="y")
    gl.rescale()
    short = gp.obj.GetName()
    po.LT_execute(f"win -a {short};")
    plots = gl.plot_list()

    # ---- 1. 线宽/线型 读写通道矩阵 ----
    p0 = plots[0]
    for key in ("linewidth", "line_width", "width"):
        v, e = safe(p0.get_float, key)
        rec("read", f"get_float('{key}')", v if e is None else str(e)[:60])
    for key in ("line_style", "linestyle", "line_type", "connect"):
        v, e = safe(p0.get_int, key)
        rec("read", f"get_int('{key}')", v if e is None else str(e)[:60])
    # 写入：set_cmd 选项通道 + get_float 读回
    safe(p0.set_cmd, "-w 2500")
    v, e = safe(p0.get_float, "linewidth")
    rec("write", "set_cmd('-w 2500') -> get_float('linewidth')", v if e is None else str(e)[:60])
    safe(p0.set_cmd, "-d 2")
    v, e = safe(p0.get_int, "line_style")
    rec("write", "set_cmd('-d 2') -> get_int('line_style')", v if e is None else str(e)[:60])
    # set_float 直接写 linewidth（若通道存在）
    _, e = safe(p0.set_float, "linewidth", 4.0)
    v, e2 = safe(p0.get_float, "linewidth")
    rec("write", "set_float('linewidth',4)", {"set_err": str(e)[:50] if e else None,
                                              "readback": v if e2 is None else str(e2)[:50]})
    # 符号填充/边缘
    rec("api", "plot.symbol_interior exists", hasattr(p0, "symbol_interior"))
    _, e = safe(setattr, p0, "symbol_interior", 1)
    v, _ = safe(getattr, p0, "symbol_interior")
    rec("write", "plot.symbol_interior=1", {"set_err": str(e)[:50] if e else None,
                                            "readback": repr(v)[:30]})
    # 图例排除单条曲线（候选通道探测）
    for key in ("showinlegend", "legend", "showlegend", "legendshow"):
        _, e = safe(p0.set_int, key, 0)
        rec("write", f"plot.set_int('{key}',0)", "ACCEPTED" if e is None else str(e)[:70])

    # ---- 2. 轴：网格/刻度/对侧/次轴/标签加粗 ----
    for key, val in (("x.showgrids", 1), ("x.showAxes", 1), ("x.opposite", 1),
                     ("x.ticklength", 8), ("x.label.bold", 1),
                     ("x.label.fsize", 12), ("y.showgrids", 1),
                     ("y2.show", 1), ("x.grid.majorType", 1)):
        _, e = safe(gl.set_int, key, val)
        v, e2 = safe(gl.get_int, key)
        rec("write", f"gl.set_int('{key}',{val})",
            {"set_err": str(e)[:60] if e else None,
             "readback": v if e2 is None else str(e2)[:60]})
    # 轴反向（LabTalk 通道）
    po.LT_execute("layer.x.reverse = 1;")
    rec("write", "layer.x.reverse=1", lt_read("layer.x.reverse"))
    po.LT_execute("layer.x.reverse = 0;")

    # ---- 3. 页面：背景 + 高度写入 + 尺寸读回 ----
    pw, ph, rx, ry = (lt_read("page.width"), lt_read("page.height"),
                      lt_read("page.resx"), lt_read("page.resy"))
    rec("read", "page w/h/dpi", {"w": pw, "h": ph, "resx": rx, "resy": ry})
    _, e = safe(gp.set_int, "height", int(ph or 4560) + 300)
    v, _ = safe(gp.get_int, "height")
    rec("write", "GPage.set_int('height',+300)", {"err": str(e)[:60] if e else None,
                                                  "readback": v})
    for key in ("background", "basecolor", "baseColor"):
        _, e = safe(gp.set_int, key, 5)
        rec("write", f"GPage.set_int('{key}',5)", "ACCEPTED" if e is None else str(e)[:70])
    rec("read", "LT:page.baseColor", lt_read("page.baseColor"))
    rec("read", "LT:page.background", lt_read("page.background"))

    # ---- 4. 图层显示/隐藏 + 尺寸(cm) 读写 ----
    po.LT_execute("layer.unit = 3; layer.width = 9; layer.height = 7;")
    rec("write", "layer cm 9x7", {"w": lt_read("layer.width"), "h": lt_read("layer.height")})
    _, e = safe(gl.set_int, "show", 0)
    rec("write", "layer.show=0 via gl.set_int", {"err": str(e)[:60] if e else None,
                                                 "readback": lt_read("layer.show")})
    po.LT_execute("layer.show = 1;")

    # ---- 5. 批量关窗（含工作簿） ----
    t1 = op.new_graph(lname="ProbeCloseA").obj.GetName()
    t2 = op.new_graph(lname="ProbeCloseB").obj.GetName()
    tb = op.new_sheet("w").__str__()
    _, e = safe(po.LT_execute, "window -c ProbeClose*;")
    rec("write", "window -c ProbeClose* (通配关多窗)",
        {"err": str(e)[:60] if e else None,
         "A_exists": safe(op.find_graph, t1)[0] is not None,
         "B_exists": safe(op.find_graph, t2)[0] is not None})
    rec("read", "workbook ref before close", tb)
    if tb.startswith("["):
        bname = tb[1:tb.index("]")]
        _, e = safe(po.LT_execute, f"window -c {bname};")
        rec("write", "window -c <Book>", {"err": str(e)[:60] if e else None,
                                          "still": safe(op.find_sheet, "w", tb)[0] is not None})

    # ---- 6. 文本对象：创建 / 枚举 / 删除 ----
    po.LT_execute('label -p 2 20 "probe3 text";')
    rec("api", "gl_dir(label/text)", [a for a in dir(gl) if any(
        k in a.lower() for k in ("label", "text", "obj", "legend", "name"))])
    rec("api", "gp_dir", [a for a in dir(gp) if not a.startswith("_")])
    for expr in ("page.nlabels", "page.nLabels", "page.nlabel", "page.nLabelObjects"):
        rec("read", f"LT:{expr}", lt_read(expr))
    # 名称探测：轴标题对象与文本对象（originpro gl.label(name) 通道）
    found = {}
    for cand in ("Legend", "Text", "Text1", "Text2", "xb", "xt", "yl", "yr",
                 "x1", "y1", "ProbeX"):
        obj, e = safe(gl.label, cand)
        found[cand] = "ok" if (obj is not None and e is None) else str(e)[:60]
    rec("api", "gl.label(候选名) 探测", found)
    # 图形对象枚举：GraphObjects 按名字索引 → 用 Count + 逐个候选名兜底
    try:
        cnt = gl.obj.GraphObjects.Count
        rec("api", "GraphObjects.Count", cnt)
    except Exception as ex:
        rec("errors", "GraphObjects.Count", str(ex)[:120])
    names = [c for c, s in found.items() if s == "ok" and c not in ("Legend",)]
    if names:
        target = names[0]
        _, e = safe(po.LT_execute, f"label -r {target};")
        rec("write", f"label -r {target} (delete)", "ok" if e is None else str(e)[:60])

    # ---- 7. 图层删除候选 ----
    gl2, e = safe(gp.add_layer)
    n_before = lt_read("page.nlayers")
    for cmd in ("layer -d 2;", ):
        _, e = safe(po.LT_execute, cmd)
        rec("write", f"LT:{cmd}", {"err": str(e)[:60] if e else None,
                                   "nlayers": lt_read("page.nlayers")})
    rec("read", "nlayers before delete", n_before)

    rec("write", "done", True)


if __name__ == "__main__":
    main()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.gettempdir(), "labtalk_probe3_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, ensure_ascii=False, indent=1, default=str)
    print("PROBE3 REPORT ->", out)
    for sec in ("read", "write", "api", "errors"):
        print(f"\n=== {sec.upper()} ===")
        for k, v in R.get(sec, {}).items():
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:220]}")
