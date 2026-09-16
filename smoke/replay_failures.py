# -*- coding: utf-8 -*-
"""重放三个失败调用，拿真实错误文本 + 验证 plot3d 正确格式。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
import origin_engine as engine  # noqa: E402

OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "_chem_out"))

def show(label, r):
    print(f"--- {label} ---")
    print(json.dumps(r, ensure_ascii=False, default=str)[:400])
    print()

engine.status()

# 1) transform ln 真实报错
r = engine.write_data({"t": [1, 2, 3, 4, 5], "y": [2.7, 1.5, 0.8, 0.4, 0.2]})
ws = r["worksheet"]
show("transform op=ln", engine.transform(ws, column="y", op="ln"))
show("transform op=log10", engine.transform(ws, column="y", op="log10"))

# 2) c13 dual_y 失败重放（right 空/短列表的校验报错）
show("dual_y 空 right", engine.plot_template(
    "dual_y", {"x": [1, 2, 3], "left": [1.0, 2.0, 3.0], "right": [],
               "left_name": "L", "right_name": "R"}))

# derivative 返回结构（c13 里我以为有 values 字段）
T = list(range(30, 800, 5))
w = [100 - 38 / (1 + pow(2.718, -(t - 240) / 18)) for t in T]
r2 = engine.write_data({"T": T, "mass": [round(x, 3) for x in w]})
ws2 = r2["worksheet"]
rd = engine.transform(ws2, column="mass", op="derivative")
print("derivative keys:", list(rd.keys()))
print("derivative snippet:", json.dumps(rd, ensure_ascii=False, default=str)[:300])
print()

# 3) plot3d 正确矩阵格式
Tv = list(range(60, 121, 8))
hh = [round(x, 1) for x in np.arange(1, 13, 0.8)]
Z = [[round(95 * pow(2.718, -((t - 95) ** 2 / 900) - ((h - 6.5) ** 2 / 9)), 2)
      for h in hh] for t in Tv]
show("plot3d surface 矩阵", engine.plot3d(
    {"x": Tv, "y": hh, "z": Z}, plot_type="surface",
    fmt="png", file_path=os.path.join(OUT, "c26_fixed.png")))

# 3b) 散点式 surface（AI 会犯的错——报错信息友好吗？）
r3 = engine.plot3d({"x": [1, 2, 3], "y": [1, 2], "z": [1.0, 2.0, 3.0]},
                    plot_type="surface")
show("plot3d 散点式 surface 报错", r3)
