# -*- coding: utf-8 -*-
"""视觉检验脚本：生成代表性输出图片供识图模型核对。
运行: <venv python> -X utf8 smoke\\visual_check.py
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import origin_engine as engine

OUT = engine.DEFAULT_OUTPUT_DIR
os.makedirs(OUT, exist_ok=True)


def main():
    # 1) 3 序列 line_symbol：journal 样式 + ocean 调色板 + 语义轴标题/符号循环
    n = 60
    xs = [i * 0.1 for i in range(n)]
    r = engine.write_data({
        "temperature_C": [math.sin(x) * 10 + 25 + x * 0.2 for x in xs],
        "pressure_kPa": [math.cos(x) * 5 + 100 for x in xs],
        "signal_uA": [(math.sin(2 * x) ** 2) * 3 + 0.5 for x in xs],
    })
    w = r["worksheet"]
    r2 = engine.plot(w, plot_type="line_symbol", style_mode="journal", family="ocean",
                     graph_name="styled3", title="styled 3-series")
    if r2.get("ok"):
        f1 = os.path.join(OUT, "vis_styled3.png")
        engine.export(r2["graph"], file_path=f1, width=1200)
        print("styled3:", f1)

    # 2) 柱状图（修复路径 plotxy 204）
    r = engine.write_data({"category": ["A", "B", "C", "D"], "value": [3.2, 5.1, 2.4, 7.8]})
    r2 = engine.plot(r["worksheet"], plot_type="bar", graph_name="barfix",
                     title="bar fixed")
    if r2.get("ok"):
        f2 = os.path.join(OUT, "vis_bar.png")
        engine.export(r2["graph"], file_path=f2, width=1000)
        print("bar:", f2)
    else:
        print("bar FAIL:", r2)

    # 3) 密集散点（600 点 -> 触发 marker_downscale 规则）
    random.seed(7)
    xs2 = [random.gauss(0, 1) for _ in range(600)]
    ys2 = [random.gauss(0, 1) for _ in range(600)]
    r = engine.write_data({"xa": xs2, "ya": ys2})
    r2 = engine.plot(r["worksheet"], plot_type="scatter", graph_name="dense",
                     title="dense scatter")
    if r2.get("ok"):
        f3 = os.path.join(OUT, "vis_dense.png")
        engine.export(r2["graph"], file_path=f3, width=1000)
        print("dense:", f3)

    # 4) 双样本 t 检验直接挂到图数据上（统计输出已由 selftest 覆盖，这里顺带出 help）
    print("VISUAL CHECK DONE")


if __name__ == "__main__":
    main()
