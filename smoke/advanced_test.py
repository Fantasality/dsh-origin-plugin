# -*- coding: utf-8 -*-
"""
进阶能力测试：删点 / 线性拟合 / 非线性拟合 / 3D 表面 / 3D 散点
（通过引擎公开 API，含并发安全层）
"""
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import origin_engine as engine

OUT = engine.DEFAULT_OUTPUT_DIR
os.makedirs(OUT, exist_ok=True)
random.seed(7)

fails = []


def check(title, r, expect=True):
    ok = r.get("ok") is True
    if expect:
        good = ok
    else:
        good = not ok  # 错误路径：期望 ok=False
    print(f"[{'OK' if good else 'FAIL'}] {title}: {json.dumps(r, ensure_ascii=False)[:220]}")
    if not good:
        fails.append(title)
    return r


# 1) 删点
r = check("write_data(20点)", engine.write_data(
    {"x": list(range(1, 21)), "y": [v * v for v in range(1, 21)]}))
wks = r["worksheet"]
r = check("filter_data 删索引2,5,9", engine.filter_data(wks, drop_rows=[2, 5, 9]))
check("filter_data 裁剪 x<15", engine.filter_data(wks, x_max=15.0))
r = check("plot(删点后)", engine.plot(wks, plot_type="line"))
check("export 删点图", engine.export(r["graph"],
      file_path=os.path.join(OUT, "adv_filter.png")))

# 2) 线性拟合 + 拟合曲线上图
r = check("write_data(带噪线性)", engine.write_data({
    "x": [i * 0.5 for i in range(1, 21)],
    "y": [2.0 * i * 0.5 + 1.0 + random.uniform(-0.6, 0.6) for i in range(1, 21)],
}))
r2 = check("fit(linear)", engine.fit(r["worksheet"], "x", "y", kind="linear"))
if r2.get("ok"):
    p = r2["parameters"]
    print(f"    slope={p.get('slope'):.3f} intercept={p.get('intercept'):.3f} "
          f"r2={p.get('r_square')}")
    check("export 线性拟合图", engine.export(
        r2["graph"], file_path=os.path.join(OUT, "adv_fit_linear.png")))

# 3) 非线性拟合 ExpDec1
r = check("write_data(指数衰减)", engine.write_data({
    "x": [i * 0.5 for i in range(0, 21)],
    "y": [5.0 * math.exp(-0.5 * i * 0.5) + random.uniform(-0.15, 0.15) for i in range(0, 21)],
}))
r2 = check("fit(ExpDec1)", engine.fit(r["worksheet"], "x", "y", kind="ExpDec1"))
if r2.get("ok"):
    print(f"    参数: {r2['parameters']}")
    check("export 非线性拟合图", engine.export(
        r2["graph"], file_path=os.path.join(OUT, "adv_fit_exp.png")))

# 4) 3D 表面
z = [[math.sin(a) * math.cos(b) for a in (i * 0.3 for i in range(-10, 11))]
     for b in (j * 0.3 for j in range(-10, 11))]
r = check("plot3d(surface)", engine.plot3d(
    {"z": z}, plot_type="surface",
    file_path=os.path.join(OUT, "adv_3d_surface.png"), title="sin*cos"))

# 5) 3D 散点
r = check("plot3d(scatter)", engine.plot3d(
    {"x": [random.uniform(-2, 2) for _ in range(40)],
     "y": [random.uniform(-2, 2) for _ in range(40)],
     "z": [random.uniform(0, 4) for _ in range(40)]},
    plot_type="scatter",
    file_path=os.path.join(OUT, "adv_3d_scatter.png"), title="3d scatter"))

# 6) 错误路径（期望 ok=False 的结构化错误，不崩溃）
check("fit 坏函数名(期望FAIL)", engine.fit("[NoSuch]Sheet1", 0, 1, kind="Gauss"), expect=False)
check("plot3d 坏类型(期望FAIL)", engine.plot3d({"z": [[1, 2]]}, plot_type="contour"), expect=False)

print(f"\nADVANCED-TEST {'OK' if not fails else 'FAIL: ' + str(fails)}")
sys.exit(0 if not fails else 1)
