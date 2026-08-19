# -*- coding: utf-8 -*-
"""验证 bar / 3D scatter 的健壮建图方案（页面名差集法），渲染 PNG 供视觉核对。
运行: <venv python> -X utf8 smoke\\plotxy_fix_probe.py
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

OUT = os.path.join(DEFAULT_OUTPUT_DIR, "probe_fix")
os.makedirs(OUT, exist_ok=True)


def page_names():
    return {str(op.po.Pages(i).GetName()) for i in range(op.po.Pages.Count)}


def plotxy_new_graph(script, before):
    """执行 plotxy 并返回新图页名（页面名差集法，不依赖 find_graph）。"""
    op.po.LT_execute(script)
    after = page_names()
    newp = after - before
    if not newp:
        return None
    # 优先图类型的页（跳过 Book/Matrix 数据页）：页名不含 'Book'
    cands = [n for n in newp if not n.lower().startswith("book")]
    return (cands or sorted(newp))[0]


def destroy_extra(before):
    for i in reversed(range(op.po.Pages.Count)):
        try:
            if str(op.po.Pages(i).GetName()) not in before:
                op.po.Pages(i).destroy()
        except Exception:
            pass


def main():
    wks = op.new_sheet("w", "FixData")
    wks.from_list(0, [1, 2, 3, 4, 5, 6, 7], lname="x")
    wks.from_list(1, [8, 3, 6, 2, 9, 4, 7], lname="y")
    wks.from_list(2, [3, 9, 1, 7, 2, 8, 4], lname="z")

    cases = [
        ("bar_204_colrange", "bar", f"plotxy plot:=204 iy:={wks.to_col_range(0)};"),
        ("bar_215_colrange", "bar", f"plotxy plot:=215 iy:={wks.to_col_range(0)};"),
        ("scatter3d_idx_active", "3d", f"plotxy plot:=310 iy:=(1,2,3);"),
        ("scatter3d_fullrange", "3d",
         f"plotxy plot:=310 iy:=[{wks}]!(1,2,3);"),
    ]
    for label, kind, script in cases:
        before = page_names()
        try:
            if kind == "3d":
                wks.activate()
            gname = plotxy_new_graph(script, before)
            if not gname:
                print(f"{label}: NO-PAGE")
                continue
            gp = op.find_graph(gname)
            if not gp:
                print(f"{label}: page '{gname}' but no graph obj")
                continue
            try:
                gp[0].rescale()
            except Exception:
                pass
            path = os.path.join(OUT, f"{label}.png")
            ok = gp.save_fig(path, replace=True, width=900)
            print(f"{label}: graph={gname} saved={os.path.exists(ok or path)} -> {label}.png")
        except Exception as e:
            print(f"{label}: err {type(e).__name__} {e}")
        destroy_extra(before)

    print("FIX PROBE DONE", OUT)


if __name__ == "__main__":
    main()
