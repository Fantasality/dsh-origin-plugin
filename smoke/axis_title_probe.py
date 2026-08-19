# -*- coding: utf-8 -*-
"""探测：指定图页激活方式 + 图层 axis 属性 + 轴标题可靠设置。
运行: <venv python> -X utf8 smoke\\axis_title_probe.py
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
    wks = op.new_sheet("w", "AxisData")
    wks.from_list(0, [1, 2, 3, 4, 5], lname="x")
    wks.from_list(1, [1, 4, 9, 16, 25], lname="a")
    gp = op.new_graph(lname="AxisProbe")
    gl = gp[0]
    gl.add_plot(wks, 1, 0, type="l")
    gl.rescale()
    gname = gp.obj.GetName()

    print("== activation options ==")
    for meth in ("Activate", "activate"):
        try:
            f = getattr(gp.obj, meth, None)
            print(f"  gp.obj.{meth} exists: {f is not None}")
        except Exception as e:
            print(f"  gp.obj.{meth} ERR {e}")
    try:
        gp.obj.Activate()
        print("  gp.obj.Activate() OK")
    except Exception as e:
        print("  gp.obj.Activate() ERR", type(e).__name__, e)
    for cmd in (f"win -a {gname};", f"doc -s {gname};", f"page.active$ = \"{gname}\";"):
        try:
            op.po.LT_execute(cmd)
            print(f"  LT {cmd!r} OK")
        except Exception as e:
            print(f"  LT {cmd!r} ERR {type(e).__name__}")

    print("== gl.axis type/attrs ==")
    print("  type(gl.axis):", type(gl.axis))
    try:
        print("  dir(gl.axis):", [a for a in dir(gl.axis) if not a.startswith('_')][:40])
    except Exception as e:
        print("  dir(gl.axis) ERR", e)

    print("== try LT axis titles after Activate ==")
    try:
        gp.obj.Activate()
    except Exception:
        pass
    try:
        op.po.LT_execute('layer.y.title.text$ = "ProbeY"; layer.x.title.text$ = "ProbeX";')
        print("  set via LT OK")
    except Exception as e:
        print("  set via LT ERR", type(e).__name__, e)

    import os as _os
    _os.makedirs(_os.path.join(_os.path.expanduser("~"), "dsch_origin_plugin", "output", "_axisprobe"), exist_ok=True)
    path = _os.path.join(_os.path.expanduser("~"), "dsch_origin_plugin", "output", "_axisprobe", "axisprobe.png")
    try:
        gp.save_fig(path, replace=True, width=700)
        print("saved:", path)
    except Exception as e:
        print("save_fig ERR", e)

    print("AXIS TITLE PROBE DONE")


if __name__ == "__main__":
    main()
