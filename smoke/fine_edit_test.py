# -*- coding: utf-8 -*-
"""
fine_edit_test —— 细粒度编辑冒烟测试（真机，模拟新手的一串微调请求）
=====================================================================

覆盖（每一步都做读回断言）：
 1. list_pages：页面枚举 + 活动窗口
 2. 造一张 3 曲线图 + inspect_graph 巡检
 3. edit_plot：把第 2 条线改成红色 / 第 1 条加粗 / 改符号 / 半透明 / 隐藏第 3 条
 4. edit_axis：标题 + 手动范围 + 网格 + 标签字号加粗
 5. edit_legend：隐藏 → 显示 → 字号 → 右上角锚点 → 边框
 6. edit_page：纸张 8.9×6.5 cm + 图层几何
 7. add_text：加一条峰位标注
 8. manage_pages：建两个空工作簿再关掉（"帮我关掉几个窗口"）
 9. 线宽端到端目视证明：加粗前后各导出一张 PNG，断言图像确实变化
10. D1 回归：在"活动窗口=工作簿 + 多层图"的情况下 verify_graph，必须数到全部曲线
11. D2 回归：LabTalk 写入校验器在活动窗口不匹配时会明确失败，而不是静默成功

用法: <venv python> -X utf8 smoke\fine_edit_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as engine  # noqa: E402

FAILS = []


def check(cond, label, extra=None):
    status = "OK " if cond else "FAIL"
    line = f"[{status}] {label}"
    if extra is not None and not cond:
        line += f"  <- {json.dumps(extra, ensure_ascii=False, default=str)[:220]}"
    print(line)
    if not cond:
        FAILS.append(label)


def main():
    ok, conn = engine._connect_impl()
    if not ok:
        print("connect failed:", conn)
        sys.exit(2)
    op = engine._origin_app
    po = op.po

    # ---------- 1. list_pages ----------
    lp = engine.list_pages()
    check(lp.get("ok"), "list_pages 返回 ok", lp)
    check(isinstance(lp.get("pages"), list) and lp.get("count", 0) > 0,
          f"list_pages 枚举到 {lp.get('count')} 个页面", lp)
    print("   graphs:", (lp.get("graphs") or [])[:6],
          "| workbooks:", len(lp.get("workbooks") or []))

    # ---------- 2. 造 3 曲线图 + inspect ----------
    w = engine.write_data({"temperature_C": [20, 25, 30, 35, 40],
                           "run_A": [95, 101, 112, 118, 130],
                           "run_B": [90, 104, 108, 121, 127],
                           "run_C": [99, 99, 115, 117, 133]})
    check(w.get("ok"), "写入 3 序列数据", w)
    ws = w["worksheet"]
    p1 = engine.plot(ws, plot_type="line_symbol", graph_name="FineEditSmoke",
                     title="fine-edit smoke", style_mode="journal")
    check(p1.get("ok"), "创建 3 曲线图", p1)
    g = p1.get("graph")

    ins = engine.inspect_graph(g)
    check(ins.get("ok"), "inspect_graph 返回 ok", ins)
    n_plots = sum(len(l.get("plots") or []) for l in (ins.get("layers") or []))
    check(n_plots == 3, f"inspect_graph 数到 3 条曲线（实得 {n_plots}）", ins.get("layers"))
    check((ins.get("page") or {}).get("width_cm") is not None,
          "inspect_graph 给出页面尺寸(cm)", ins.get("page"))
    print("   page:", json.dumps(ins.get("page"), ensure_ascii=False)[:160])
    print("   legend:", json.dumps(ins.get("legend"), ensure_ascii=False)[:160])

    # ---------- 3. edit_plot ----------
    ep = engine.edit_plot(g, [
        {"plot": 1, "color": "#D55E00"},                 # 第 2 条换橙色
        {"plot": 0, "line_width_pt": 2.5},               # 第 1 条加粗
        {"plot": 2, "symbol_kind": 2, "symbol_size": 9},  # 第 3 条换圆点
        {"plot": 1, "transparency": 30},
        {"plot": 2, "visible": False},                    # 隐藏第 3 条
    ])
    check(ep.get("ok"), "edit_plot 返回 ok", ep)
    statuses = {c["item"]: c["status"] for c in (ep.get("changes") or [])}
    print("   changes:", json.dumps(statuses, ensure_ascii=False)[:300])
    check(any("color" in k and v == "applied" for k, v in statuses.items()),
          "换颜色 applied（含读回）", statuses)
    check(any("line_width_pt" in k and v.startswith("applied") for k, v in statuses.items()),
          "加粗 applied（通道写入）", statuses)
    check(any("transparency" in k and v == "applied" for k, v in statuses.items()),
          "透明度 applied", statuses)
    check(any("visible" in k and v == "applied" for k, v in statuses.items()),
          "隐藏曲线 applied", statuses)

    # 复核：读回第 2 条颜色确为橙色
    ins2 = engine.inspect_graph(g)
    colors = [p.get("color") for l in (ins2.get("layers") or [])
              for p in (l.get("plots") or [])]
    check("#D55E00" in colors, f"读回复核：图内出现橙色（实际 {colors}）", colors)

    # ---------- 4. edit_axis ----------
    ea = engine.edit_axis(g, axis="y", title="Pressure (kPa)", from_value=80, to_value=140,
                          props={"show_grids": 1, "label_font_pt": 12, "label_bold": 1})
    check(ea.get("ok"), "edit_axis 返回 ok", ea)
    astats = {c["item"]: (c["status"], c.get("readback")) for c in (ea.get("changes") or [])}
    print("   axis changes:", json.dumps(astats, ensure_ascii=False, default=str)[:300])
    check(astats.get("y.title", ("",))[0] == "applied", "轴标题 applied（读回一致）", astats)
    check(astats.get("y.range", ("",))[0] == "applied", "轴范围 applied", astats)
    check(astats.get("y.showgrids", ("",))[0] == "applied", "网格 applied", astats)
    check(astats.get("y.label.fsize", ("",))[0] == "applied", "标签字号 applied", astats)

    # ---------- 5. edit_legend ----------
    el1 = engine.edit_legend(g, {"visible": False})
    check(el1.get("ok") and all(c["status"] == "applied" for c in el1["changes"]),
          "隐藏图例 applied", el1.get("changes"))
    el2 = engine.edit_legend(g, {"visible": True, "font_size_pt": 12, "position": "tr",
                                 "frame": True})
    check(el2.get("ok"), "图例：显示/字号/右上锚点/边框", el2.get("changes"))
    lstats = {c["item"]: c["status"] for c in (el2.get("changes") or [])}
    print("   legend changes:", json.dumps(lstats, ensure_ascii=False)[:260])
    check(lstats.get("legend.show") == "applied", "图例显示 applied", lstats)
    check(lstats.get("legend.position") == "applied", "图例右上锚点 applied（读回坐标）", lstats)

    # ---------- 6. edit_page ----------
    epg = engine.edit_page(g, page_size_cm={"width": 8.9, "height": 6.5},
                           layer=0, layer_geometry_pct={"left": 14, "top": 12,
                                                        "width": 78, "height": 60})
    check(epg.get("ok"), "edit_page 返回 ok", epg)
    pstats = {c["item"]: (c["status"], c.get("readback")) for c in (epg.get("changes") or [])}
    print("   page changes:", json.dumps(pstats, ensure_ascii=False, default=str)[:320])
    check(pstats.get("page.width_cm", ("",))[0] == "applied", "纸张宽 8.9cm applied", pstats)
    check(pstats.get("page.height_cm", ("",))[0] == "applied", "纸张高 6.5cm applied", pstats)
    check(pstats.get("layer0.width_pct", ("",))[0] == "applied", "图层宽 78% applied", pstats)

    # ---------- 7. add_text ----------
    at = engine.add_text(g, "peak @ 30 C", x=30, y=118)
    check(at.get("ok"), "添加文本标注", at)

    # ---------- 8. manage_pages：建两个空工作簿再关掉 ----------
    b1 = engine.write_data({"junk": [1, 2, 3]})
    b2 = engine.write_data({"junk": [4, 5, 6]})
    names = []
    for r in (b1, b2):
        ref = r.get("worksheet") or ""
        if ref.startswith("["):
            names.append(ref[1:ref.index("]")])
    check(len(names) == 2, f"建了两个待关闭工作簿 {names}", names)
    mp = engine.manage_pages("close", pages=names)
    check(mp.get("ok") and mp.get("n_applied") == 2,
          f"关闭两个工作簿（{mp.get('n_applied')}/2）", mp.get("results"))
    lp2 = engine.list_pages()
    gone = all(n not in (lp2.get("workbooks") or []) for n in names)
    check(gone, "复核：两个工作簿已不在页面列表中", lp2.get("workbooks"))

    # ---------- 9. 线宽端到端目视证明（PNG 前后对比） ----------
    tmp = tempfile.mkdtemp(prefix="dsh_fine_edit_")
    before = os.path.join(tmp, "before.png")
    after = os.path.join(tmp, "after.png")
    engine.edit_plot(g, [{"plot": 0, "line_width_pt": 1.0}])
    r_before = engine.export(g, file_path=before, fmt="png", width=900)
    engine.edit_plot(g, [{"plot": 0, "line_width_pt": 6.0}])
    r_after = engine.export(g, file_path=after, fmt="png", width=900)
    check(r_before.get("ok") and r_after.get("ok"), "加粗前后各导出一张 PNG",
          [r_before.get("error"), r_after.get("error")])
    if r_before.get("ok") and r_after.get("ok"):
        b1b = open(before, "rb").read()
        b2b = open(after, "rb").read()
        changed = (b1b != b2b)
        # 更强的证据：加粗会显著增加前景像素数 → PNG 体积通常变大
        check(changed, f"加粗前后图像确实不同（{len(b1b)}B vs {len(b2b)}B）",
              {"before": len(b1b), "after": len(b2b)})

    # ---------- 10. D1 回归：工作簿活动 + 多层图 → 曲线数仍正确 ----------
    w2 = engine.write_data({"x": [1.0, 2, 3], "y1": [1, 2, 3], "y2": [3, 2, 1],
                            "y3": [2, 2, 2], "y4": [1, 3, 1], "y5": [3, 1, 3],
                            "y6": [2, 4, 6]})
    ws2 = w2["worksheet"]
    # 第 1 层只放 3 条，第 2 层再放 3 条 → 全图共 6 条（跨层统计才能数对）
    g2 = engine.plot(ws2, plot_type="line", y_columns=["y1", "y2", "y3"],
                     graph_name="MultiLayerD1")
    check(g2.get("ok"), "创建跨层图（第 1 层 3 条）", g2)
    gp2 = op.find_graph(g2["graph"])
    gl2, e_add = engine.safe_call(gp2.add_layer)
    check(gl2 is not None, f"添加第 2 个图层（{e_add or 'ok'}）", e_add)
    if gl2 is not None:
        wsobj = op.find_sheet("w", ws2)
        for ci in (4, 5, 6):
            gl2.add_plot(wsobj, ci, 0, type="l")
        gl2.rescale()
    # 把活动窗口切到工作簿（用正确的窗口名）
    ref = str(op.find_sheet("w", ws2))
    book = ref[1:ref.index("]")] if ref.startswith("[") else None
    if book:
        engine._origin_app.po.LT_execute(f"win -a {book};")
    vf = engine.verify_graph(graph=g2["graph"], expected_series=6)
    sc = next((c for c in (vf.get("checks") or []) if c["name"] == "series_count"), {})
    check(vf.get("ok") and sc.get("status") == "pass",
          f"D1 回归：活动窗口={ (vf.get('context') or {}).get('active_window') } 时仍数到 6 条曲线",
          {"series_check": sc, "passed": vf.get("passed"), "context": vf.get("context")})
    print("   verify context:", json.dumps(vf.get("context"), ensure_ascii=False)[:220])

    # ---------- 11. D2 回归：LabTalk 写校验器能抓出失靶 ----------
    if book:
        try:
            engine._origin_app.po.LT_execute(f"win -a {book};")
        except Exception:
            pass
    ok_w, rb, note = engine._lt_write_checked('xb.text$ = "SHOULD_CHECK";', "xb.text$",
                                              expect="SHOULD_CHECK")
    # 期望：要么在校验里被判失靶（ok_w False），要么确实写进了图页并读回一致
    active = engine._lt_read_str("page.name$")
    landed = False
    try:
        gtmp = op.find_graph(g)
        landed = "SHOULD_CHECK" in str(gtmp[0].axis("x").title)
    except Exception:
        pass
    check((ok_w and landed) or (not ok_w),
          f"D2 回归：LabTalk 写入校验器不给假成功（ok={ok_w}, 活动窗口={active}, 落图={landed}）",
          {"note": note, "readback": rb})

    print()
    if FAILS:
        print(f"FINE-EDIT-TEST FAIL（{len(FAILS)} 项）:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("FINE-EDIT-TEST OK")


if __name__ == "__main__":
    main()
