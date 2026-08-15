# -*- coding: utf-8 -*-
"""
DSH Origin 画图插件 —— MCP 服务器（DSH 对话调用入口）
=====================================================

通过 Model Context Protocol (stdio) 把 Origin 画图能力暴露给 DSH：
DSH 的 @deepseek-ai/dsh-mcp-client 插件会把它注册为原生工具，
模型可见名称为  mcp__origin__origin_status / origin_write_data /
origin_plot / origin_export / origin_plot_file 等。

运行（供 DSH 注册）:
    C:\\Users\\Admin\\dsch_origin_plugin\\.venv\\Scripts\\python.exe
        -X utf8 C:\\Users\\Admin\\dsch_origin_plugin\\origin_mcp_server.py

自测:
    python origin_mcp_server.py --selftest          # 引擎级完整链路
    python origin_mcp_server.py --mcp-test          # 模拟 DSH 的 MCP 协议调用
    python origin_mcp_server.py --concurrency-test  # 8 并发调用稳定性测试
"""
import json
import os
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer

import origin_engine as engine

mcp = MCPServer(
    name="origin",
    version="1.1.0",
    instructions=(
        "Origin 科学绘图工具（连接本机 Origin 2026b 自动化服务器）。"
        "画图/分析前先调用 origin_help 获取速查（秒回，含数据格式与 10 个任务模板），"
        "然后按模板直接调用；推荐一键 origin_plot_file（写数据->画图->导出文件），"
        "或分步 origin_write_data / origin_plot / origin_export。"
        "所有工具都返回 JSON 对象，ok 字段表示成败，失败时读 error/hint。"
    ),
)


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------
@mcp.tool()
def origin_status() -> dict:
    """检查 Origin 连接状态与插件环境（先调用它可确认 Origin 是否可用）。

    首次调用会自动启动 Origin（约 5~45 秒）；返回 plot_types / default_output_dir。
    """
    return engine.status()


@mcp.tool()
def origin_help() -> dict:
    """【优先调用】快速使用速查：工具清单、数据格式、典型调用模板。

    画图/分析前先调用本工具（不连接 Origin，秒回），按返回的 templates 直接调用，
    无需阅读任何文档。包含：数据格式、16 个工具用途、10 个常用任务模板。
    """
    return engine.help()


@mcp.tool()
def origin_write_data(columns: dict, worksheet: str = "") -> dict:
    """把多列数据写入 Origin 工作表。

    Args:
        columns: 数据，格式 {"列名": [数值列表], ...}，第一列自动设为 X，其余为 Y；
                 也可传二维列表 [[行1...], [行2...]] 或一维数值列表（自动命名为 Y）。
        worksheet: 已有工作表引用如 "[Book1]Sheet1"；留空则新建唯一工作表。
    Returns:
        {"ok": true, "worksheet": "[Book]Sheet", "columns": [...], "rows": N}
    """
    return engine.write_data(columns, worksheet=worksheet or None)


@mcp.tool()
def origin_plot(worksheet: str, plot_type: str = "line",
                x_column: str = "", y_columns: list = None, title: str = "",
                yerr_column: str = "") -> dict:
    """基于工作表数据画图。

    Args:
        worksheet: write_data 返回的工作表引用 "[Book]Sheet"。
        plot_type: line(折线) | scatter(散点) | line_symbol(线+符号) | column(柱状)
                   | histogram(直方图) | box(箱线图) | bar(条形图)。
        x_column: X 列名，默认取第一列。
        y_columns: Y 列名列表，默认取除 X 外全部数值列。
        yerr_column: 可选，Y 误差棒列（error bar）。
        title: 图标题。
    Returns:
        {"ok": true, "graph": "<图引用>", ...}
    """
    return engine.plot(worksheet, y_columns=y_columns, x_column=x_column or None,
                       plot_type=plot_type, title=title or None,
                       yerr_column=yerr_column or None)


@mcp.tool()
def origin_export(graph: str, fmt: str = "png", file_path: str = "",
                  width: int = 1200) -> dict:
    """把图导出为 PNG/SVG 文件。

    Args:
        graph: origin_plot 返回的图引用。
        fmt: png | svg。
        file_path: 完整输出路径（含扩展名）；留空则输出到
                   ~/dsch_origin_plugin/output 下自动命名文件。
        width: PNG 宽度像素。
    Returns:
        {"ok": true, "file": "绝对路径", "size": 字节数, "format": "png"}
    """
    return engine.export(graph, file_path=file_path or None, fmt=fmt, width=width)


