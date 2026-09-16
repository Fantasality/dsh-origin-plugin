# -*- coding: utf-8 -*-
"""
chemistry_cases —— 化学场景全链路实测（26 案例 × 真机 Origin）
================================================================
从大学物理化学基础实验到 Nature 级科研场景，模拟 AI 拿着 SKILL.md 按流程干活。
每个案例走 SOP 分支，记录工具调用成败、参数返回、拟合精度、发现的缺陷。

tier A = 大学基础 | B = 仪器分析/毕设 | C = 科研/Nature 级

用法:
  <venv python> -X utf8 smoke\\chemistry_cases.py --quick   # 前 2 案例试跑
  <venv python> -X utf8 smoke\\chemistry_cases.py           # 全量
输出: smoke/chemistry_cases_result.json + 控制台逐案例摘要
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import origin_engine as engine  # noqa: E402

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "_chem_out"))
OUT_DIR = os.path.normpath(OUT_DIR)

TOOL_LOG = []
RESULTS = []
RANDOM = random.Random(20260916)


def call(label, fn, *a, **kw):
    """工具调用包装：计时 + 记录成败，异常不中断案例。"""
    t0 = time.time()
    try:
        r = fn(*a, **kw) or {}
        if not isinstance(r, dict):
            r = {"_raw": str(r)[:120]}
        ok = bool(r.get("ok", True))
        TOOL_LOG.append({"tool": label, "ok": ok,
                         "ms": int((time.time() - t0) * 1000)})
        return r
    except Exception as e:  # noqa: BLE001
        TOOL_LOG.append({"tool": label, "ok": False, "ms": 0,
                         "error": f"{type(e).__name__}: {e}"[:250]})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:250]}


def case(cid, tier, desc, fn):
    print(f"\n=== [{cid}] ({tier}) {desc} ===", flush=True)
    t0 = time.time()
    n_tools_before = len(TOOL_LOG)
    problems = []
    try:
        summary = fn(problems) or ""
    except Exception as e:  # noqa: BLE001
        problems.append(f"案例级异常: {type(e).__name__}: {e}"[:250])
        summary = ""
    n_new = len(TOOL_LOG) - n_tools_before
    n_ok = sum(1 for t in TOOL_LOG[n_tools_before:] if t["ok"])
    ok = n_new > 0 and n_ok == n_new and not any("案例级异常" in p for p in problems)
    for t in TOOL_LOG[n_tools_before:]:
        if not t["ok"]:
            problems.append(f"工具失败: {t['tool']}: {t.get('error', '?')[:160]}")
    RESULTS.append({"case": cid, "tier": tier, "desc": desc, "ok": ok,
                    "summary": summary[:400], "problems": problems,
                    "tool_calls": n_new, "tool_ok": n_ok,
                    "seconds": round(time.time() - t0, 1)})
    print(f"--> {'OK' if ok else 'ATTN'} | tools {n_ok}/{n_new} | "
          f"{round(time.time() - t0, 1)}s | {summary[:150]}", flush=True)
    if problems:
        for p in problems:
            print(f"    [问题] {p[:170]}", flush=True)


def png(name):
    return os.path.join(OUT_DIR, name + ".png")


def fit_params(r):
    """抽取 fit 返回的参数字典（结构随 kind 而异，容错提取）。"""
    if not isinstance(r, dict):
        return {}
    for k in ("parameters", "params", "result"):
        v = r.get(k)
        if isinstance(v, dict):
            return {kk: (round(vv, 6) if isinstance(vv, (int, float)) else vv)
                    for kk, vv in list(v.items())[:8]}
    return {}


# ============================================================ A 基础


def c01_beer_lambert(p):
    c = np.array([0.5, 1.0, 1.5, 2.0, 2.5, 3.0])          # μmol/L
    eps_b = 0.42                                            # A/μmol
    a = eps_b * c + np.array([RANDOM.gauss(0, 0.008) for _ in c])
    r = call("write_data", engine.write_data,
             {"concentration_uM": c.tolist(), "absorbance": a.tolist()})
    ws = r.get("worksheet")
    rp = call("plot", engine.plot, ws, y_columns=["absorbance"],
              x_column="concentration_uM", plot_type="line_symbol",
              title="Beer-Lambert 标准曲线")
    g = rp.get("graph")
    rf = call("fit", engine.fit, ws, x_column="concentration_uM",
              y_column="absorbance", kind="linear")
    pr = fit_params(rf)
    slope = pr.get("slope", pr.get("b", 0))
    if slope and abs(slope - eps_b) / eps_b > 0.05:
        p.append(f"拟合斜率 {slope} 与真值 {eps_b} 偏差 >5%")
    rax = call("edit_axis", engine.edit_axis, g, axis="x",
               title="Concentration (μmol/L)")
    ray = call("edit_axis", engine.edit_axis, g, axis="y",
               title="Absorbance (a.u.)")
    rv = call("verify_graph", engine.verify_graph, g,
              expected_x_title="Concentration (μmol/L)")
    re_ = call("export", engine.export, g, fmt="png", file_path=png("c01"))
    return (f"graph={g} 斜率={slope} R2参数={list(pr)[:4]} "
            f"verify_passed={rv.get('passed')} export={re_.get('ok')}")


def c02_first_order(p):
    t = np.arange(0, 900, 60.0)
    k = 0.0042
    conc = 0.8 * np.exp(-k * t) + np.array([RANDOM.gauss(0, 0.004) for _ in t])
    r = call("write_data", engine.write_data,
             {"time_s": t.tolist(), "conc_M": conc.tolist()})
    ws = r.get("worksheet")
    # 先试 transform ln（预期不支持 → 缺陷记录）
    rt = call("transform(ln)", engine.transform, ws, column="conc_M", op="ln")
    if not rt.get("ok"):
        p.append("transform 无 ln/log 算子（一级反应动力学 ln[A]-t 是必经路径，"
                 "AI 只能退回 numpy 自算后 write_data 回写，多两跳）")
    ln_c = np.log(np.clip(conc, 1e-9, None))
    r2 = call("write_data", engine.write_data,
              {"time_s": t.tolist(), "ln_conc": ln_c.tolist()})
    ws2 = r2.get("worksheet")
    rf = call("fit", engine.fit, ws2, x_column="time_s",
              y_column="ln_conc", kind="linear")
    pr = fit_params(rf)
    slope = pr.get("slope", pr.get("b", 0))
    k_fit = -slope if slope else 0
    if abs(k_fit - k) / k > 0.05:
        p.append(f"动力学速率常数拟合 {k_fit:.5f} vs 真值 {k} 偏差>5%")
    call("plot", engine.plot, ws2, y_columns=["ln_conc"], x_column="time_s",
         plot_type="line_symbol", title="一级反应 ln[A]-t")
    return f"k_fit={k_fit:.5f} (真值 {k}), ln通道={'有' if rt.get('ok') else '无'}"


def c03_arrhenius(p):
    R = 8.314
    Ea, A = 52000.0, 1e10
    T = np.array([288.15, 298.15, 308.15, 318.15, 328.15])
    k = A * np.exp(-Ea / (R * T))
    x, y = 1 / T, np.log(k)
    r = call("write_data", engine.write_data,
             {"inv_T": x.tolist(), "ln_k": y.tolist()})
    ws = r.get("worksheet")
    rf = call("fit", engine.fit, ws, x_column="inv_T", y_column="ln_k",
              kind="linear")
    pr = fit_params(rf)
    slope = pr.get("slope", pr.get("b", 0))
    Ea_fit = -slope * R if slope else 0
    call("plot", engine.plot, ws, y_columns=["ln_k"], x_column="inv_T",
         plot_type="line_symbol", title="Arrhenius 图")
    if abs(Ea_fit - Ea) / Ea > 0.01:
        p.append(f"活化能拟合 {Ea_fit:.0f} vs 真值 {Ea:.0f} 偏差>1%")
    return f"Ea_fit={Ea_fit:.0f} J/mol (真值 {Ea:.0f}), params={list(pr)[:5]}"


def c04_titration(p):
    # 0.1M NaOH 滴 25mL 0.1M 醋酸（Ka=1.75e-5），忽略稀释
    Ka, C0, V0 = 1.75e-5, 0.1, 25.0
    vb = np.arange(0, 50.5, 1.0)
    ph = []
    for v in vb:
        n_a, n_b = C0 * V0 / 1000, 0.1 * v / 1000
        if v < 25:
            buf = n_a - n_b
            h = Ka * (buf / max(n_b, 1e-12)) if n_b > 0 else math.sqrt(Ka * C0)
        elif v == 25:
            h = math.sqrt(Ka * C0 / 2)
        else:
            oh = (n_b - n_a)
            h = 1e-14 / max(oh, 1e-15) / 50 * 50
        ph.append(-math.log10(max(h, 1e-14)))
    r = call("write_data", engine.write_data,
             {"V_NaOH_mL": vb.tolist(), "pH": [round(x, 3) for x in ph]})
    ws = r.get("worksheet")
    rd = call("transform(derivative)", engine.transform, ws, column="pH",
              op="derivative")
    if not rd.get("ok"):
        p.append("derivative 失败：滴定突跃一阶导定位是经典教学需求")
    rp = call("plot", engine.plot, ws, y_columns=["pH"], x_column="V_NaOH_mL",
              plot_type="line", title="醋酸滴定曲线")
    call("export", engine.export, rp.get("graph"), fmt="png",
         file_path=png("c04"))
    return f"deriv={'ok' if rd.get('ok') else rd.get('error', 'fail')[:80]} graph={rp.get('graph')}"


def c05_ideal_vdw(p):
    # CO2 313K，Tc=304.1 故仍是气态
    R, T, n = 8.314, 313.15, 1.0
    a, b = 0.364, 4.27e-5
    V = np.linspace(0.05, 0.5, 24)
    p_id = n * R * T / V / 1e5
    p_vdw = (n * R * T / (V - n * b) - a * n * n / V ** 2) / 1e5
    r = call("write_data", engine.write_data,
             {"V_m3": V.tolist(), "p_ideal_bar": p_id.tolist(),
              "p_vdw_bar": p_vdw.tolist()})
    rp = call("plot", engine.plot, r.get("worksheet"),
              y_columns=["p_ideal_bar", "p_vdw_bar"], x_column="V_m3",
              plot_type="line", title="理想气体 vs 范德华 (CO2, 313K)")
    g = rp.get("graph")
    call("edit_axis", engine.edit_axis, g, axis="x", title="V (m³)")
    call("edit_axis", engine.edit_axis, g, axis="y", title="p (bar)")
    rv = call("verify_graph", engine.verify_graph, g, expected_series=2)
    return f"graph={g} verify={rv.get('passed')}"


def c06_solubility(p):
    T = np.array([0, 10, 20, 30, 40, 50, 60, 70, 80], float)
    s = 13.3 * np.exp(0.0335 * T)   # KNO3 近似文献曲线
    s = s * (1 + np.array([RANDOM.gauss(0, 0.015) for _ in T]))
    r = call("write_data", engine.write_data,
             {"T_C": T.tolist(), "solubility_g_100g": s.round(3).tolist()})
    ws = r.get("worksheet")
    rf = call("fit", engine.fit, ws, x_column="T_C",
              y_column="solubility_g_100g", kind="ExpGrow1" if False else "linear")
    pr = fit_params(rf)
    call("plot", engine.plot, ws, y_columns=["solubility_g_100g"],
         x_column="T_C", plot_type="line_symbol", title="KNO3 溶解度曲线")
    return f"fit={rf.get('ok')} params_keys={list(pr)[:5]}"


def c07_buffer(p):
    # Henderson-Hasselbalch：pKa=4.76, [A-]/[HA] 随加碱体积变化
    v = np.arange(0, 50.5, 2.0)
    ratio = np.clip(v, 1e-6, None) / np.clip(50 - v, 1e-6, None)
    ph = 4.76 + np.log10(np.where(v >= 50, 1e6, ratio))
    r = call("write_data", engine.write_data,
             {"Vbase_mL": v.tolist(), "pH": ph.round(3).tolist()})
    rp = call("plot", engine.plot, r.get("worksheet"), y_columns=["pH"],
              x_column="Vbase_mL", plot_type="line",
              title="H-H 缓冲液 pH")
    g = rp.get("graph")
    rtx = call("add_text", engine.add_text, g, "pKa = 4.76 (醋酸)", x=25, y=6.0)
    return f"add_text={rtx.get('ok')} graph={g}"


def c08_phase_tx(p):
    x = np.arange(0, 1.01, 0.05)
    # 理想双组分（苯-甲苯近似）：泡点/露点线
    tb, ta = 383.8, 384.9  # K
    bp = 1 / (x / ta + (1 - x) / tb)
    dp = 1 / ((x * 2.0 / ta) + ((1 - x) * 0.5) / tb)
    r = call("write_data", engine.write_data,
             {"x_B": x.round(2).tolist(), "bubble_T_K": bp.round(2).tolist(),
              "dew_T_K": dp.round(2).tolist()})
    rp = call("plot", engine.plot, r.get("worksheet"),
              y_columns=["bubble_T_K", "dew_T_K"], x_column="x_B",
              plot_type="line", title="双组分气液相图")
    g = rp.get("graph")
    call("edit_plot", engine.edit_plot, g,
         [{"plot": 1, "line_style": 1}])
    call("edit_axis", engine.edit_axis, g, axis="x", title="x(B 摩尔分数)")
    call("edit_axis", engine.edit_axis, g, axis="y", title="T (K)")
    call("add_text", engine.add_text, g, "两相区", x=0.5, y=383.5)
    rv = call("verify_graph", engine.verify_graph, g, expected_series=2)
    return f"graph={g} verify={rv.get('passed')}"


# ============================================================ B 中级


def c09_uvvis_series(p):
    lam = np.arange(200, 501, 2.0)
    specs = {}
    for i, c in enumerate([0.1, 0.2, 0.3, 0.4, 0.5]):
        y = (c * np.exp(-((lam - 273) ** 2) / (2 * 22 ** 2))
             + 0.4 * c * np.exp(-((lam - 220) ** 2) / (2 * 15 ** 2))
             + RANDOM.gauss(0, 0.003) * 0.1)
        specs[f"{c:.1f} μM"] = y.round(4).tolist()
    r = call("plot_template(stacked_spectra)", engine.plot_template,
             "stacked_spectra", {"x": lam.tolist(), "spectra": specs},
             title="浓度系列 UV-Vis 谱", fmt="png", file_path=png("c09"))
    g = r.get("graph")
    rv = call("verify_graph", engine.verify_graph, g, expected_series=5)
    return f"graph={g} verify={rv.get('passed')} issues={rv.get('issues', [])[:2]}"


def c10_xrd_rietveld(p):
    tt = np.arange(10, 80.05, 0.02)
    peaks = [(25.32, 100), (37.80, 35), (48.05, 30), (53.90, 18),
             (55.07, 14), (62.69, 20), (68.76, 8), (70.31, 6), (75.03, 4)]
    calc = np.zeros_like(tt)
    for pos, inten in peaks:
        calc += inten * np.exp(-((tt - pos) ** 2) / (2 * 0.06 ** 2))
    calc += 8 + 40 * np.exp(-tt / 25)
    obs = calc + np.array([RANDOM.gauss(0, 4.5) for _ in tt])
    diff = obs - calc
    r = call("plot_template(xrd_pattern)", engine.plot_template, "xrd_pattern",
             {"two_theta": tt.tolist(), "observed": obs.round(2).tolist(),
              "calculated": calc.round(2).tolist(),
              "difference": diff.round(2).tolist(),
              "phases": {"锐钛矿 TiO2": [25.32, 37.80, 48.05, 53.90]}},
             title="锐钛矿 TiO2 XRD Rietveld", fmt="png", file_path=png("c10"))
    g = r.get("graph")
    ri = call("inspect_graph", engine.inspect_graph, g)
    rv = call("verify_graph", engine.verify_graph, g)
    layers = ri.get("layers") or []
    return (f"graph={g} layers={len(layers)} verify_passed={rv.get('passed')} "
            f"verify_checks={len(rv.get('checks', []))}")


def c11_ftir_delta(p):
    w = np.arange(4000, 400.0, -4.0)
    def band(x, c, w0, fwhm):
        return c * np.exp(-((x - w0) ** 2) / (2 * (fwhm / 2.355) ** 2))
    before = (band(w, 0.95, 3350, 60) + band(w, 1.0, 1650, 50)
              + band(w, 0.6, 1540, 55) + band(w, 0.5, 1240, 45))
    after = (band(w, 0.95, 3350, 60) + band(w, 0.42, 1650, 50)
             + band(w, 0.72, 1540, 55) + band(w, 0.5, 1240, 45)
             + band(w, 0.30, 1715, 40))
    r = call("write_data", engine.write_data,
             {"wavenumber_cm": w.round(1).tolist(), "before": before.round(4).tolist(),
              "after": after.round(4).tolist()})
    rp = call("plot", engine.plot, r.get("worksheet"),
              y_columns=["before", "after"], x_column="wavenumber_cm",
              plot_type="line", title="FTIR 反应前后对比")
    g = rp.get("graph")
    call("edit_plot", engine.edit_plot, g, [{"plot": 0, "transparency": 35}])
    call("add_text", engine.add_text, g, "酰胺 I 带 1650", x=1650, y=1.05)
    call("add_text", engine.add_text, g, "新羰基 1715", x=1715, y=0.35)
    call("edit_axis", engine.edit_axis, g, axis="x",
         title="Wavenumber (cm-1)", from_value=4000, to_value=400)
    return f"graph={g} (FTIR x 轴应 4000→400 反向)"


def c12_fluorescence(p):
    ex = np.arange(300, 421, 2.0)
    em = np.arange(380, 651, 2.0)
    exc = 0.8 * np.exp(-((ex - 370) ** 2) / (2 * 25 ** 2)) + 0.3 * np.exp(
        -((ex - 315) ** 2) / (2 * 18 ** 2))
    emi = 0.95 * np.exp(-((em - 465) ** 2) / (2 * 32 ** 2))
    r = call("plot_template(dual_y)", engine.plot_template, "dual_y",
             {"x": np.concatenate([ex, em]).tolist(),
              "left": np.concatenate([exc, np.full_like(em, np.nan)]).tolist(),
              "right": np.concatenate([np.full_like(ex, np.nan), emi]).tolist(),
              "left_name": "激发光谱 (λem=465nm)",
              "right_name": "发射光谱 (λex=370nm)"},
             title="荧光 激发/发射", fmt="png", file_path=png("c12"))
    return f"graph={r.get('graph')} ok={r.get('ok')} (NaN 拼接双谱尝试)"


def c13_tga_dtg(p):
    T = np.arange(30, 800.5, 5.0)
    w = 100 - 38 / (1 + np.exp(-(T - 240) / 18)) - 22 / (1 + np.exp(-(T - 480) / 25))
    w = w + np.array([RANDOM.gauss(0, 0.15) for _ in T])
    r = call("write_data", engine.write_data,
             {"T_C": T.tolist(), "mass_pct": w.round(3).tolist()})
    ws = r.get("worksheet")
    rd = call("transform(derivative)", engine.transform, ws, column="mass_pct",
              op="derivative")
    if not rd.get("ok"):
        p.append("derivative 失败：DTG 曲线是 TGA 标配")
    rp = call("plot_template(dual_y)", engine.plot_template, "dual_y",
              {"x": T.tolist(), "left": w.round(3).tolist(),
               "right": [-x for x in (rd.get("values") or [])] if rd.get("ok") else (100 - w).round(3).tolist(),
               "left_name": "质量 (%)", "right_name": "DTG (%/°C)"},
              title="TGA/DTG", fmt="png", file_path=png("c13"))
    return f"deriv={rd.get('ok')} graph={rp.get('graph')}"


def c14_dsc(p):
    T = np.arange(30, 250.5, 1.0)
    base = 0.02 * T
    melt = 12 * np.exp(-((T - 156.5) ** 2) / (2 * 3.2 ** 2))
    cp = base + melt + np.array([RANDOM.gauss(0, 0.12) for _ in T])
    r = call("write_data", engine.write_data,
             {"T_C": T.tolist(), "heat_flow_mW": cp.round(3).tolist()})
    ws = r.get("worksheet")
    rpk = call("peak_find", engine.peak_find, ws, x_column="T_C",
               y_column="heat_flow_mW", min_height=6)
    rin = call("integrate", engine.integrate, ws, x_column="T_C",
               y_column="heat_flow_mW")
    auc = rin.get("area") or rin.get("auc") or 0
    rpn = rpk.get("peaks") or rpk.get("n_peaks") or 0
    call("plot", engine.plot, ws, y_columns=["heat_flow_mW"], x_column="T_C",
         plot_type="line", title="DSC 熔融峰 (PE)")
    return f"peaks={rpn if isinstance(rpn, int) else len(rpk.get('peaks', []))} area={auc if isinstance(auc, float) else str(auc)[:60]}"


def c15_cv(p):
    E = np.concatenate([np.arange(-0.2, 0.801, 0.01), np.arange(0.80, -0.199, -0.01)])
    Epa, Epc, ip = 0.52, 0.44, 42.0
    i = (ip * np.exp(-((E - Epa) ** 2) / (2 * 0.06 ** 2))
         - 34 * np.exp(-((E - Epc) ** 2) / (2 * 0.06 ** 2)))
    i = i + np.array([RANDOM.gauss(0, 0.8) for _ in E])
    r = call("write_data", engine.write_data,
             {"E_V": E.round(3).tolist(), "i_uA": i.round(2).tolist()})
    rp = call("plot", engine.plot, r.get("worksheet"), y_columns=["i_uA"],
              x_column="E_V", plot_type="line", title="循环伏安 CV (可逆)")
    g = rp.get("graph")
    call("edit_axis", engine.edit_axis, g, axis="y", title="i (μA)")
    call("add_text", engine.add_text, g, "ΔEp≈59mV 可逆判据", x=0.1, y=38)
    return f"graph={g}"


def c16_particle(p):
    n = 220
    d = np.random.default_rng(7).lognormal(mean=math.log(38), sigma=0.22, size=n)
    r = call("write_data", engine.write_data,
             {"diameter_nm": d.round(2).tolist()})
    ws = r.get("worksheet")
    rh = call("histogram", engine.histogram, ws, column="diameter_nm",
              bins=22, plot=True, file_path=png("c16"))
    rs = call("stats", engine.stats, ws, columns=["diameter_nm"])
    st = rs.get("stats", {}).get("diameter_nm", rs.get("stats", {}))
    mean = st.get("mean", 0) if isinstance(st, dict) else 0
    return (f"hist={rh.get('ok')} mean={mean} "
            f"count={st.get('count', '?') if isinstance(st, dict) else '?'}")


def c17_kinetics_compare(p):
    t = np.arange(0, 601, 30.0)
    k1, k2 = 0.006, 0.0022
    a1 = 1.0 * np.exp(-k1 * t)
    a2 = 1.0 / (1 + k2 * t * 1.0)
    a1 = a1 + np.array([RANDOM.gauss(0, 0.008) for _ in t])
    a2 = a2 + np.array([RANDOM.gauss(0, 0.008) for _ in t])
    r = call("write_data", engine.write_data,
             {"t_min": t.tolist(), "A1_first": a1.round(4).tolist(),
              "A2_second": a2.round(4).tolist()})
    ws = r.get("worksheet")
    rf1 = call("fit1", engine.fit, ws, x_column="t_min",
               y_column="A1_first", kind="linear")
    call("plot", engine.plot, ws, y_columns=["A1_first", "A2_second"],
         x_column="t_min", plot_type="line_symbol", title="一级 vs 二级反应")
    return f"fit1={rf1.get('ok')} (二级动力学需 1/[A] 变换，又无 ln/reciprocal 算子)"


# ============================================================ C 高级


def c18_catalyst_arrhenius(p):
    R = 8.314
    T = np.array([293.15, 303.15, 313.15, 323.15, 333.15])
    cats = {"Pt/C": 68000, "Ru/C": 54000, "Pd/C": 76000, "Ni foam": 88000}
    cols = {"inv_T": (1 / T).tolist()}
    for name, ea in cats.items():
        k = 1e8 * np.exp(-ea / (R * T))
        k = k * (1 + np.array([RANDOM.gauss(0, 0.03) for _ in T]))
        cols[f"k_{name.replace('/', '_')}"] = np.log(k).round(5).tolist()
    r = call("write_data", engine.write_data, cols)
    ws = r.get("worksheet")
    ycols = [c for c in cols if c.startswith("k_")]
    rp = call("plot", engine.plot, ws, y_columns=ycols, x_column="inv_T",
              plot_type="line_symbol", title="多催化剂 Arrhenius 对比")
    g = rp.get("graph")
    ri = call("inspect_graph", engine.inspect_graph, g)
    n_plots = len(ri.get("plots") or ri.get("layers", [{}])[0].get("plots", [])) if ri.get("ok") else 0
    call("edit_plot", engine.edit_plot, g,
         [{"plot": 2, "line_style": 1, "color": "#882255"}])
    rv = call("verify_graph", engine.verify_graph, g, expected_series=4)
    # 顺带做显著性：Pt/C vs Ni foam 的 ln k 差
    rt = call("ttest", engine.ttest, ws, column_a=ycols[0], column_b=ycols[-1],
              kind="two")
    return (f"graph={g} verify={rv.get('passed')} ttest={rt.get('ok')} "
            f"(Ea 排序应 Pt<C<Ru<Pd... 视觉可查斜率)")


def c19_bet(p):
    pp0 = np.arange(0.05, 0.96, 0.02)
    # IV 型等温 + H2 回滞环
    ads = (60 * pp0 / (1 + 30 * pp0 ** 3) + 140 * pp0 ** 8) * 1.0
    des = np.interp(pp0, [0, 0.42, 0.45, 0.60, 0.95],
                    [0, 27, 45, 120, 200])
    ads = ads + np.array([RANDOM.gauss(0, 1.2) for _ in pp0])
    des = des + np.array([RANDOM.gauss(0, 1.2) for _ in pp0])
    r = call("write_data", engine.write_data,
             {"p_over_p0": pp0.round(3).tolist(),
              "adsorption": ads.round(2).tolist(),
              "desorption": des.round(2).tolist()})
    rp = call("plot", engine.plot, r.get("worksheet"),
              y_columns=["adsorption", "desorption"], x_column="p_over_p0",
              plot_type="line_symbol", title="N2 吸附/脱附 BET (IV-H2)")
    g = rp.get("graph")
    call("edit_plot", engine.edit_plot, g,
         [{"plot": 1, "line_style": 1, "symbol_kind": 3}])
    call("edit_axis", engine.edit_axis, g, axis="x",
         title="p/p0", from_value=0, to_value=1.0)
    call("edit_axis", engine.edit_axis, g, axis="y",
         title="Volume (cm³/g STP)")
    call("add_text", engine.add_text, g, "H2 回滞环", x=0.55, y=120)
    rv = call("verify_graph", engine.verify_graph, g, expected_series=2)
    return f"graph={g} verify={rv.get('passed')}"


def c20_insitu_xrd(p):
    tt = np.arange(20, 70.05, 0.04)
    temps = [25, 100, 200, 300, 400, 500]
    specs = {}
    for Tc in temps:
        anat = np.exp(-((tt - 25.32) ** 2) / (2 * 0.07 ** 2))
        rut = np.exp(-((tt - 27.45) ** 2) / (2 * 0.07 ** 2))
        frac = 1 / (1 + np.exp(-(Tc - 280) / 35))     # 300°C 起相变
        y = (1 - frac) * anat + frac * rut * 1.15
        y = y * (1 - 0.0004 * Tc) + np.array([RANDOM.gauss(0, 0.01) for _ in tt])
        specs[f"{Tc}°C"] = y.round(4).tolist()
    r = call("plot_template(stacked_spectra)", engine.plot_template,
             "stacked_spectra", {"x": tt.tolist(), "spectra": specs},
             title="原位 XRD 温度系列 (锐钛矿→金红石)",
             fmt="png", file_path=png("c20"))
    return f"graph={r.get('graph')} ok={r.get('ok')}"


def c21_stability(p):
    cyc = np.arange(1, 501, 5.0)
    cap = 200 * np.exp(-0.0006 * cyc) * (1 - 0.02 * (cyc > 300) * (cyc - 300) / 200)
    cap = cap + np.array([RANDOM.gauss(0, 0.35) for _ in cyc])
    ce = np.clip(98.5 + 0.006 * cyc, 0, 100) + np.array([RANDOM.gauss(0, 0.08) for _ in cyc])
    r = call("plot_template(dual_y)", engine.plot_template, "dual_y",
             {"x": cyc.tolist(), "left": cap.round(2).tolist(),
              "right": ce.round(3).tolist(),
              "left_name": "放电比容量 (mAh/g)",
              "right_name": "库仑效率 (%)"},
             title="长循环稳定性 500 cycles", fmt="png", file_path=png("c21"))
    g = r.get("graph")
    rv = call("verify_graph", engine.verify_graph, g, expected_series=2)
    call("edit_axis", engine.edit_axis, g, axis="y2", layer=1,
         from_value=97, to_value=100.5)
    return f"graph={g} verify={rv.get('passed')} (y2 次轴范围编辑)"


def c22_kie(p):
    R = 8.314
    T = np.array([288.15, 298.15, 308.15, 318.15])
    EaH, EaD = 40000.0, 47000.0
    kH = 2e9 * np.exp(-EaH / (R * T))
    kD = 2e9 * np.exp(-EaD / (R * T))
    r = call("write_data", engine.write_data,
             {"inv_T": (1 / T).tolist(),
              "ln_kH": np.log(kH).round(5).tolist(),
              "ln_kD": np.log(kD).round(5).tolist()})
    ws = r.get("worksheet")
    rfH = call("fit(kH)", engine.fit, ws, x_column="inv_T",
               y_column="ln_kH", kind="linear")
    rfD = call("fit(kD)", engine.fit, ws, x_column="inv_T",
               y_column="ln_kD", kind="linear")
    prH, prD = fit_params(rfH), fit_params(rfD)
    sH, sD = prH.get("slope", 0), prD.get("slope", 0)
    # ln k = ln A - Ea/(R*T) => slope = -Ea/R；kH/kD(T) = exp((sH - sD) / T)
    kie_exp = math.exp((sH - sD) / 298.15) if sH and sD else 0
    rp = call("plot", engine.plot, ws, y_columns=["ln_kH", "ln_kD"],
              x_column="inv_T", plot_type="line_symbol",
              title="KIE: H vs D 阿伦尼乌斯")
    call("edit_legend", engine.edit_legend, rp.get("graph"),
         {"position": "tr", "font_size_pt": 10})
    kie_true = math.exp((EaD - EaH) / (R * 298.15))
    if abs(kie_exp - kie_true) / kie_true > 0.02:
        p.append(f"KIE 拟合 {kie_exp:.2f} vs 真值 {kie_true:.2f} 偏差 >2%")
    return (f"kie_fit={kie_exp:.2f} vs 真值 {kie_true:.2f} "
            f"(298K, Ea差 7kJ/mol)")


def c23_dft_dos(p):
    E = np.arange(-8, 4.01, 0.05)
    def g(x, mu, w, a):
        return a * np.exp(-((x - mu) ** 2) / (2 * w ** 2))
    panels = {
        "s 态": (g(E, -3.2, 0.7, 1.0) + 0.6 * g(E, 1.2, 0.9, 0.8)).round(4).tolist(),
        "p 态": (g(E, -1.5, 1.0, 1.4) + 0.5 * g(E, 2.0, 1.1, 0.9)).round(4).tolist(),
        "d 态": (g(E, -2.0, 0.5, 2.0) + g(E, 0.5, 1.3, 1.1)
                 + g(E, 1.8, 0.6, 0.7)).round(4).tolist(),
    }
    r = call("plot_template(multi_panel)", engine.plot_template, "multi_panel",
             {"x": E.tolist(), "panels": panels},
             title="DFT 分波态密度 PDOS", fmt="png", file_path=png("c23"))
    g = r.get("graph")
    rv = call("verify_graph", engine.verify_graph, g)
    call("edit_axis", engine.edit_axis, g, axis="x", layer=2,
         title="E - Ef (eV)")
    return f"graph={g} verify={rv.get('passed')}"


def c24_michaelis_menten(p):
    S = np.array([0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3.0, 5.0])
    Vmax, Km = 95.0, 0.35
    v = Vmax * S / (Km + S)
    v = v + np.array([RANDOM.gauss(0, 1.2) for _ in S])
    r = call("write_data", engine.write_data,
             {"S_mM": S.tolist(), "v_uM_min": v.round(3).tolist()})
    ws = r.get("worksheet")
    rf = call("fit(MM)", engine.fit, ws, x_column="S_mM",
              y_column="v_uM_min", kind="MichaelisMenten")
    if not rf.get("ok"):
        p.append("fit 无 Michaelis-Menten 预设（酶动力学核心模型）；尝试 Origin "
                 "内置名也失败 → AI 需要知道可用 NLFit 模型名清单或支持自定义公式")
        rf2 = call("fit(DoseResp)", engine.fit, ws, x_column="S_mM",
                   y_column="v_uM_min", kind="DoseResponse")
        pr = fit_params(rf2)
    else:
        pr = fit_params(rf)
    call("plot", engine.plot, ws, y_columns=["v_uM_min"], x_column="S_mM",
         plot_type="line_symbol", title="Michaelis-Menten 酶动力学")
    return f"MM拟合ok={rf.get('ok')} params={list(pr)[:6]}"


def c25_yield_forest(p):
    labels = ["Pd-xantphos", "Pd-PPh3", "Ni-cat", "CuI", "FeCl3",
              "Rh-cat", "Ir-cat", " organo"]
    eff = np.array([92, 85, 78, 65, 55, 88, 90, 71], float)
    rng = np.random.default_rng(3)
    lo = eff - rng.uniform(3, 9, 8)
    hi = eff + rng.uniform(3, 9, 8)
    r = call("plot_template(forest)", engine.plot_template, "forest",
             {"labels": labels, "effect": eff.tolist(),
              "ci_low": lo.round(1).tolist(), "ci_high": hi.round(1).tolist()},
             title="催化剂筛选产率 (森林图)", fmt="png", file_path=png("c25"))
    return f"graph={r.get('graph')} ok={r.get('ok')}"


def c26_response_surface(p):
    T = np.arange(60, 121, 8.0)      # °C（9 个）
    hh = np.arange(1, 13, 0.8)       # h（15 个）
    TT, HH = np.meshgrid(T, hh)      # (15, 9)：行=y(h)、列=x(T) 标准方向
    Y = 95 * np.exp(-((TT - 95) ** 2 / 900) - ((HH - 6.5) ** 2 / 9))
    Y = Y + np.array([RANDOM.gauss(0, 0.5) for _ in range(Y.size)]).reshape(Y.shape)
    # 先试"AI 直觉写法"（行=x 方向，插件应自动转置），失败再退标准方向
    Y_row_x = Y.T
    r = call("plot3d", engine.plot3d,
             {"x": T.tolist(), "y": hh.tolist(),
              "z": Y_row_x.round(2).tolist()},
             plot_type="surface", fmt="png",
             file_path=os.path.join(OUT_DIR, "c26.png"))
    if not r.get("ok"):
        r = call("plot3d(标准方向)", engine.plot3d,
                 {"x": T.tolist(), "y": hh.tolist(),
                  "z": Y.round(2).tolist()},
                 plot_type="surface", fmt="png",
                 file_path=os.path.join(OUT_DIR, "c26.png"))
    return f"ok={r.get('ok')} graph={r.get('graph')} ({Y.size} 点响应面，行=x 自动转置)"


# ============================================================ main


def main():
    quick = "--quick" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    r0 = call("status", engine.status)
    if not r0.get("ok"):
        print("FATAL: Origin 连接失败:", json.dumps(r0, ensure_ascii=False)[:300])
        sys.exit(2)
    print("Origin connected. out dir:", OUT_DIR)

    lp0 = call("list_pages(前)", engine.list_pages)
    def _names(pages):
        out = []
        for x in (pages or []):
            out.append(x["name"] if isinstance(x, dict) else x)
        return set(out)
    pages_before = _names(lp0.get("pages"))

    cases = [
        ("c01_beer_lambert", "A", "UV-Vis 标准曲线+线性拟合", c01_beer_lambert),
        ("c02_first_order", "A", "一级反应动力学 ln[A]-t", c02_first_order),
        ("c03_arrhenius", "A", "Arrhenius 活化能", c03_arrhenius),
        ("c04_titration", "A", "酸碱滴定曲线+一阶导", c04_titration),
        ("c05_ideal_vdw", "A", "理想气体 vs 范德华", c05_ideal_vdw),
        ("c06_solubility", "A", "KNO3 溶解度曲线", c06_solubility),
        ("c07_buffer", "A", "H-H 缓冲液 pH", c07_buffer),
        ("c08_phase_tx", "A", "双组分 T-x 相图", c08_phase_tx),
        ("c09_uvvis_series", "B", "浓度系列 UV-Vis 堆叠谱", c09_uvvis_series),
        ("c10_xrd_rietveld", "B", "XRD Rietveld 三件套", c10_xrd_rietveld),
        ("c11_ftir_delta", "B", "FTIR 反应前后对比", c11_ftir_delta),
        ("c12_fluorescence", "B", "荧光激发/发射双谱", c12_fluorescence),
        ("c13_tga_dtg", "B", "TGA/DTG 热重", c13_tga_dtg),
        ("c14_dsc", "B", "DSC 熔融峰:找峰+积分", c14_dsc),
        ("c15_cv", "B", "循环伏安 CV", c15_cv),
        ("c16_particle", "B", "纳米粒径分布直方图", c16_particle),
        ("c17_kinetics_compare", "B", "一级 vs 二级反应对比", c17_kinetics_compare),
        ("c18_catalyst_arrhenius", "C", "多催化剂 Arrhenius+t检验", c18_catalyst_arrhenius),
        ("c19_bet", "C", "BET 吸附回滞环+虚线编辑", c19_bet),
        ("c20_insitu_xrd", "C", "原位 XRD 温度系列", c20_insitu_xrd),
        ("c21_stability", "C", "电池长循环 dual_y+次轴", c21_stability),
        ("c22_kie", "C", "KIE 同位素效应拟合", c22_kie),
        ("c23_dft_dos", "C", "DFT PDOS 多面板", c23_dft_dos),
        ("c24_michaelis_menten", "C", "Michaelis-Menten 非线性拟合", c24_michaelis_menten),
        ("c25_yield_forest", "C", "催化剂筛选森林图", c25_yield_forest),
        ("c26_response_surface", "C", "响应面 3D", c26_response_surface),
    ]
    if quick:
        cases = cases[:2]

    for cid, tier, desc, fn in cases:
        case(cid, tier, desc, fn)

    # ---- 交付与清理 ----
    if not quick:
        rsave = call("save_project", engine.save_project,
                     os.path.join(OUT_DIR, "chem_cases.opju"))
        lp1 = call("list_pages(后)", engine.list_pages)
        new_pages = [x for x in _names(lp1.get("pages")) if x not in pages_before]
        rcl = call("manage_pages(close 清理)", engine.manage_pages, "close",
                   new_pages[:10])
        # 分批最多10个，剩余再关一轮
        rest = new_pages[10:]
        if rest:
            call("manage_pages(close 清理2)", engine.manage_pages, "close", rest[:10])
            if len(rest) > 10:
                p_note = f"清理剩余 {len(rest) - 10} 页未关（批上限）"
                RESULTS.append({"case": "zz_cleanup", "tier": "-",
                                "desc": "窗口清理", "ok": True,
                                "summary": p_note, "problems": [],
                                "tool_calls": 0, "tool_ok": 0, "seconds": 0})
        print(f"\n新页面 {len(new_pages)} 个, 关闭={rcl.get('ok')}, "
              f"save={rsave.get('ok')} -> {OUT_DIR}/chem_cases.opju")

    # ---- 摘要 ----
    n_ok = sum(1 for c in RESULTS if c["ok"])
    n_p = sum(len(c["problems"]) for c in RESULTS)
    n_tools = len(TOOL_LOG)
    n_tools_ok = sum(1 for t in TOOL_LOG if t["ok"])
    print(f"\n{'=' * 60}\n案例 {n_ok}/{len(RESULTS)} 全绿 | "
          f"工具调用 {n_tools_ok}/{n_tools} 成功 | 问题记录 {n_p} 条")
    for c in RESULTS:
        mark = "OK  " if c["ok"] else "ATTN"
        print(f"[{mark}] {c['case']:<28} {c['tool_ok']}/{c['tool_calls']} "
              f"| {c['summary'][:110]}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "chemistry_cases_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"results": RESULTS, "tool_log": TOOL_LOG,
                   "summary": {"cases_ok": n_ok, "cases_total": len(RESULTS),
                               "tools_ok": n_tools_ok, "tools_total": n_tools,
                               "problems_total": n_p}},
                  f, ensure_ascii=False, indent=1, default=str)
    print("\nresult ->", out)


if __name__ == "__main__":
    main()
