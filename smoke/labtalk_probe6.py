# -*- coding: utf-8 -*-
"""
labtalk_probe6 —— 真·失活状态下的通道矩阵（用正确的工作簿窗口名）
====================================================================

P5 的错误：w.obj.GetName() 得到的是工作表名 Sheet1，win -a Sheet1 静默失败
→ 活动窗口从未离开图页 → 通道矩阵测了个假的。本探针改用 str(w) → [BookN]Sheet1
取真正的窗口名，并**先复核激活成功**再测通道。

输出：失活 vs 激活两种状态下，每类通道是否会失靶（这是 D1/D2 修复的直接依据）。
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
        sys.exit(2)
    op = engine._origin_app
    po = op.po

    w = op.new_sheet("w")
    w.from_list(0, [1.0, 2, 3, 4, 5], lname="x")
    w.from_list(1, [1.0, 4, 9, 16, 25], lname="a")
    w.from_list(2, [2.0, 3, 5, 7, 11], lname="b")
    gp = op.new_graph(lname="P6")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="l")
    gl.rescale()
    gname = gp.obj.GetName()

    # 正确的窗口名：str(worksheet) = "[BookN]Sheet1"
    ref = str(w)
    book = ref[1:ref.index("]")] if ref.startswith("[") else None
    R["names"] = {"graph": gname, "worksheet_str": ref, "book_window": book}

    # ---- 切到工作簿并**复核** ----
    safe(po.LT_execute, f"win -a {book};")
    R["book_activation_verified"] = (active_name() == book)
    R["active_after_switch"] = active_name()
    if active_name() != book:
        # 兜底：用 COM 激活工作表
        safe(lambda: w.activate())
        safe(po.LT_execute, f"win -a {book};")
        R["active_after_retry"] = active_name()

    g = op.find_graph(gname)     # 按名查找（不激活）
    inactive = {"active_window": active_name()}
    # COM 通道
    inactive["COM_plot_list_count"] = safe(lambda: len(g[0].plot_list() or []))[0]
    inactive["COM_page_Layers_Count"] = safe(lambda: g.obj.Layers.Count)[0]
    inactive["COM_layer_DataPlots_Count"] = safe(lambda: g[0].obj.DataPlots.Count)[0]
    inactive["COM_get_int_x_label_fsize"] = safe(lambda: g[0].get_int("x.label.fsize"))[0]
    inactive["COM_axis_title_read"] = safe(lambda: g[0].axis("x").title)[0]
    inactive["COM_gp_is_active"] = safe(lambda: g.is_active())[0]
    # COM 写入是否生效（失活状态）
    safe(lambda: g[0].set_int("x.showgrids", 1))
    inactive["COM_write_showgrids_readback"] = safe(lambda: g[0].get_int("x.showgrids"))[0]
    # LabTalk 通道（失活状态）
    inactive["LT_xb_fsize"] = safe(engine._lt_read_float, "xb.fsize")[0]
    inactive["LT_xb_text_read"] = safe(engine._lt_read_str, "xb.text$")[0]
    inactive["LT_legend_fsize"] = safe(engine._lt_read_float, "legend.fsize")[0]
    inactive["LT_legend_show"] = safe(engine._lt_read_float, "legend.show")[0]
    inactive["LT_layer_left"] = safe(engine._lt_read_float, "layer.left")[0]
    inactive["LT_page_nlayers"] = safe(engine._lt_read_float, "page.nlayers")[0]
    # LabTalk 写入：落到哪个窗口？
    safe(po.LT_execute, 'xb.text$ = "P6_WRITE_INACTIVE";')
    inactive["LT_write_landed_on_graph"] = ("P6_WRITE_INACTIVE" in str(
        safe(lambda: op.find_graph(gname)[0].axis("x").title)[0]))
    # 工作簿是否被写脏？（LabTalk 写 xb 到一个 Book 时的表现）
    inactive["LT_active_after_write"] = active_name()
    R["INACTIVE"] = inactive

    # ---- 用 COM 激活图页 + 复核 ----
    safe(lambda: op.find_graph(gname).activate())
    R["graph_activation_verified"] = (active_name() == gname)
    g2 = op.find_graph(gname)
    active = {"active_window": active_name()}
    active["COM_plot_list_count"] = safe(lambda: len(g2[0].plot_list() or []))[0]
    active["COM_get_int_x_label_fsize"] = safe(lambda: g2[0].get_int("x.label.fsize"))[0]
    active["LT_xb_fsize"] = safe(engine._lt_read_float, "xb.fsize")[0]
    active["LT_legend_fsize"] = safe(engine._lt_read_float, "legend.fsize")[0]
    active["LT_page_nlayers"] = safe(engine._lt_read_float, "page.nlayers")[0]
    safe(po.LT_execute, 'xb.text$ = "P6_WRITE_ACTIVE";')
    active["LT_write_landed"] = ("P6_WRITE_ACTIVE" in str(
        safe(lambda: op.find_graph(gname)[0].axis("x").title)[0]))
    # 恢复活动窗口为工作簿（模拟 AI 常态）
    safe(po.LT_execute, f"win -a {book};")
    active["restored_active"] = active_name()
    R["ACTIVE"] = active

    print(json.dumps(R, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
