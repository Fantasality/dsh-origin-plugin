# -*- coding: utf-8 -*-
"""plotxy 最小化调试：看页面集合/活动页/col_range 实际值。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import originpro as op

try:
    from originpro import config as _opc
    _opc.po.Attach()
except Exception:
    pass


def page_names(tag):
    try:
        names = [str(op.po.Pages(i).GetName()) for i in range(op.po.Pages.Count)]
        active = None
        try:
            active = str(op.po.ActiveLayer)
        except Exception:
            pass
        print(f"  {tag}: pages={names} active_pages_name?={active}")
    except Exception as e:
        print(f"  {tag}: ERR {e}")


def main():
    wks = op.new_sheet("w", "DbgData")
    wks.from_list(0, [1.0, 2.0, 3.0, 4.0, 5.0], lname="x")
    wks.from_list(1, [1.0, 4.0, 9.0, 16.0, 25.0], lname="y")
    wks.activate()
    colrange = wks.to_col_range(0)
    print("colrange(0) =", repr(colrange))
    try:
        print("to_col_range(0,1) =", repr(wks.to_col_range(0, 1)))
    except Exception as e:
        print("to_col_range(0,1) err:", type(e).__name__, e)
    page_names("before-plotxy-206")
    op.po.LT_execute(f"plotxy iy:={colrange} plot:=206;")
    page_names("after-plotxy-206")
    try:
        gp = op.find_graph()
        print("find_graph() ->", gp)
    except Exception as e:
        print("find_graph err:", e)

    # 另一种写法：短名 Book 引用
    wks.activate()
    page_names("before-plotxy-204")
    op.po.LT_execute("plotxy iy:=[Book4]1!A plot:=204;")  # 示例，实际短名需探测
    page_names("after-plotxy-204")

    # 用变量记录 plotxy 返回
    wks.activate()
    try:
        page_names("before-plotxy-310-idx")
        op.po.LT_execute("plotxy iy:=(1,2,3) plot:=310;")
        page_names("after-plotxy-310-idx")
    except Exception as e:
        print("310 err:", e)

    print("DEBUG DONE")


if __name__ == "__main__":
    main()