@mcp.tool()
def origin_plot_file(columns: dict, plot_type: str = "line", fmt: str = "png",
                     file_path: str = "", width: int = 1200,
                     x_column: str = "", y_columns: list = None, title: str = "") -> dict:
    """一键完成：写数据 -> 画图 -> 导出文件（最常用）。

    Args:
        columns: 数据 {"列名": [数值列表], ...} 或二维列表；第一列自动为 X。
        plot_type: line | scatter | line_symbol | column。
        fmt: png | svg。
        file_path: 完整输出路径（含扩展名）；留空自动命名到
                   ~/dsch_origin_plugin/output。
        width: PNG 宽度像素。
        x_column / y_columns: 可选，指定列。
        title: 图标题。
    Returns:
        {"ok": true, "file": "绝对路径", "size": N, "format": "png", ...}
    """
    return engine.plot_file(
        columns, plot_type=plot_type, fmt=fmt, file_path=file_path or None,
        width=width, x_column=x_column or None, y_columns=y_columns, title=title or None,
    )


@mcp.tool()
def origin_filter_data(worksheet: str, drop_rows: list = None,
                       x_column: str = "", x_min: float = None,
                       x_max: float = None) -> dict:
    """删除/裁剪数据点（写回原工作表）。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"（origin_write_data 返回）。
        drop_rows: 要删除的行索引列表（0 起始），如 [2, 5, 9]。
        x_column: X 列名（默认第一列），用于范围裁剪。
        x_min / x_max: 仅保留 x 在 [x_min, x_max] 内的数据点。
    Returns:
        {"ok": true, "worksheet": ..., "kept": N, "dropped": M}
    """
    return engine.filter_data(worksheet, drop_rows=drop_rows,
                              x_column=x_column or 0, x_min=x_min, x_max=x_max)


@mcp.tool()
def origin_fit(worksheet: str, x_column: str = "", y_column: str = "",
               kind: str = "linear", plot_curve: bool = True,
               graph: str = "", title: str = "") -> dict:
    """对工作表数据做曲线拟合，可选把拟合曲线加到图上。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        x_column / y_column: X/Y 列名（默认第一列/第二列）。
        kind: "linear" 线性拟合；或 Origin 内置拟合函数名，如
              "ExpDec1"(指数衰减) / "Gauss"(高斯) / "Polynomial"(多项式) /
              "Lorentz" / "Sigmoid" 等。
        plot_curve: 是否生成拟合曲线图（原始散点 + 拟合线）。
        graph: 可选，把拟合曲线添加到已有图（短名）；留空则新建图。
        title: 新图标题。
    Returns:
        {"ok": true, "kind": ..., "parameters": {...}, "report": ...,
         "fit_curves": ..., "graph": 可选}
    """
    return engine.fit(worksheet, x_column or 0, y_column or 1, kind=kind,
                      plot_curve=plot_curve, graph=graph or None, title=title or None)


@mcp.tool()
def origin_plot3d(data: dict, plot_type: str = "surface", fmt: str = "png",
                  file_path: str = "", width: int = 1200, title: str = "") -> dict:
    """画 3D 图并导出文件。

    Args:
        data: surface 需要 {"z": [[...],...]}（2D 网格，可选 x/y 向量）；
              scatter 需要 {"x": [...], "y": [...], "z": [...]}。
        plot_type: surface(3D 表面) | scatter(3D 散点)。
        fmt: png | svg。
        file_path: 完整输出路径；留空自动命名到 ~/dsch_origin_plugin/output。
        width: PNG 宽度像素。
        title: 图标题。
    Returns:
        {"ok": true, "graph": ..., "file": "绝对路径", "size": N, "format": ...}
    """
    return engine.plot3d(data, plot_type=plot_type, fmt=fmt,
                         file_path=file_path or None, width=width, title=title or None)


@mcp.tool()
def origin_stats(worksheet: str, columns: list = None) -> dict:
    """描述性统计：count/mean/std/min/p25/median/p75/max/skew。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        columns: 列名列表；留空则统计全部列。
    Returns:
        {"ok": true, "stats": {列名: {...}}}
    """
    return engine.stats(worksheet, columns=columns)


