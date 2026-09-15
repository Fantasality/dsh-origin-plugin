# -*- coding: utf-8 -*-
"""
labtalk_probe5 —— 窗口上下文依赖性矩阵（D1/D2 精确根因）
=========================================================

测定在"活动窗口 = 工作簿"时，各读写通道是否失靶：
A. 图页激活的可靠性：win -a <短名> vs GPage.activate()，各以 page.name$ / is_active 复核
B. 失活状态下：COM 通道（gp[0].plot_list / gp.obj.Layers.Count / gl.get_int / gl.set_int /
   axis.title）是否仍指向目标图页
C. 失活状态下：LabTalk 通道（xb.fsize / xb.text$ / legend.fsize / layer.*）落在哪里
D. 激活复核通过后：全部通道是否恢复正常

用法: <venv python> -X utf8 smoke\labtalk_probe5.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as engine  # noqa: E402

R = {}


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def active_name():
    return safe(engine._lt_read_str, "page.name$")[0]


def main():
    ok, _ = engine._connect_impl()
    if not ok:
        print("connect failed")
        sys.exit(2)
    op = engine._origin_app
    po = op.po

    # 建 2 曲线图 + 一个工作簿
    w = op.new_sheet("w")
    w.from_list(0, [1.0, 2, 3, 4, 5], lname="x")
    w.from_list(1, [1.0, 4, 9, 16, 25], lname="a")
    w.from_list(2, [2.0, 3, 5, 7, 11], lname="b")
    gp = op.new_graph(lname="P5")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="l")
    gl.rescale()
    gname = gp.obj.GetName()
    book = safe(lambda: w.obj.GetName())[0] or "Book1"
    R["names"] = {"graph": gname, "book": book}

    # ---------- A. 激活可靠性 ----------
    safe(po.LT_execute, f"win -a {book};")
    R["A_after_win_a_book"] = active_name()
    # win -a 图页后复核
    safe(po.LT_execute, f"win -a {gname};")
    R["A_after_win_a_graph"] = active_name()
    # GM: 再切回工作簿，改用 COM activate
    safe(po.LT_execute, f"win -a {book};")
    R["A_back_to_book"] = active_name()
    safe(gp.activate)
    R["A_after_gp_activate"] = active_name()
    R["A_is_active_method"] = safe(lambda: gp.is_active())[0]
    # 用 gd 对象（重新 find_graph）再验一次 is_active
    gp2 = op.find_graph(gname)
    R["A_is_active_fresh_obj"] = safe(lambda: gp2.is_active())[0]

    # ---------- B/C. 故意失活（切到工作簿）后测各通道 ----------
    safe(po.LT_execute, f"win -a {book};")
    R["BC_active_window"] = active_name()
    g = op.find_graph(gname)          # 按名查找，不激活
    R["B_plot_list_count"] = safe(lambda: len(g[0].plot_list() or []))[0]
    R["B_page_layers_count"] = safe(lambda: g.obj.Layers.Count)[0]
    R["B_layer_datplots_count"] = safe(lambda: g[0].obj.DataPlots.Count)[0]
    R["B_gl_get_int_x_label_fsize"] = safe(lambda: g[0].get_int("x.label.fsize"))[0]
    R["B_axis_title_read"] = safe(lambda: g[0].axis("x").title)[0]
    # COM 写入是否落在目标图页（失活状态）
    safe(lambda: g[0].set_int("x.showgrids", 1))
    R["B_after_com_write_showgrids"] = safe(lambda: g[0].get_int("x.showgrids"))[0]
    # LabTalk 读（失活状态）：读到的可能是工作簿上下文
    R["C_lt_xb_fsize"] = safe(engine._lt_read_float, "xb.fsize")[0]
    R["C_lt_legend_fsize"] = safe(engine._lt_read_float, "legend.fsize")[0]
    R["C_lt_layer_left"] = safe(engine._lt_read_float, "layer.left")[0]
    # LabTalk 写（失活状态）→ 落到哪个窗口？
    safe(po.LT_execute, 'xb.text$ = "P5_LT_WRITE";')
    try:
        title_after = g[0].axis("x").title
    except Exception as e:  # noqa: BLE001
        title_after = f"<err {e}>"
    R["C_lt_write_xb_landed_on_graph"] = ("P5_LT_WRITE" in str(title_after))
    R["C_after_lt_write_active_window"] = active_name()

    # ---------- D. 激活复核通过后再测 ----------
    gp3 = op.find_graph(gname)
    safe(lambda: gp3.activate())
    R["D_active_after_activate"] = active_name()
    R["D_plot_list_count"] = safe(lambda: gp3[0].plot_list() or [])[0] and len(gp3[0].plot_list())
    R["D_gl_get_int_x_label_fsize"] = safe(lambda: gp3[0].get_int("x.label.fsize"))[0]
    safe(po.LT_execute, 'xb.text$ = "P5_LT_WRITE_2";')
    R["D_lt_write_landed"] = ("P5_LT_WRITE_2" in str(safe(lambda: gp3[0].axis("x").title)[0]))
    R["D_lt_last_window_kf"] = ("P5_LT_WRITE_2" in str(
        safe(engine._lt_read_str, "%X")[0]))  # %X 寄存器 = 活动图层 X 轴标题

    # ---------- E. 命名空间：win -a 对"长名/短名/带空格"的容忍度 ----------
    gl_name = safe(lambda: gp3.lname)[0]
    R["E_gpage_lname"] = str(gl_name)[:40]
    safe(po.LT_execute, 'win -a "P5";')
    R["E_win_a_quoted_short"] = active_name()

    print(json.dumps(R, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
