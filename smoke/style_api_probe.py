# -*- coding: utf-8 -*-
"""样式应用 API 探测第二发：plot_list / 轴标题 LT / 图例 / 删页。
运行: <venv python> -X utf8 smoke\\style_api_probe.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import originpro as op

try:
    from originpro import config as _opc
    _opc.po.Attach()
except Exception:
    pass


def main():
    wks = op.new_sheet("w", "StyleData2")
    wks.from_list(0, [1, 2, 3, 4, 5], lname="x")
    wks.from_list(1, [1, 4, 9, 16, 25], lname="a")
    wks.from_list(2, [2, 3, 5, 8, 13], lname="b")
    gp = op.new_graph(lname="StyleProbe2")
    gl = gp[0]
    p1 = gl.add_plot(wks, 1, 0, type="y")
    gl.add_plot(wks, 2, 0, type="y")
    gl.rescale()

    print("== layer.plot_list ==")
    try:
        pl = gl.plot_list()
        print("  plot_list len:", len(pl))
        for i, p in enumerate(pl):
            print(f"  plot[{i}] type={type(p).__name__} color={getattr(p,'color',None)!r} "
                  f"symbol_kind={getattr(p,'symbol_kind',None)!r} symbol_size={getattr(p,'symbol_size',None)!r}")
    except Exception as e:
        print("  plot_list ERR", type(e).__name__, e)

    print("== set color (BGR int) + symbol_kind + size ==")
    for i, p in enumerate(gl.plot_list() or []):
        try:
            p.color = 0x00B27200     # 试试 BGR 或 RGB 哪种给色
            print(f"  plot[{i}].color = 0x00B27200 OK, now read={hex(getattr(p,'color',0))}")
        except Exception as e:
            print(f"  plot[{i}].color ERR {type(e).__name__} {e}")
        try:
            p.symbol_kind = (2, 3)[i]
            print(f"  plot[{i}].symbol_kind = {(2,3)[i]} OK")
        except Exception as e:
            print(f"  plot[{i}].symbol_kind ERR {type(e).__name__} {e}")
        try:
            p.symbol_size = 12
            print(f"  plot[{i}].symbol_size = 12 OK")
        except Exception as e:
            print(f"  plot[{i}].symbol_size ERR {type(e).__name__} {e}")

    print("== axis title via LT ==")
    try:
        op.po.LT_execute('layer.y.title.text$ = "My Y Title"; layer.x.title.text$ = "My X Title";')
        print("  LT set axis titles OK")
        if hasattr(op.po, "LTGetVarStr"):
            v = op.po.LTGetVarStr("layer.y.title.text$")
            print("  readback layer.y.title =", repr(v))
        else:
            print("  no LTGetVarStr; has:", [a for a in dir(op.po) if 'GT' in a or 'Var' in a][:10])
    except Exception as e:
        print("  LT axis titles ERR", type(e).__name__, e)

    print("== legend on/off via LT ==")
    try:
        op.po.LT_execute("layer1.showlegend=0;")
        print("  layer1.showlegend=0 OK")
        op.po.LT_execute("layer1.showlegend=1;")
        print("  layer1.showlegend=1 OK")
    except Exception as e:
        print("  legend LT ERR", type(e).__name__, e)

    gname = gp.name
    print("== GPage.destroy ==")
    try:
        gp.destroy()
        if gname not in [str(op.po.Pages(i).GetName()) for i in range(op.po.Pages.Count)]:
            print(f"  gp.destroy() removed page '{gname}' OK")
        else:
            print(f"  gp.destroy() did NOT remove '{gname}'")
    except Exception as e:
        print("  gp.destroy ERR", type(e).__name__, e)

    print("STYLE API PROBE2 DONE")


if __name__ == "__main__":
    main()