@mcp.tool()
def origin_transform(worksheet: str, column: str = "", op: str = "smooth",
                     window: int = 5, method: str = "moving",
                     new_x: list = None, write_back: bool = True) -> dict:
    """数据变换（结果写入新列或返回）。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        column: 要变换的列名。
        op: smooth(平滑) | normalize(归一化) | derivative(微分) | interpolate(插值)。
        window: smooth 窗口大小（奇数）。
        method: smooth 用 moving(移动平均)/median(中值)；normalize 用
                minmax/zscore/sum。
        new_x: interpolate 时的新 x 网格列表。
        write_back: 是否把结果写为新列。
    Returns:
        {"ok": true, "new_column": "列名", "points": N}
    """
    return engine.transform(worksheet, column or 0, op=op, window=window,
                            method=method, new_x=new_x, write_back=write_back)


@mcp.tool()
def origin_integrate(worksheet: str, x_column: str = "", y_column: str = "") -> dict:
    """数值积分（梯形法），计算曲线下面积 AUC。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        x_column / y_column: X/Y 列名（默认第一列/第二列）。
    Returns:
        {"ok": true, "auc": 面积, "points": N}
    """
    return engine.integrate(worksheet, x_column or 0, y_column or 1)


@mcp.tool()
def origin_fft(worksheet: str, x_column: str = "", y_column: str = "",
               plot_spectrum: bool = False, file_path: str = "",
               width: int = 1200, top: int = 5) -> dict:
    """FFT 频谱分析：返回主频列表，可选画频谱图并导出。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"（x 需均匀采样）。
        x_column / y_column: X/Y 列名。
        plot_spectrum: 是否画幅度谱图并导出。
        file_path: 频谱图输出路径（留空自动命名）。
        width: PNG 宽度。
        top: 返回前 N 个主频（不含直流）。
    Returns:
        {"ok": true, "top_frequencies": [...], "nyquist": ..., "file": 可选}
    """
    return engine.fft(worksheet, x_column or 0, y_column or 1,
                      plot_spectrum=plot_spectrum, file_path=file_path or None,
                      width=width, top=top)


@mcp.tool()
def origin_correlate(worksheet: str, columns: list = None) -> dict:
    """列间 Pearson 相关矩阵。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        columns: 列名列表；留空则全部列。
    Returns:
        {"ok": true, "columns": [...], "correlation": [[...]]}
    """
    return engine.correlate(worksheet, columns=columns)


@mcp.tool()
def origin_peak_find(worksheet: str, x_column: str = "", y_column: str = "",
                     min_height: float = None, min_distance: int = 1) -> dict:
    """峰值检测：局部极大值 + 最小峰高 + 最小间距去重。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        x_column / y_column: X/Y 列名。
        min_height: 最小峰高（低于此值的峰忽略）。
        min_distance: 相邻峰最小索引间距（默认 1）。
    Returns:
        {"ok": true, "peaks": [{"index","x","y"}...], "count": N}
    """
    return engine.peak_find(worksheet, x_column or 0, y_column or 1,
                            min_height=min_height, min_distance=min_distance)


@mcp.tool()
def origin_histogram(worksheet: str, column: str = "", bins: int = 10,
                     plot: bool = False, file_path: str = "",
                     width: int = 1200) -> dict:
    """直方图统计；plot=True 时画柱状图并导出。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        column: 要统计的列名。
        bins: bin 数量。
        plot: 是否画图并导出。
        file_path: 输出路径（留空自动命名）。
    Returns:
        {"ok": true, "counts": [...], "bin_edges": [...], "file": 可选}
    """
    return engine.histogram(worksheet, column or 0, bins=bins, plot=plot,
                            file_path=file_path or None, width=width)


@mcp.tool()
def origin_plot_contour(data: dict, plot_type: str = "contour", fmt: str = "png",
                        file_path: str = "", width: int = 1200,
                        title: str = "") -> dict:
    """等高线/3D 线框图并导出。

    Args:
        data: {"z": [[...],...]}（2D 网格，可选 x/y 向量）。
        plot_type: contour(等高线) | contour_fill(填充等高线) | 3d_wire(3D 线框)。
        fmt: png | svg。
        file_path: 完整输出路径；留空自动命名。
        width: PNG 宽度像素。
        title: 图标题。
    Returns:
        {"ok": true, "graph": ..., "file": "绝对路径", "size": N}
    """
    return engine.plot_contour(data, plot_type=plot_type, fmt=fmt,
                               file_path=file_path or None, width=width,
                               title=title or None)


