# -*- coding: utf-8 -*-
"""
能力实测：删点 / 线性拟合 / 非线性拟合 / 3D 表面 / 3D 散点
（验证 originpro 是否支持，供扩展插件用）
"""
import math
import os
import random
import sys

OUT = os.path.join(os.path.expanduser("~"), "dsch_origin_plugin", "output")
os.makedirs(OUT, exist_ok=True)

import originpro as op
from originpro import config as opconfig

opconfig.po.Attach()  # 单实例语义
random.seed(42)


def sec(title):
    print(f"\n===== {title} =====")


# ---------- 1) 删除数据点 ----------
sec("1) 删除数据点（LabTalk delrow + Python 过滤）")
wks = op.new_sheet("w", "DelTest")
x = list(range(1, 21))
y = [v * v for v in x]
wks.from_list(0, x, lname="x")
wks.from_list(1, y, lname="y")
print(f"  删除前 rows: {wks.rows}")
opconfig.po.LT_execute("delrow 1:=2 2:=3;")  # 删除第 2~3 行
print(f"  delrow 后 rows: {wks.rows}, x 前 5: {wks.to_list(0)[:5]}")
# Python 侧过滤（删除 x>=15 的点）
wks2 = op.new_sheet("w", "DelTest2")
x2 = [v for v in x if v < 15]
y2 = [v * v for v in x2]
wks2.from_list(0, x2, lname="x")
wks2.from_list(1, y2, lname="y")
print(f"  过滤后 rows: {wks2.rows} (x<15), max(x)={max(wks2.to_list(0))}")

# ---------- 2) 线性拟合 + 拟合曲线上图 ----------
sec("2) 线性拟合（LinearFit + 拟合曲线加图）")
wf = op.new_sheet("w", "FitLin")
xf = [i * 0.5 for i in range(1, 21)]
yf = [2.0 * v + 1.0 + random.uniform(-0.6, 0.6) for v in xf]
wf.from_list(0, xf, lname="x")
wf.from_list(1, yf, lname="y")

lr = op.LinearFit()
lr.set_data(wf, 0, 1)
rr = lr.result()
slope = rr["Parameters"]["Slope"]["Value"]
intercept = rr["Parameters"]["Intercept"]["Value"]
r2 = rr.get("Statistics", {}).get("ReducedChiSq", "?")
print(f"  Slope={slope:.4f} (真值2.0), Intercept={intercept:.4f} (真值1.0)")
rep, curves = lr.report(0)
print(f"  report sheet: {rep}, fit curve sheet: {curves}")
if curves:
    wc = op.find_sheet("w", curves)
    gp = op.new_graph("FitLinPlot")
    gl = gp[0]
    gl.add_plot(wf, 1, 0, type="s")           # 原始点（散点）
    gl.add_plot(wc, 1, 0, type="l")           # 拟合线
    gl.rescale()
    fn = gp.save_fig(os.path.join(OUT, "cap_fit_linear.png"), width=600, replace=True)
    print(f"  图已导出: {fn}")

# ---------- 3) 非线性拟合 ----------
sec("3) 非线性拟合（NLFit ExpDec1）")
we = op.new_sheet("w", "FitExp")
xe = [i * 0.5 for i in range(0, 21)]
ye = [5.0 * math.exp(-0.5 * v) + random.uniform(-0.15, 0.15) for v in xe]
we.from_list(0, xe, lname="x")
we.from_list(1, ye, lname="y")
model = op.NLFit("ExpDec1")
model.set_data(we, 0, 1)
model.fit()
rep2, curves2 = model.report()   # 必须先 report，再 result（originpro 约束）
res = model.result()
import json as _json
print(f"  result 原始结构: {_json.dumps(res, ensure_ascii=False)[:400]}")
# 从报告表读参数（Parameter/Value 列）
if rep2:
    wr = op.find_sheet("w", rep2)
    try:
        ncol = wr.obj.Cols
        pcol = wr.to_list(0)
        vcol = wr.to_list(1)
        pairs = [(str(a).strip(), b) for a, b in zip(pcol, vcol) if str(a).strip()]
        print(f"  报告表参数: {pairs[:6]}")
    except Exception as e:
        print(f"  读报告表失败: {e}")
print(f"  report: {rep2}, curves: {curves2}")

# ---------- 4) 3D 表面图（matrix + add_mplot） ----------
sec("4) 3D 表面图（matrix + add_mplot, GLparafunc 模板）")
import numpy as np
n = 25
gx, gy = np.meshgrid(np.linspace(-3, 3, n), np.linspace(-3, 3, n))
gz = np.sin(gx) * np.cos(gy)
data3 = np.array([gz, gx, gy])   # Z, X, Y 三个矩阵对象
ms = op.new_sheet("m", "SurfData")
ms.from_np(data3)
gp3 = op.new_graph("SurfPlot", template="GLparafunc")
gl3 = gp3[0]
p3 = gl3.add_mplot(ms, 0, 1, 2)
gl3.rescale()
fn3 = gp3.save_fig(os.path.join(OUT, "cap_3d_surface.png"), width=600, replace=True)
print(f"  add_mplot 完成（对象引用已失效，不打印 str），图已导出: {fn3}")

# ---------- 5) 3D 散点（worksheet XYZ） ----------
sec("5) 3D 散点（worksheet XYZ → plotxy plot:=310）")
w3 = op.new_sheet("w", "Scat3D")
pts = 40
xs = [random.uniform(-2, 2) for _ in range(pts)]
ys = [random.uniform(-2, 2) for _ in range(pts)]
zs = [a * a + b * b + random.uniform(-0.2, 0.2) for a, b in zip(xs, ys)]
w3.from_list(0, xs, lname="X")
w3.from_list(1, ys, lname="Y")
w3.from_list(2, zs, lname="Z")
opconfig.po.LT_execute("plotxy iy:=(1,2,3) plot:=310;")  # 310 = 3D scatter?
try:
    gp4 = op.find_graph()
    fn4 = gp4.save_fig(os.path.join(OUT, "cap_3d_scatter.png"), width=600, replace=True)
    print(f"  3D 散点图已导出: {fn4}")
except Exception as e:
    print(f"  3D 散点失败: {e}")

print("\nCAPABILITY-TEST DONE")
