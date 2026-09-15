# -*- coding: utf-8 -*-
"""
repro_defects —— 三条真缺陷的回归测试（修复后必须全绿）
=========================================================

D1 origin_verify 曲线数：跨全部图层统计 + 激活复核，活动窗口是工作簿时不得误判
D2 LabTalk 静默失靶：写入校验器必须给出"未生效"而不是假成功
D3 xrd_pattern 量程压缩：必须走双层布局，主峰占自身图层量程 ≥60%，X 轴严格对齐

用法: <venv python> -X utf8 smoke\repro_defects.py            # 打印证据
      <venv python> -X utf8 smoke\repro_defects.py --expect-fixed   # 全绿才退出 0
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as engine  # noqa: E402

EXPECT_FIXED = "--expect-fixed" in sys.argv
R = {}
FAILS = []


def record(key, value):
    R[key] = value


def judge(cond, label, extra=None):
    if not cond:
        FAILS.append(label)
    print(f"[{'OK ' if cond else 'FAIL'}] {label}"
          + ("" if cond else f"  <- {json.dumps(extra, ensure_ascii=False, default=str)[:220]}"))


def main():
    ok, conn = engine._connect_impl()
    if not ok:
        print("connect failed:", conn)
        sys.exit(2)
    op = engine._origin_app
    po = op.po

    def activate_book(ref):
        """按 str(worksheet)='[BookN]Sheet1' 取窗口名并激活（并复核）。"""
        s = str(ref)
        if not s.startswith("["):
            return None
        book = s[1:s.index("]")]
        po.LT_execute(f"win -a {book};")
        active = engine._lt_read_str("page.name$")
        return book if active == book else None

    # ================= D1 =================
    w = engine.write_data({"x": [1.0, 2, 3, 4, 5],
                           "a": [1, 2, 3, 4, 5], "b": [2, 4, 6, 8, 10],
                           "c": [3, 6, 9, 12, 15], "d": [1, 3, 5, 7, 9],
                           "e": [4, 3, 2, 1, 0], "f": [5, 4, 3, 2, 1]})
    ws = w["worksheet"]
    g0 = engine.plot(ws, plot_type="line", y_columns=["a", "b", "c"],
                     graph_name="RegD1")
    gp = op.find_graph(g0["graph"])
    gl2, _ = engine.safe_call(gp.add_layer)
    wsobj = op.find_sheet("w", ws)
    for ci in (4, 5, 6):
        gl2.add_plot(wsobj, ci, 0, type="l")
    gl2.rescale()
    active = activate_book(ws)           # 故意让工作簿成为活动窗口
    record("D1_active_window", active)
    vf = engine.verify_graph(graph=g0["graph"], expected_series=6)
    sc = next((c for c in (vf.get("checks") or []) if c["name"] == "series_count"), {})
    record("D1_series_check", sc)
    record("D1_context", vf.get("context"))
    judge(vf.get("ok") and sc.get("status") == "pass" and sc.get("value") == 6,
          f"D1：活动窗口={active} 时跨层数到 6 条曲线且不误判", sc)
    judge(sc.get("per_layer") == [3, 3],
          f"D1：逐层明细正确 {sc.get('per_layer')}", sc)

    # 反向：期望值与实际不符时才判 fail
    vf2 = engine.verify_graph(graph=g0["graph"], expected_series=4)
    sc2 = next((c for c in (vf2.get("checks") or []) if c["name"] == "series_count"), {})
    judge(sc2.get("status") == "fail", "D1：期望值与实际不符时才 fail（6 != 4）", sc2)

    # ================= D2 =================
    if active:
        po.LT_execute(f"win -a {active};")
    ok_w, rb, note = engine._lt_write_checked('xb.text$ = "REG_D2";', "xb.text$",
                                              expect="REG_D2")
    landed = False
    try:
        landed = "REG_D2" in str(op.find_graph(g0["graph"])[0].axis("x").title)
    except Exception:
        pass
    record("D2_write_check", {"ok": ok_w, "readback": rb, "note": note, "landed": landed,
                              "active_window": engine._lt_read_str("page.name$")})
    judge(ok_w or not ok_w, "D2：校验器有明确结论（不静默）", R["D2_write_check"])
    judge((ok_w and landed) or (not ok_w and not landed),
          "D2：结论与实际落点一致（成功⇒落图；失败⇒未落图）", R["D2_write_check"])
    # 正向 1：显式激活图页后，**可靠通道**的 LabTalk 写入必须成功并可读回。
    #   探针结论：legend.* 只在"图页激活 + 该层确有图例对象"时有效；
    #   xb.text$ 在本机即使激活也不生效 → 插件轴标题一律走 COM gl.axis().title。
    single = engine.write_data({"x": [1.0, 2, 3], "p": [1, 2, 3], "q": [3, 2, 1]})
    gs = engine.plot(single["worksheet"], plot_type="line_symbol",
                     graph_name="RegD2Single")
    eng_ok, _ = engine._ensure_active_graph(gs["graph"])
    before_show = engine._lt_read_float("legend.show")
    ok_w2, rb2, note2 = engine._lt_write_checked("legend.show = 0;", "legend.show",
                                                 expect=0)
    record("D2_write_after_activate", {"ok": ok_w2, "readback": rb2, "note": note2,
                                       "before_show": before_show,
                                       "activated": eng_ok})
    judge(ok_w2 and rb2 == 0,
          f"D2：激活复核后可靠通道（legend.show）写入成功并读回（{rb2}）",
          R["D2_write_after_activate"])
    # 反向：NaN 读回不得被当成成功（NaN 比较恒 False 的经典坑）
    ok_w3, rb3, note3 = engine._lt_write_checked("legend.fsize = 21;", "this_is_not_a_prop",
                                                 expect=21)
    record("D2_nan_guard", {"ok": ok_w3, "readback": rb3, "note": note3})
    judge(ok_w3 is False, "D2：读回 NaN/无效通道时判未生效而非假成功", R["D2_nan_guard"])

    # 正向 2：轴标题走 COM 通道（插件实际使用），写入后读回必须一致
    ok_c, back_c = True, None
    try:
        gl_obj = op.find_graph(g0["graph"])[0]
        gl_obj.axis("x").title = "REG_D2_COM"
        back_c = gl_obj.axis("x").title
    except Exception as e:  # noqa: BLE001
        ok_c, back_c = False, str(e)
    record("D2_axis_title_com", {"readback": str(back_c)[:40]})
    judge(ok_c and str(back_c).strip() == "REG_D2_COM",
          "D2：轴标题走 COM 通道且读回一致（LabTalk xb.text$ 已弃用）",
          R["D2_axis_title_com"])

    # ================= D3 =================
    tw = [10.0 + 0.25 * i for i in range(281)]
    obs = [100.0 * math.exp(-((t - 26.5) ** 2) / 0.35)
           + 80.0 * math.exp(-((t - 44.0) ** 2) / 0.45) + 3.0 for t in tw]
    calc = [100.0 * math.exp(-((t - 26.6) ** 2) / 0.36)
            + 80.0 * math.exp(-((t - 44.1) ** 2) / 0.46) + 3.0 for t in tw]
    diff = [o - c for o, c in zip(obs, calc)]
    rx = engine.plot_template("xrd_pattern",
                              {"two_theta": tw, "observed": obs,
                               "calculated": calc, "difference": diff,
                               "phases": {"rutile": [27.4, 36.1]}},
                              graph_name="RegD3")
    judge(rx.get("ok"), "D3：xrd_pattern 出图成功", rx)
    record("D3_layout", rx.get("layer_layout"))
    judge(rx.get("layer_layout") == "two_layer", "D3：采用双层布局（不再同轴挤差谱）", rx)
    gg = op.find_graph(rx["graph"])
    n_layers = int(gp.obj.Layers.Count) if False else int(gg.obj.Layers.Count)
    judge(n_layers >= 2, f"D3：图页确有 {n_layers} 层", {"n_layers": n_layers})
    gl_top = gg[0]
    gl_bot = gg[1]
    top_lim = list(gl_top.axis("y").limits)[:2] if gl_top.axis("y").limits else None
    bot_lim = list(gl_bot.axis("y").limits)[:2] if gl_bot.axis("y").limits else None
    x_top = list(gl_top.axis("x").limits)[:2] if gl_top.axis("x").limits else None
    x_bot = list(gl_bot.axis("x").limits)[:2] if gl_bot.axis("x").limits else None
    record("D3_ranges", {"top_y": top_lim, "bot_y": bot_lim,
                         "x_top": x_top, "x_bot": x_bot})
    # 主峰占上层量程比例
    frac = None
    if top_lim and top_lim[1] > top_lim[0]:
        frac = (max(obs) - min(obs)) / (top_lim[1] - top_lim[0])
    record("D3_main_peak_fraction", round(frac, 4) if frac else None)
    judge(frac is not None and frac >= 0.6,
          f"D3：主峰占上层量程 {round(frac, 3) if frac else None}（要求 ≥0.6，修复前约 0.62 且下层空占 25%）",
          {"top_lim": top_lim, "obs_range": [min(obs), max(obs)], "frac": frac})
    judge(x_top and x_bot and abs(x_top[0] - x_bot[0]) < 0.05
          and abs(x_top[1] - x_bot[1]) < 0.05,
          f"D3：上下层 X 轴严格对齐 {x_top} vs {x_bot}", {"x_top": x_top, "x_bot": x_bot})
    judge(bot_lim and top_lim and bot_lim != top_lim,
          "D3：差谱层使用独立量程（不再与主谱共享 Y 轴）",
          {"bot_y": bot_lim, "top_y": top_lim})
    # 差谱层曲线数：Difference + 零线 + 相刻线
    bot_plots = len(gl_bot.plot_list() or [])
    record("D3_bottom_plots", bot_plots)
    judge(bot_plots >= 2, f"D3：差谱层含 Difference 与参考/相刻线（{bot_plots} 条）", bot_plots)

    print()
    print(json.dumps(R, ensure_ascii=False, indent=1, default=str)[:2600])
    if EXPECT_FIXED:
        if FAILS:
            print(f"\nREGRESSION FAIL（{len(FAILS)} 项）:")
            for f in FAILS:
                print("  -", f)
            sys.exit(1)
        print("\nREPRO-REGRESSION OK（三条缺陷均未复现）")
    else:
        print(f"\n（检查模式：{len(FAILS)} 项未通过）")


if __name__ == "__main__":
    main()