# ---------------------------------------------------------------------------
# 自测入口
# ---------------------------------------------------------------------------
def _selftest():
    print("== selftest: 引擎级完整链路 ==")
    out_dir = engine.DEFAULT_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    r1 = engine.write_data({"x": list(range(1, 11)), "y": [v * v for v in range(1, 11)]})
    print("write:", json.dumps(r1, ensure_ascii=False))
    if not r1.get("ok"):
        sys.exit(1)
    r2 = engine.plot(r1["worksheet"], plot_type="line", title="selftest")
    print("plot:", json.dumps(r2, ensure_ascii=False))
    if not r2.get("ok"):
        sys.exit(1)
    r3 = engine.export(r2["graph"], file_path=os.path.join(out_dir, "selftest.png"))
    print("export png:", json.dumps(r3, ensure_ascii=False))
    if not r3.get("ok"):
        sys.exit(1)
    r4 = engine.export(r2["graph"], file_path=os.path.join(out_dir, "selftest.svg"))
    print("export svg:", json.dumps(r4, ensure_ascii=False))
    if not r4.get("ok"):
        sys.exit(1)
    st = engine.status()
    print("status:", json.dumps(st, ensure_ascii=False)[:300])
    print("SELFTEST OK")


def _mcp_test():
    """模拟 DSH：用 MCP stdio 客户端协议调用服务器。"""
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-X", "utf8", os.path.abspath(__file__)],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = sorted(t.name for t in tools.tools)
                print("== mcp-test: list_tools ==")
                print(names)
                assert "origin_plot_file" in names and "origin_status" in names
                assert "origin_help" in names and "origin_fit" in names

                print("== mcp-test: call origin_help ==")
                res = await session.call_tool("origin_help", {})
                help_out = _first_text(res)
                assert '"ok": true' in help_out and "templates" in help_out
                print(help_out[:200])

                print("== mcp-test: call origin_status ==")
                res = await session.call_tool("origin_status", {})
                print(_first_text(res))

                print("== mcp-test: call origin_plot_file ==")
                res = await session.call_tool("origin_plot_file", {
                    "columns": {"t": [0.0, 1.0, 2.0, 3.0],
                                "v": [0.0, 1.2, 4.8, 10.9]},
                    "plot_type": "scatter",
                    "fmt": "svg",
                    "file_path": os.path.join(engine.DEFAULT_OUTPUT_DIR, "mcp_test.svg"),
                    "title": "mcp test",
                })
                out = _first_text(res)
                print(out)
                obj = json.loads(out)
                assert obj.get("ok") and os.path.exists(obj["file"]), "call failed"
                print("MCP-TEST OK")

    asyncio.run(run())


def _first_text(res):
    for c in res.content:
        if getattr(c, "type", "") == "text":
            return c.text
    return json.dumps(res.model_dump(), ensure_ascii=False)


def _concurrency_test():
    print("== concurrency-test: 8 线程并发各画一张图 ==")
    errors = []
    results = [None] * 8
    lock = threading.Lock()

    def worker(i):
        try:
            n = i + 1
            r = engine.plot_file(
                {"x": list(range(1, 11)), f"y{i}": [v * v * n for v in range(1, 11)]},
                plot_type="scatter" if i % 2 else "line",
                fmt="png",
                file_path=os.path.join(engine.DEFAULT_OUTPUT_DIR, f"con{i}.png"),
            )
            with lock:
                results[i] = r
        except Exception as e:
            with lock:
                errors.append(f"worker{i}: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    ok = 0
    for i, r in enumerate(results):
        if r and r.get("ok") and os.path.exists(r["file"]):
            ok += 1
            print(f"  worker{i}: OK {r['file']} ({r['size']}B)")
        else:
            print(f"  worker{i}: FAIL {r}")
    print(f"  通过 {ok}/8, 耗时 {elapsed:.2f}s, 错误: {errors}")
    if ok != 8:
        sys.exit(1)
    print("CONCURRENCY-TEST OK")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        _selftest()
    elif arg == "--mcp-test":
        _mcp_test()
    elif arg == "--concurrency-test":
        _concurrency_test()
    elif arg == "--json-echo":  # 供外部快速探测
        print(json.dumps({"server": "origin", "ok": True}))
    else:
        mcp.run(transport="stdio")
