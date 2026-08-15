# -*- coding: utf-8 -*-
"""探测：histogram / box / contour 等图型的 plot 类型代码与可用性"""
import os
import random
import sys

OUT = os.path.join(os.path.expanduser("~"), "dsch_origin_plugin", "output")
import originpro as op
from originpro import config as opconfig

opconfig.po.Attach()
random.seed(1)

# 数据：正态-ish 样本
wks = op.new_sheet("w", "Probe")
data = [random.gauss(5, 1.5) for _ in range(200)]
wks.from_list(0, data, lname="v")

results = {}
for code, name in [(207, "histogram"), (215, "box"), (216, "box2"), (208, "bar")]:
    try:
        opconfig.po.LT_execute(f"plotxy iy:=(1) plot:={code};")
        gp = op.find_graph()
        gname = gp.obj.GetName()
        fn = gp.save_fig(os.path.join(OUT, f"probe_{name}.png"), width=500, replace=True)
        results[name] = f"OK({code}) -> {fn}"
        opconfig.po.LT_execute(f"win -c {gname};")
    except Exception as e:
        results[name] = f"FAIL({code}): {e}"

# contour：matrix + add_mplot type=?
import numpy as np
gx, gy = np.meshgrid(np.linspace(-2, 2, 20), np.linspace(-2, 2, 20))
z = np.exp(-(gx**2 + gy**2))
ms = op.new_sheet("m", "ProbeM")
ms.from_np(np.array([z, gx, gy]))
for tcode, tname in [(104, "contour"), (105, "contour_fill"), (106, "3d_wire")]:
    try:
        gp = op.new_graph(f"Probe{tname}", template="GLparafunc")
        gl = gp[0]
        gl.add_mplot(ms, 0, 1, 2, type=tcode)
        gl.rescale()
        fn = gp.save_fig(os.path.join(OUT, f"probe_{tname}.png"), width=500, replace=True)
        results[tname] = f"OK({tcode}) -> {fn}"
        opconfig.po.LT_execute(f"win -c {gp.obj.GetName()};")
    except Exception as e:
        results[tname] = f"FAIL({tcode}): {e}"

for k, v in results.items():
    print(f"{k}: {v}")
print("PROBE DONE")
