# -*- coding: utf-8 -*-
"""
Origin 内置图模板探测：尝试 op.new_graph(template=name)，保存空模板页 PNG，
供视觉模型确认图型。运行: <venv python> -X utf8 smoke\\template_probe.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import originpro as op
from origin_engine import DEFAULT_OUTPUT_DIR

try:
    from originpro import config as _opc
    _opc.po.Attach()
except Exception:
    pass

OUT = os.path.join(DEFAULT_OUTPUT_DIR, "probe_tpl")
os.makedirs(OUT, exist_ok=True)

# 候选模板名（Origin 内置图模板的公开名称集合）
TPL = [
    "line", "scatter", "line_symbol", "column", "column1", "bar",
    "area", "stack", "percentStack", "step", "stairstep",
    "polar_xy_rtheta", "polar_r_theta", "ternary", "bubble",
    "waterfall", "candlestick", "high_low_close", "doubleY", "OKBox",
    "box", "pie", "contour", "3DScatter", "3DWaterfall", "3DRibbon",
    "3DBars", "3DErrorBar", "3DVector", "GLparafunc", "energy_bar",
    "heatmap", "numeric_bar", "split", "graph", "lattice", "vector",
]


def destroy_pages_back_to(start):
    for i in reversed(range(op.po.Pages.Count)):
        try:
            if i >= start:
                op.po.Pages(i).destroy()
        except Exception:
            pass


def main():
    n0 = op.po.Pages.Count
    for name in TPL:
        start = op.po.Pages.Count
        try:
            gp = op.new_graph(template=name)
            ok_count = op.po.Pages.Count - start
            path = os.path.join(OUT, f"tpl_{name}.png")
            try:
                gp.save_fig(path, replace=True, width=700)
                print(f"  {name:<16} -> OK pages={ok_count} saved={os.path.exists(path)}")
            except Exception as e:
                print(f"  {name:<16} -> OK pages={ok_count} SAVE-ERR {type(e).__name__} {e}")
        except Exception as e:
            print(f"  {name:<16} -> ERR {type(e).__name__} {e}")
        destroy_pages_back_to(start)
    print("TEMPLATE PROBE DONE", OUT)


if __name__ == "__main__":
    main()
