# -*- coding: utf-8 -*-
"""
Plot Type 视觉标定脚本 v4：把候选 plotxy 代码逐一出图并导出 PNG，
供视觉模型识别真实图型。修正：LT_execute 仅 1 参数；多列用激活表+索引形式。
运行:  <venv python> -X utf8 smoke\\plot_type_probe.py
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

OUT = os.path.join(DEFAULT_OUTPUT_DIR, "probe_kinds")
os.makedirs(OUT, exist_ok=True)

CANDIDATES = [
    (204, "204_bar", 1),
    (206, "206_box", 1),
    (210, "210_area", 1),
    (211, "211_stack_area", 1),
    (215, "215_bar2", 1),
    (217, "217_errorbar", 1),
    (218, "218_waterfall", 1),
    (220, "220_polar", 1),
    (225, "225_bubble", 2),
    (235, "235_candlestick", 2),
    (240, "240_heatmap", 2),
    (310, "310_3d_scatter", 3),
    (313, "313_3d_ribbon", 3),
    (314, "314_3d_bars", 3),
]


def pages_before():
    return {str(op.po.Pages(i).GetName()) for i in range(op.po.Pages.Count)}


def destroy_extra(before):
    for i in reversed(range(op.po.Pages.Count)):
        try:
            if str(op.po.Pages(i).GetName()) not in before:
                op.po.Pages(i).destroy()
        except Exception:
            pass


def main():
    wks = op.new_sheet("w", "ProbeData")
    wks.from_list(0, [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5], lname="x")
    wks.from_list(1, [1.0, 4.0, 9.0, 16.0, 25.0, 36.0, 49.0], lname="y")
    wks.from_list(2, [3.0, 1.0, 4.0, 2.0, 5.0, 1.5, 4.5], lname="z")

    for code, label, ncol in CANDIDATES:
        before = pages_before()
        try:
            wks.activate()
            if ncol == 1:
                script = f"plotxy plot:={code} iy:={{A}};"
            elif ncol == 2:
                script = f"plotxy plot:={code} iy:=(1,2);"
            else:
                script = f"plotxy plot:={code} iy:=(1,2,3);"
            op.po.LT_execute(script)
            newp = pages_before() - before
            if newp:
                gname = next(iter(newp))
                gp = op.find_graph(gname)
                if gp:
                    try:
                        gp[0].rescale()
                    except Exception:
                        pass
                    path = os.path.join(OUT, f"{label}.png")
                    try:
                        gp.save_fig(path, replace=True, width=900)
                        print(f"{code}:{label} -> {path}")
                    except Exception as e:
                        print(f"{code}:{label} -> export failed {type(e).__name__} {e}")
                else:
                    print(f"{code}:{label} -> page but no graph obj")
            else:
                print(f"{code}:{label} -> NO-PAGE")
        except Exception as e:
            print(f"{code}:{label} -> err {type(e).__name__} {e}")
        destroy_extra(before)

    print("PROBE V4 DONE", OUT)


if __name__ == "__main__":
    main()
