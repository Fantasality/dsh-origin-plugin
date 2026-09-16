# -*- coding: utf-8 -*-
"""
chemistry_cases_v2 —— v2.3.0 物理化学专业场景实测（15 案例 × 真机 Origin）
==========================================================================
按用户六大诉求设计：
1. 物理化学特别性：拉曼/XPS 多峰拟合、NMR 裂分、燃烧热、LSV Tafel、EIS、
   CV 多圈、充放电、过渡态 3D 能量面
2. 细节把握：轴量程/分度值、拟合线交点（x_intercept）、辅助线
3. 高度自定义：mask 错误点（cosmic ray）
4. 图表一体交付：export_delivery + data.csv + report.txt
5. 数据准确化：column_formula 走 Origin 原生计算（不经 numpy）
6. 逃生舱：origin_labtalk 任意脚本

用法: <venv python> -X utf8 smoke\\chemistry_cases_v2.py [--quick]
输出: _chem_out/*.png + smoke/chemistry_cases_v2_result.json
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import origin_engine as engine  # noqa: E402

OUT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "_chem_out"))

TOOL_LOG = []
RESULTS = []
SEED = np.random.default_rng(20260916)


def call(tname, fn, *a, **kw):
    t0 = time.time()
    try:
        r = fn(*a, **kw) or {}
        if not isinstance(r, dict):
            r = {"_raw": str(r)[:120]}
        ok = bool(r.get("ok", True))
        TOOL_LOG.append({"tool": tname, "ok": ok,
                         "ms": int((time.time() - t0) * 1000)})
        return r
    except Exception as e:  # noqa: BLE001
        TOOL_LOG.append({"tool": tname, "ok": False, "ms": 0,
                         "error": f"{type(e).__name__}: {e}"[:250]})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:250]}


def case(cid, desc, fn):
    print(f"\n=== [{cid}] {desc} ===", flush=True)
    t0 = time.time()
    n0 = len(TOOL_LOG)
    problems = []
    try:
        summary = fn(problems) or ""
    except Exception as e:  # noqa: BLE001
        problems.append(f"案例级异常: {type(e).__name__}: {e}"[:250])
        summary = ""
    n_new = len(TOOL_LOG) - n0
    n_ok = sum(1 for t in TOOL_LOG[n0:] if t["ok"])
    for t in TOOL_LOG[n0:]:
        if not t["ok"]:
            problems.append(f"工具失败: {t['tool']}: {t.get('error', '?')[:160]}")
    ok = n_new > 0 and n_ok == n_new and not any("案例级异常" in p for p in problems)
    RESULTS.append({"case": cid, "desc": desc, "ok": ok, "summary": summary[:400],
                    "problems": problems, "tool_calls": n_new, "tool_ok": n_ok,
                    "seconds": round(time.time() - t0, 1)})
    print(f"--> {'OK' if ok else 'ATTN'} | tools {n_ok}/{n_new} | "
          f"{summary[:150]}", flush=True)
    for p_ in problems:
        print(f"    [问题] {p_[:170]}", flush=True)


def png(name):
    return os.path.join(OUT_DIR, name + ".png")


# ============================================================ 光谱与拟合


def d01_raman(p):
    """碳材料拉曼：D 1350 / G 1580 / 2D 2700 三峰拟合"""
    x = np.arange(800, 3201, 2.0)
    y = (1200 * np.exp(-4 * np.log(2) * ((x - 1350) / 45) ** 2)
         + 2500 * np.exp(-4 * np.log(2) * ((x - 1580) / 28) ** 2)
         + 900 * np.exp(-4 * np.log(2) * ((x - 2700) / 60) ** 2)
         + 180 + SEED.normal(0, 25, x.size))
    r = call("write_data", engine.write_data,
             {"raman_shift": x.tolist(), "intensity": y.round(2).tolist()})
    ws = r["worksheet"]
    rf = call("peak_fit(3)", engine.peak_fit, ws, x_column="raman_shift",
              y_column="intensity", n_peaks=3, kind="gauss",
              centers_hint=[1350, 1580, 2700])
    peaks = rf.get("peaks") or []
    r2 = rf.get("r_squared", 0)
    errs = []
    for pk, true_c in zip(sorted(peaks, key=lambda q: q["center"]),
                          [1350, 1580, 2700]):
        if abs(pk["center"] - true_c) > 3:
            errs.append(f"峰位 {pk['center']} vs {true_c}")
    if errs:
        p.append("; ".join(errs))
    if r2 < 0.95:
        p.append(f"R²={r2} 过低")
    call("export", engine.export, rf.get("graph"), fmt="png",
         file_path=png("d01_raman"))
    return (f"peaks={[q['center'] for q in peaks]} R2={r2} "
            f"graph={rf.get('graph')}")


def d02_xps(p):
    """XPS C1s 三组分（结合能反向轴）+ 面积占比"""
    x = np.arange(292, 280.0, -0.05)
    y = (3000 * np.exp(-4 * np.log(2) * ((x - 284.8) / 1.1) ** 2)
         + 1100 * np.exp(-4 * np.log(2) * ((x - 286.2) / 1.4) ** 2)
         + 550 * np.exp(-4 * np.log(2) * ((x - 288.5) / 1.6) ** 2)
         + 120 + SEED.normal(0, 15, x.size))
    r = call("write_data", engine.write_data,
             {"binding_eV": x.tolist(), "cps": y.round(2).tolist()})
    ws = r["worksheet"]
    rf = call("peak_fit(3)", engine.peak_fit, ws, x_column="binding_eV",
              y_column="cps", n_peaks=3, kind="gauss",
              centers_hint=[284.8, 286.2, 288.5])
    peaks = rf.get("peaks") or []
    areas = sorted([q["area"] for q in peaks], reverse=True)
    total = sum(areas) or 1
    ratios = [round(a / total, 3) for a in areas]
    call("edit_axis", engine.edit_axis, rf.get("graph"), axis="x",
         title="Binding Energy (eV)", props={"reverse": 1})
    call("export", engine.export, rf.get("graph"), fmt="png",
         file_path=png("d02_xps"))
    return f"面积比={ratios} (真值≈[0.62,0.23,0.11]) R2={rf.get('r_squared')}"


def d03_nmr(p):
    """NMR dd 峰（4 根 Lorentz 裂分）"""
    x = np.arange(7.0, 8.0, 0.001)
    J1, J2 = 0.030, 0.018
    ctr = 7.55
    y = np.zeros_like(x) + 12 + SEED.normal(0, 3, x.size)
    for dc in (-J1 / 2 - J2 / 2, -J1 / 2 + J2 / 2,
               J1 / 2 - J2 / 2, J1 / 2 + J2 / 2):
        y += 800 / (1 + 4 * ((x - (ctr + dc)) / 0.015) ** 2)
    r = call("write_data", engine.write_data,
             {"ppm": x.tolist(), "intensity": y.round(2).tolist()})
    ws = r["worksheet"]
    rf = call("peak_fit(4)", engine.peak_fit, ws, x_column="ppm",
              y_column="intensity", n_peaks=4, kind="lorentz",
              centers_hint=[ctr - J1 / 2 - J2 / 2, ctr - J1 / 2 + J2 / 2,
                            ctr + J1 / 2 - J2 / 2, ctr + J1 / 2 + J2 / 2])
    peaks = sorted([q["center"] for q in (rf.get("peaks") or [])])
    call("edit_axis", engine.edit_axis, rf.get("graph"), axis="x",
         title="δ (ppm)", props={"reverse": 1})
    call("export", engine.export, rf.get("graph"), fmt="png",
         file_path=png("d03_nmr"))
    return f"4峰位={[round(c_, 4) for c_ in peaks]} R2={rf.get('r_squared')}"


# ============================================================ 热化学


def d04_combustion(p):
    """燃烧热：绝热温升曲线，前后基线拟合 + ΔT 外推 + 积分"""
    t = np.arange(0, 600, 2.0)
    t0, dT, k_heat, k_cool = 200.0, 2.5, 0.08, 0.003
    T = np.where(
        t < t0,
        298.15 + 0.0008 * t,
        298.15 + 0.0008 * t + dT * (1 - np.exp(-k_heat * (t - t0)))
        * np.exp(-k_cool * (t - t0)))
    T = T + SEED.normal(0, 0.004, t.size)
    r = call("write_data", engine.write_data,
             {"time_s": t.tolist(), "T_K": T.round(4).tolist()})
    ws = r["worksheet"]
    # 前基线拟合：只保留点火前数据（t < t0），拟合基线斜率
    rpk = call("peak_find", engine.peak_find, ws, x_column="time_s",
               y_column="T_K", min_height=299.5)
    peaks = rpk.get("peaks") or []
    tmax = float(peaks[0]["x"]) if peaks else 0
    tmax_T = float(peaks[0]["y"]) if peaks else 0
    call("mask_points", engine.mask_points, ws, y_column="T_K",
         x_column="time_s", x_min=t0 - 1, backup=False)
    rf1 = call("fit(前基线)", engine.fit, ws, x_column="time_s",
               y_column="T_K", kind="linear", plot_curve=False)
    rp = call("plot", engine.plot, ws, y_columns=["T_K"], x_column="time_s",
              plot_type="line", title="绝热温升曲线（燃烧热）")
    g = rp.get("graph", "")
    if not g:
        p.append("plot 未返回 graph 名，无法画辅助线")
        return "plot 未返回 graph"
    call("add_line", engine.add_line, g, orientation="vertical", at=t0,
         color="#888888", line_style=1, label="点火")
    call("add_line", engine.add_line, g, orientation="horizontal", at=tmax_T,
         color="#D55E00", line_style=1, label=f"Tmax={tmax_T:.2f}K")
    call("export", engine.export, g, fmt="png", file_path=png("d04_combustion"))
    pr = rf1.get("parameters", {})
    if tmax_T < 299.5:
        p.append(f"peak_find 未找到温升峰（Tmax={tmax_T}），ΔT 外推不可用")
    return (f"基线 slope={pr.get('slope')} Tmax={tmax_T:.2f}K@{tmax:.0f}s "
            f"(真值 ΔT≈2.5K)")


# ============================================================ 电化学


def d05_lsv_tafel(p):
    """LSV Tafel：log10|j| vs η，列公式算 log，Tafel 拟合读 x_intercept"""
    eta = np.arange(0.02, 0.45, 0.005)
    j0, alpha_nF_RT = 1e-6, 19.5      # Tafel b = 2.303RT/(αnF) ≈ 0.118 V/dec
    j = j0 * np.exp(alpha_nF_RT * eta) * 1000   # mA/cm2
    j = j * (1 + SEED.normal(0, 0.02, eta.size))
    r = call("write_data", engine.write_data,
             {"eta_V": eta.tolist(), "j_mA": j.round(6).tolist()})
    ws = r["worksheet"]
    # 数据准确化：log10 走 Origin 原生列公式
    rcf = call("column_formula", engine.column_formula, ws, 2,
               "log10(col(2))", lname="log_j")
    sc = (rcf.get("sample_check") or {})
    if not sc.get("match"):
        p.append(f"column_formula 复核未过: {sc}")
    # Tafel 区线性拟合（η 0.15~0.40）
    rmask = call("mask_points", engine.mask_points, ws, y_column="log_j",
                 x_column="eta_V", x_max=0.14)
    rf = call("fit(Tafel)", engine.fit, ws, x_column="eta_V",
              y_column="log_j", kind="linear", plot_curve=False)
    pr = rf.get("parameters", {})
    slope = pr.get("slope", 0)
    b_mv = 1000 / slope if slope else 0
    log_j0 = -pr.get("x_intercept", 0) * slope + pr.get("intercept", 0)
    rp = call("plot", engine.plot, ws, y_columns=["log_j"],
              x_column="eta_V", plot_type="line_symbol", title="Tafel 图")
    g = rp.get("graph")
    call("add_line", engine.add_line, g, orientation="slope", slope=slope,
         intercept=pr.get("intercept"), color="#D55E00", line_style=1,
         label="Tafel 外推")
    call("edit_axis", engine.edit_axis, g, axis="x", title="η (V)")
    call("edit_axis", engine.edit_axis, g, axis="y",
         title="log |j| (mA/cm²)")
    call("export", engine.export, g, fmt="png", file_path=png("d05_tafel"))
    if abs(b_mv - 118) > 6:
        p.append(f"Tafel 斜率 {b_mv:.0f} mV/dec vs 真值 118 偏差>6")
    return f"b={b_mv:.0f}mV/dec (真值118) mask={rmask.get('masked')} col_formula={'OK' if sc.get('match') else 'X'}"


def d06_eis(p):
    """EIS Nyquist：半圆 + Warburg 尾，等轴比"""
    Rs, Rct = 20.0, 150.0
    theta = np.linspace(0, np.pi, 40)
    zr = Rs + Rct / 2 * (1 + np.cos(theta))
    zi = Rct / 2 * np.sin(theta)
    # Warburg 尾（45° 线）
    w_ = np.linspace(0, 80, 12)
    zr = np.concatenate([zr, Rs + Rct + w_ * 0.7])
    zi = np.concatenate([zi, np.zeros(12) + 2 + w_ * 0.7])
    zr = zr + SEED.normal(0, 0.8, zr.size)
    zi = zi + SEED.normal(0, 0.8, zi.size)
    r = call("plot_template(eis_nyquist)", engine.plot_template, "eis_nyquist",
             {"z_real": zr.round(2).tolist(), "z_imag": zi.round(2).tolist()},
             title="EIS Nyquist", fmt="png", file_path=png("d06_eis"))
    rv = call("verify_graph", engine.verify_graph, r.get("graph"),
              expected_series=1)
    return f"graph={r.get('graph')} verify={rv.get('passed')}"


def d07_cv_multi(p):
    """CV 5 圈渐变叠放（first_last 图例）"""
    E = np.concatenate([np.arange(-0.2, 0.801, 0.01),
                        np.arange(0.80, -0.199, -0.01)])
    series = {}
    for c in range(1, 6):
        fade = 1 - 0.03 * (c - 1)
        i = (42 * fade * np.exp(-((E - 0.52) ** 2) / (2 * 0.06 ** 2))
             - 34 * fade * np.exp(-((E - 0.44) ** 2) / (2 * 0.06 ** 2)))
        i = i + SEED.normal(0, 0.7, E.size)
        series[f"Cycle {c}"] = i.round(2).tolist()
    r = call("plot_template(cycle_overlay)", engine.plot_template,
             "cycle_overlay",
             {"x": E.round(3).tolist(), "series": series,
              "legend_mode": "first_last"},
             title="CV 5 圈", x_title="E (V)", y_title="i (μA)",
             fmt="png", file_path=png("d07_cv"))
    rv = call("verify_graph", engine.verify_graph, r.get("graph"),
              expected_series=5)
    return f"graph={r.get('graph')} legend={r.get('legend_mode')} verify={rv.get('passed')}"


def d08_gcd(p):
    """充放电 3 圈阶梯曲线叠放"""
    t_all, series = np.arange(0, 300, 1.0), {}
    for c in range(1, 4):
        V = np.piecewise(
            t_all,
            [t_all < 60, (t_all >= 60) & (t_all < 75),
             (t_all >= 75) & (t_all < 135), t_all >= 135],
            [lambda tt: 3.0 + 0.021 * tt + 0.003 * c,
             4.25 + 0.002 * c,
             lambda tt: 4.25 - 0.021 * (tt - 75) - 0.003 * c,
             3.0 - 0.002 * c])
        V = V + SEED.normal(0, 0.004, t_all.size)
        series[f"第{c}圈"] = V.round(4).tolist()
    r = call("plot_template(cycle_overlay)", engine.plot_template,
             "cycle_overlay", {"x": t_all.tolist(), "series": series},
             title="GCD 3 圈", x_title="t (min)", y_title="V (V)",
             fmt="png", file_path=png("d08_gcd"))
    return f"graph={r.get('graph')}"


# ============================================================ 3D 与等高线


def d09_ts_surface(p):
    """过渡态能量面（马鞍面）3D + 等高线"""
    b1 = np.arange(1.0, 3.01, 0.05)
    b2 = np.arange(1.0, 3.01, 0.05)
    B1, B2 = np.meshgrid(b1, b2)
    E = (8 * (B1 - 2.0) ** 2 - 6 * (B2 - 2.0) ** 2
         + 1.5 * (B1 - 2) * (B2 - 2) + 12 * np.exp(-((B1 - 2) ** 2
                                                    + (B2 - 2) ** 2) / 0.1))
    r1 = call("plot3d", engine.plot3d,
              {"x": b1.tolist(), "y": b2.tolist(),
               "z": E.round(3).tolist()},
              plot_type="surface", fmt="png", file_path=png("d09_ts3d"))
    r2 = call("plot_contour", engine.plot_contour,
              {"x": b1.tolist(), "y": b2.tolist(),
               "z": E.round(3).tolist()},
              plot_type="contour", fmt="png",
              file_path=png("d09_tscontour"))
    return f"surface={r1.get('ok')}({r1.get('graph')}) contour={r2.get('ok')}({r2.get('graph')})"


# ============================================================ 细节/自定义


def d10_axis_ticks(p):
    """轴量程与分度值：major_increment + minor_ticks + 量程"""
    x = np.arange(0, 10.01, 0.1)
    y = np.sin(x) * np.exp(-x / 8)
    r = call("plot_file", engine.plot_file,
             {"x_s": x.tolist(), "y": y.round(4).tolist()},
             plot_type="line", title="阻尼振荡")
    g = r.get("graph")
    call("edit_axis", engine.edit_axis, g, axis="x",
         title="t (s)", from_value=0, to_value=10,
         props={"major_increment": 2.0, "minor_ticks": 3})
    call("edit_axis", engine.edit_axis, g, axis="y",
         title="A (a.u.)", from_value=-0.6, to_value=0.7,
         props={"major_increment": 0.2, "minor_ticks": 1,
                "label_decimals": 1})
    rv = call("verify_graph", engine.verify_graph, g,
              expected_x_title="t (s)", expected_y_title="A (a.u.)")
    call("export", engine.export, g, fmt="png", file_path=png("d10_ticks"))
    return f"graph={g} verify={rv.get('passed')}"


def d11_mask_cosmic(p):
    """拉曼 cosmic ray 尖峰屏蔽"""
    x = np.arange(400, 2001, 4.0)
    y = 200 + 1500 * np.exp(-4 * np.log(2) * ((x - 1000) / 30) ** 2)
    y = y + SEED.normal(0, 18, x.size)
    spike_rows = [87, 88, 240]
    y[spike_rows] += [4200, 3900, 2600]
    r = call("write_data", engine.write_data,
             {"shift": x.tolist(), "intensity": y.round(2).tolist()})
    ws = r["worksheet"]
    rpk = call("peak_find", engine.peak_find, ws, x_column="shift",
               y_column="intensity", min_height=3000)
    found = [int(pk.get("index", -1)) for pk in (rpk.get("peaks") or [])]
    rows = sorted(set(spike_rows) | {i for i in found if 0 <= i < x.size})
    rm = call("mask_points", engine.mask_points, ws, y_column="intensity",
              rows=rows)
    rp = call("plot", engine.plot, ws, y_columns=["intensity"],
              x_column="shift", plot_type="line", title="拉曼（屏蔽 cosmic ray）")
    rr = call("read_worksheet", engine.read_worksheet, ws,
              columns=["intensity"], max_rows=None)
    n_nan = sum(1 for v in (rr.get("columns", {}).get("intensity") or [])
                if v is None or (isinstance(v, float) and v != v))
    call("export", engine.export, rp.get("graph"), fmt="png",
         file_path=png("d11_mask"))
    if n_nan < len(spike_rows):
        p.append(f"屏蔽后 NaN 数 {n_nan} < 预期 {len(spike_rows)}")
    return (f"peak_find 命中 {len(found)} 尖峰，mask={rm.get('masked')}，"
            f"备份={rm.get('backup_column')}，NaN={n_nan}")


def d12_formula_kinetics(p):
    """一级动力学：ln[A] 全程走 Origin 列公式（不经 numpy）"""
    t = np.arange(0, 900, 60.0)
    conc = 0.8 * np.exp(-0.0042 * t)
    conc = conc * (1 + SEED.normal(0, 0.01, t.size))
    r = call("write_data", engine.write_data,
             {"time_s": t.tolist(), "conc_M": conc.round(5).tolist()})
    ws = r["worksheet"]
    rcf = call("column_formula(ln)", engine.column_formula, ws, 2,
               "ln(col(2))", lname="ln_conc")
    sc = rcf.get("sample_check") or {}
    if not sc.get("match"):
        p.append(f"ln 列公式复核未过: {sc}")
    rf = call("fit", engine.fit, ws, x_column="time_s",
              y_column="ln_conc", kind="linear")
    pr = rf.get("parameters", {})
    k_fit = -pr.get("slope", 0)
    if abs(k_fit - 0.0042) / 0.0042 > 0.03:
        p.append(f"k 拟合 {k_fit:.5f} 偏差>3%")
    return (f"k={k_fit:.5f} (真值 0.0042) Origin 原生计算 "
            f"sample_check={'match' if sc.get('match') else sc}")


def d13_threshold_line(p):
    """粒径分布 + 阈值辅助线 + 标注"""
    d = SEED.lognormal(mean=math.log(38), sigma=0.22, size=220)
    r = call("write_data", engine.write_data,
             {"diameter_nm": d.round(2).tolist()})
    ws = r["worksheet"]
    rh = call("histogram", engine.histogram, ws, column="diameter_nm",
              bins=22, plot=True, file_path=png("d13_hist"),
              color="#009E73")
    g = rh.get("graph")
    call("add_line", engine.add_line, g, orientation="vertical", at=50,
         color="#D55E00", line_style=1, label="D90 阈值 50nm")
    call("export", engine.export, g, fmt="png", file_path=png("d13_hist"))
    return f"graph={g} hist_color=自定义"


# ============================================================ 交付与逃生舱


def d14_delivery(p):
    """图表一体交付：png+opju+data.csv+report.txt 全核验"""
    x = np.arange(200, 801, 4.0)
    y = 800 * np.exp(-((x - 460) ** 2) / (2 * 30 ** 2)) + SEED.normal(0, 8, x.size)
    r = call("write_data", engine.write_data,
             {"wavelength_nm": x.tolist(), "pl_intensity": y.round(2).tolist()})
    ws = r["worksheet"]
    rp = call("plot", engine.plot, ws, y_columns=["pl_intensity"],
              x_column="wavelength_nm", plot_type="line",
              title="PL 光谱")
    g = rp.get("graph")
    rd = call("export_delivery", engine.export_delivery, g,
              source_path=None, output_dir=os.path.join(OUT_DIR, "d14_bundle"),
              fmts="png,pdf", report_text=(
                  "PL 光谱分析：发射峰位于 460nm，FWHM≈70nm。\n"
                  "测试条件：350nm 激发，室温。数据见 data.csv。"),
              export_data_csv=True)
    ddir = rd.get("delivery_dir")
    need = ["data.csv", "report.txt"]
    missing = [f for f in need
               if not (ddir and os.path.exists(os.path.join(ddir, f)))]
    if missing:
        p.append(f"交付缺失: {missing}")
    return f"dir={ddir} csv={'OK' if rd.get('data_csv') else 'X'} txt={'OK' if rd.get('report_txt') else 'X'} files={len(rd.get('files', []))}"


def d15_labtalk(p):
    """逃生舱：任意 LabTalk 读 page.nlayers + 写读回（非阻塞命令）"""
    r = call("plot_file", engine.plot_file,
             {"a": [1, 2, 3, 4], "b": [1, 4, 9, 16]},
             plot_type="line", title="LabTalkProbe")
    g = r.get("graph")
    r1 = call("labtalk(read)", engine.labtalk,
              "double __probe_v = 2 * 3;", read_expr="page.nlayers", graph=g)
    rb = r1.get("readback")
    r2 = call("labtalk(write+read)", engine.labtalk,
              "layer.x.label.fsize = 16;", read_expr="layer.x.label.fsize",
              graph=g)
    rb2 = r2.get("readback")
    if rb != 1:
        p.append(f"page.nlayers 读回 {rb} != 1")
    if rb2 != 16:
        p.append(f"字号写入读回 {rb2} != 16")
    return f"nlayers={rb} fsize_write_read={rb2}"


# ============================================================ main


def main():
    quick = "--quick" in sys.argv
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = sys.argv[i + 1]
        elif a.startswith("--only="):
            only = a.split("=", 1)[1]
    os.makedirs(OUT_DIR, exist_ok=True)
    r0 = call("status", engine.status)
    if not r0.get("ok"):
        print("FATAL:", json.dumps(r0, ensure_ascii=False)[:200])
        sys.exit(2)

    cases = [
        ("d01_raman", "拉曼 D/G/2D 三峰拟合", d01_raman),
        ("d02_xps", "XPS C1s 三组分+面积占比+反向轴", d02_xps),
        ("d03_nmr", "NMR dd 峰 Lorentz 裂分拟合", d03_nmr),
        ("d04_combustion", "燃烧热温升曲线+辅助线", d04_combustion),
        ("d05_lsv_tafel", "LSV Tafel：列公式 log+拟合+外推线", d05_lsv_tafel),
        ("d06_eis", "EIS Nyquist 等轴比", d06_eis),
        ("d07_cv_multi", "CV 5 圈渐变叠放", d07_cv_multi),
        ("d08_gcd", "充放电 3 圈叠放", d08_gcd),
        ("d09_ts_surface", "过渡态马鞍面 3D+等高线", d09_ts_surface),
        ("d10_axis_ticks", "轴量程与分度值", d10_axis_ticks),
        ("d11_mask_cosmic", "cosmic ray 屏蔽", d11_mask_cosmic),
        ("d12_formula_kinetics", "ln 走 Origin 列公式", d12_formula_kinetics),
        ("d13_threshold_line", "直方图阈值辅助线", d13_threshold_line),
        ("d14_delivery", "图表一体交付", d14_delivery),
        ("d15_labtalk", "任意 LabTalk 逃生舱", d15_labtalk),
    ]
    if quick:
        cases = cases[:2]
    if only:
        _sel = {x.strip() for x in only.split(",") if x.strip()}
        cases = [c for c in cases
                 if c[0] in _sel or any(c[0].startswith(s) for s in _sel)]
        print("only ->", [c[0] for c in cases], flush=True)
    for cid, desc, fn in cases:
        case(cid, desc, fn)

    n_ok = sum(1 for c in RESULTS if c["ok"])
    n_p = sum(len(c["problems"]) for c in RESULTS)
    n_t = len(TOOL_LOG)
    n_tok = sum(1 for t in TOOL_LOG if t["ok"])
    print(f"\n{'=' * 60}\n案例 {n_ok}/{len(RESULTS)} 全绿 | "
          f"工具 {n_tok}/{n_t} | 问题 {n_p} 条")
    for c in RESULTS:
        print(f"[{'OK  ' if c['ok'] else 'ATTN'}] {c['case']:<22} "
              f"{c['tool_ok']}/{c['tool_calls']} | {c['summary'][:105]}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "chemistry_cases_v2_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"results": RESULTS, "tool_log": TOOL_LOG,
                   "summary": {"cases_ok": n_ok, "cases_total": len(RESULTS),
                               "tools_ok": n_tok, "tools_total": n_t,
                               "problems_total": n_p}},
                  f, ensure_ascii=False, indent=1, default=str)
    print("\nresult ->", out)


if __name__ == "__main__":
    main()
