# -*- coding: utf-8 -*-
"""科学功能测试：统计/变换/积分/FFT/相关/峰值/直方图/等高线/误差棒/箱线图"""
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import origin_engine as engine

OUT = engine.DEFAULT_OUTPUT_DIR
os.makedirs(OUT, exist_ok=True)
random.seed(11)
fails = []


def check(title, r, expect=True):
    ok = r.get("ok") is True
    good = ok if expect else not ok
    print(f"[{'OK' if good else 'FAIL'}] {title}: {json.dumps(r, ensure_ascii=False)[:200]}")
    if not good:
        fails.append(title)
    return r


# 基础数据：正弦 + 噪声 + 高斯峰
n = 256
xs = [i * 0.05 for i in range(n)]
ys = [3.0 * math.sin(2 * math.pi * 2.0 * x) + 1.0 + random.uniform(-0.4, 0.4) for x in xs]
peak = [8.0 * math.exp(-((x - 6.0) ** 2) / 0.5) for x in xs]
ysp = [a + b for a, b in zip(ys, peak)]
r = check("write_data(正弦+峰)", engine.write_data({"x": xs, "y": ys, "y_peak": ysp}))

# 1) 描述统计
check("origin_stats", engine.stats(r["worksheet"], ["y"]))
# 2) 平滑 / 归一化 / 微分
r2 = check("transform smooth", engine.transform(r["worksheet"], "y", op="smooth", window=7))
check("transform normalize zscore", engine.transform(r["worksheet"], "y", op="normalize", method="zscore"))
check("transform derivative", engine.transform(r["worksheet"], "y_peak", op="derivative"))
check("transform interpolate", engine.transform(
    r["worksheet"], "y", op="interpolate", new_x=[0.5, 1.0, 1.5, 2.0, 2.5]))
# 3) 积分 AUC（正弦半周期理论面积 3/pi ≈ 0.955 每半周期）
r3 = check("origin_integrate", engine.integrate(r["worksheet"], "x", "y"))
if r3.get("ok"):
    print(f"    AUC={r3['auc']:.4f}（正弦全周期 256 点 0~12.75，理论≈0）")
# 4) FFT（主频应≈2.0 Hz）
r4 = check("origin_fft", engine.fft(r["worksheet"], "x", "y", top=3))
if r4.get("ok"):
    print(f"    主频: {[round(p['frequency'], 4) for p in r4['top_frequencies']]}（期望≈2.0）")
check("origin_fft + 频谱图", engine.fft(r["worksheet"], "x", "y", plot_spectrum=True,
      file_path=os.path.join(OUT, "sci_fft.png")))
# 5) 相关矩阵
check("origin_correlate", engine.correlate(r["worksheet"]))
# 6) 峰值检测（峰在 x≈6）
r6 = check("origin_peak_find", engine.peak_find(r["worksheet"], "x", "y_peak", min_height=5.0, min_distance=5))
if r6.get("ok"):
    print(f"    峰位置: {[round(p['x'], 3) for p in r6['peaks']]}（期望≈6.0）")
# 7) 直方图（数据 + 图）
check("origin_histogram", engine.histogram(r["worksheet"], "y", bins=12))
check("origin_histogram + 图", engine.histogram(r["worksheet"], "y", bins=12, plot=True,
      file_path=os.path.join(OUT, "sci_hist.png")))
# 8) 等高线 + 填充等高线 + 3D 线框
z = [[math.sin(a) * math.cos(b) for a in (i * 0.3 for i in range(-10, 11))]
     for b in (j * 0.3 for j in range(-10, 11))]
check("origin_plot_contour", engine.plot_contour(
    {"z": z}, file_path=os.path.join(OUT, "sci_contour.png")))
check("origin_plot_contour filled", engine.plot_contour(
    {"z": z}, plot_type="contour_fill", file_path=os.path.join(OUT, "sci_contour_fill.png")))
check("origin_plot_contour 3d_wire", engine.plot_contour(
    {"z": z}, plot_type="3d_wire", file_path=os.path.join(OUT, "sci_3d_wire.png")))
# 9) 画图扩展：histogram / box / 误差棒
check("plot histogram", engine.plot(r["worksheet"], y_columns=["y"], plot_type="histogram"))
check("plot box", engine.plot(r["worksheet"], y_columns=["y"], plot_type="box"))
r9 = check("write_data(误差棒)", engine.write_data({
    "x": [1, 2, 3, 4, 5], "y": [1.1, 2.3, 3.2, 4.1, 5.2],
    "err": [0.2, 0.3, 0.15, 0.25, 0.2]}))
r9b = check("plot 误差棒", engine.plot(r9["worksheet"], y_columns=["y"], x_column="x",
      yerr_column="err"))
if r9b.get("ok"):
    check("export 误差棒图", engine.export(r9b["graph"],
          file_path=os.path.join(OUT, "sci_errorbar.png")))

# 10) 错误路径
check("stats 坏列(期望FAIL)", engine.stats(r["worksheet"], ["NoSuchCol"]), expect=False)
check("transform 坏op(期望FAIL)", engine.transform(r["worksheet"], "y", op="fft"), expect=False)

print(f"\nSCIENCE-TEST {'OK' if not fails else 'FAIL: ' + str(fails)}")
sys.exit(0 if not fails else 1)
