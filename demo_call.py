# -*- coding: utf-8 -*-
"""
DSH Origin 画图插件 —— 最小可运行调用示例
=========================================

不依赖 MCP，直接调用引擎（DSH 的 MCP 工具底层就是这些函数）。
也演示了分步调用（写数 -> 画图 -> 导出）与一键调用（plot_file）。

运行:  <venv python> -X utf8 demo_call.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import origin_engine as engine


def show(title, r):
    print(f"== {title} ==")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print()
    return r


def main():
    # 0) 检查连接（Origin 未运行时给出明确提示）
    st = show("origin_status", engine.status())

    # 1) 一键：写数据 -> 画折线图 -> 导出 PNG（最常用）
    r = show("plot_file(line, png)", engine.plot_file(
        {"x": list(range(1, 11)), "y": [v * v for v in range(1, 11)]},
        plot_type="line", fmt="png",
        file_path=os.path.join(engine.DEFAULT_OUTPUT_DIR, "demo_line.png"),
        title="demo y=x^2",
    ))
    if r.get("ok"):
        print(f"  文件已生成: {r['file']} ({r['size']} bytes)\n")

    # 2) 分步：写多列数据 -> 散点图 -> 导出 SVG
    r1 = show("write_data(3列)", engine.write_data({
        "t": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "sin": [0.0, 0.48, 0.84, 1.0, 0.91, 0.6, 0.14],
        "cos": [1.0, 0.88, 0.54, 0.07, -0.42, -0.8, -0.99],
    }))
    if not r1.get("ok"):
        sys.exit(1)
    r2 = show("plot(scatter)", engine.plot(
        r1["worksheet"], x_column="t", y_columns=["sin", "cos"],
        plot_type="scatter", title="demo scatter",
    ))
    if not r2.get("ok"):
        sys.exit(1)
    r3 = show("export(svg)", engine.export(
        r2["graph"], file_path=os.path.join(engine.DEFAULT_OUTPUT_DIR, "demo_scatter.svg"),
    ))
    if r3.get("ok"):
        print(f"  文件已生成: {r3['file']} ({r3['size']} bytes)\n")

    # 3) 错误路径演示：坏参数不崩溃，返回清晰错误
    show("错误示例(坏列)", engine.plot("[NoSuchBook]Sheet1"))
    show("错误示例(坏类型)", engine.plot_file({"x": [1, 2]}, plot_type="pie"))

    # 4) 科学分析：统计 / 平滑 / 积分 / FFT / 等高线
    import math
    import random
    random.seed(3)
    n = 256
    xs = [i * 0.05 for i in range(n)]
    ys = [2.0 * math.sin(2 * math.pi * 2.0 * x) + 0.3 * x
          + random.uniform(-0.3, 0.3) for x in xs]
    r4 = show("write_data(含噪信号)", engine.write_data({"t": xs, "sig": ys}))
    if r4.get("ok"):
        w4 = r4["worksheet"]
        show("stats", engine.stats(w4, ["sig"]))
        show("transform smooth", engine.transform(w4, "sig", op="smooth", window=9))
        show("integrate AUC", engine.integrate(w4, "t", "sig"))
        show("fft 主频", engine.fft(w4, "t", "sig", top=3))
        z = [[math.sin(a) * math.cos(b) for a in (i * 0.3 for i in range(-10, 11))]
             for b in (j * 0.3 for j in range(-10, 11))]
        show("plot_contour", engine.plot_contour(
            {"z": z}, file_path=os.path.join(engine.DEFAULT_OUTPUT_DIR, "demo_contour.png")))

    print("DEMO OK")


if __name__ == "__main__":
    main()
