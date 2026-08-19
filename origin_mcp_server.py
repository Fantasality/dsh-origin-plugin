# -*- coding: utf-8 -*-
"""
DSH Origin 画图插件 —— MCP 服务器（v2，注册式 + 排版/统计增强）
================================================================

通过 Model Context Protocol (stdio) 把 Origin 能力暴露给 DSH：
  - 画图：line/scatter/line_symbol/column/histogram/box/bar + 3D surface/scatter +
    等高线；支持 style_mode(排版预设) 与 family(调色板)。
  - 预览：origin_view_graph 把图渲染成模型可见的内联图片（不落盘）。
  - 分析：描述统计/变换/积分/FFT/相关/峰值 + 新增 t 检验/ANOVA/PCA/生存分析。
  - 可靠：统一错误码(error_code)+recoverable+next_actions；origin_catalog 动态
    工具目录（文档即实现）。

Profile：ORIGIN_MCP_PROFILE=compact 时隐藏统计批(ttest/anova/pca/survival)。

自测:
    python origin_mcp_server.py --selftest          # 引擎级完整链路 + 新能力
    python origin_mcp_server.py --mcp-test          # 模拟 DSH 的 MCP 协议调用
    python origin_mcp_server.py --concurrency-test  # 8 并发调用稳定性测试
"""
import json
import os
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent, ImageContent

import origin_engine as engine

_EXTENDED = os.environ.get("ORIGIN_MCP_PROFILE", "full").lower() != "compact"

mcp = MCPServer(
    name="origin",
    version="2.0.0",
    instructions=(
        "Origin 科学绘图工具（连接本机 Origin 2026b 自动化服务器）。"
        "画图/分析前先调用 origin_help 或 origin_catalog 获取速查（秒回）。"
        "推荐一键 origin_plot_file；需要可视校验时调用 origin_view_graph（返回内联图片，"
        "模型可直接看）。所有工具返回 JSON：ok 字段表示成败，失败时读 error_code / "
        "recoverable / next_actions 安全分支重试。"
    ),
)


# ---------------------------------------------------------------------------
# 工具目录（单一事实源：origin_catalog / origin_help 都从这里生成）
# ---------------------------------------------------------------------------
TOOL_CATALOG = [
    # connect / lifecycle
    {"name": "origin_status", "group": "连接与诊断", "desc": "检查 Origin 连接状态与插件环境"},
    {"name": "origin_help", "group": "连接与诊断", "desc": "快速使用速查（不连 Origin，秒回）"},
    {"name": "origin_catalog", "group": "连接与诊断", "desc": "动态工具目录（按分类列出全部工具）"},
    {"name": "origin_error_codes", "group": "连接与诊断", "desc": "列出全部稳定错误码与恢复建议"},
    {"name": "origin_list_graphs", "group": "连接与诊断", "desc": "列出当前项目的图页短名"},
    {"name": "origin_list_sheets", "group": "连接与诊断", "desc": "列出当前项目的工作簿/工作表页"},
    # data
    {"name": "origin_write_data", "group": "数据", "desc": "把多列数据写入 Origin 工作表"},
    {"name": "origin_read_worksheet", "group": "数据", "desc": "读取工作表列数据（含列角色/点数）"},
    # plot / export
    {"name": "origin_plot", "group": "画图", "desc": "基于工作表画图（含 histogram/box/bar，可传 style_mode/family）"},
    {"name": "origin_plot_file", "group": "画图", "desc": "一键 写数+画图+导出（最常用）"},
    {"name": "origin_plot3d", "group": "画图", "desc": "3D 表面 / 3D 散点"},
    {"name": "origin_plot_contour", "group": "画图", "desc": "等高线 / 填充等高线 / 3D 线框"},
    {"name": "origin_histogram", "group": "画图", "desc": "直方图统计（可画图导出）"},
    {"name": "origin_view_graph", "group": "画图", "desc": "把图渲染为内联图片（模型可看，不落盘）"},
    {"name": "origin_apply_style", "group": "画图", "desc": "对已有图应用排版/调色板/多序列区分"},
    {"name": "origin_export", "group": "画图", "desc": "导出 PNG/SVG/PDF/TIF/EMF 文件"},
    # edit
    {"name": "origin_filter_data", "group": "数据编辑", "desc": "删除/裁剪数据点（写回原工作表）"},
    # fit / analysis
    {"name": "origin_fit", "group": "拟合与统计", "desc": "线性/非线性拟合，拟合曲线上图"},
    {"name": "origin_stats", "group": "拟合与统计", "desc": "描述统计 count/mean/std/.../skew"},
    {"name": "origin_transform", "group": "拟合与统计", "desc": "smooth/normalize/derivative/interpolate"},
    {"name": "origin_integrate", "group": "拟合与统计", "desc": "梯形法 AUC"},
    {"name": "origin_fft", "group": "拟合与统计", "desc": "FFT 频谱（主频 + 频谱图）"},
    {"name": "origin_correlate", "group": "拟合与统计", "desc": "Pearson 相关矩阵"},
    {"name": "origin_peak_find", "group": "拟合与统计", "desc": "峰值检测"},
]
if _EXTENDED:
    TOOL_CATALOG += [
        {"name": "origin_ttest", "group": "统计批", "desc": "t 检验：单样本/双样本(Welch)/配对"},
        {"name": "origin_anova", "group": "统计批", "desc": "单因素方差分析（每组一列）"},
        {"name": "origin_pca", "group": "统计批", "desc": "主成分分析（载荷/解释方差/得分）"},
        {"name": "origin_survival", "group": "统计批", "desc": "Kaplan-Meier 生存分析（时间+事件列）"},
    ]


def _text(obj) -> TextContent:
    return TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 连接与诊断
# ---------------------------------------------------------------------------
@mcp.tool()
def origin_status() -> dict:
    """检查 Origin 连接状态与插件环境（先调用它可确认 Origin 是否可用）。

    首次调用会自动启动 Origin（约 5~45 秒）；返回 plot_types / 工具数 / 错误码数。
    """
    r = engine.status()
    if r.get("ok"):
        r["tool_count"] = len(TOOL_CATALOG)
        r["extended"] = _EXTENDED
    return r


@mcp.tool()
def origin_help() -> dict:
    """【优先调用】快速使用速查：工具清单、数据格式、典型调用模板。

    画图/分析前先调用本工具（不连接 Origin，秒回），按返回的 templates 直接调用。
    """
    r = engine.help()
    if r.get("ok"):
        r["tool_count"] = len(TOOL_CATALOG)
        r["error_codes"] = engine.error_codes() if _EXTENDED else None
    return r


@mcp.tool()
def origin_catalog(group: str = "") -> dict:
    """动态工具目录：按分类列出全部工具（文档即实现，永不与代码脱节）。

    Args:
        group: 分类名（连接与诊断/数据/画图/数据编辑/拟合与统计/统计批）；留空返回全部分类。
    Returns:
        {"ok": true, "groups": {...}, "total": N}
    """
    groups = {}
    for t in TOOL_CATALOG:
        groups.setdefault(t["group"], []).append({"name": t["name"], "desc": t["desc"]})
    if group:
        groups = {g: v for g, v in groups.items() if g == group}
    return {"ok": True, "groups": groups, "total": len(TOOL_CATALOG)}


@mcp.tool()
def origin_error_codes() -> dict:
    """列出全部稳定错误码与恢复建议（供模型安全分支/重试）。"""
    return engine.error_codes()


@mcp.tool()
def origin_list_graphs() -> dict:
    """列出当前 Origin 项目里的图页短名（供 export/view/apply_style 引用）。"""
    return engine.list_graphs()


@mcp.tool()
def origin_list_sheets() -> dict:
    """列出当前 Origin 项目的工作簿/工作表页短名。"""
    return engine.list_sheets()


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
@mcp.tool()
def origin_write_data(columns: dict, worksheet: str = "") -> dict:
    """把多列数据写入 Origin 工作表。

    Args:
        columns: 数据 {"列名": [数值列表], ...}，第一列自动设为 X，其余为 Y；
                 也可传二维列表或一维数值列表。
        worksheet: 已有工作表引用如 "[Book1]Sheet1"；留空则新建唯一工作表。
    Returns:
        {"ok": true, "worksheet": "[Book]Sheet", "columns": [...], "rows": N}
    """
    return engine.write_data(columns, worksheet=worksheet or None)


@mcp.tool()
def origin_read_worksheet(worksheet: str, columns: list = None, max_rows: int = None) -> dict:
    """读取工作表列数据。

    Args:
        worksheet: 工作表引用 "[Book]Sheet"。
        columns: 列名列表；留空则全部列。
        max_rows: 可选，每列最多读取前 N 行。
    Returns:
        {"ok": true, "columns": {列名: [数值...]}, "column_meta": [...], "n_rows": N}
    """
    return engine.read_worksheet(worksheet, columns=columns, max_rows=max_rows)


# ---------------------------------------------------------------------------
# 画图 / 导出
# ---------------------------------------------------------------------------
_PLOT_ARGS = """    Args:
        worksheet: write_data 返回的工作表引用 "[Book]Sheet"。
        plot_type: line(折线) | scatter(散点) | line_symbol(线+符号) | column(柱状)
                   | histogram(直方图) | box(箱线图) | bar(条形图)。
        x_column / y_columns: X/Y 列（默认 X=第一列，Y=其余数值列）。
        yerr_column: 可选 Y 误差棒列。
        title: 图标题。
        graph_name: 可选图页短名；重复调用同名时清旧重画（幂等，不产生 Graph2/3）。
        style_mode: default | journal | presentation —— 排版预设（字号/线宽/几何）。
        family: 可选调色板家族（ocean/nightfall/duo_warm/forest/grey_tone/
                low_saturation/paired）。
    Returns:
        {"ok": true, "graph": "<图引用>", "style": {...}, ...}
"""


@mcp.tool()
def origin_plot(worksheet: str, plot_type: str = "line",
                x_column: str = "", y_columns: list = None, title: str = "",
                yerr_column: str = "", graph_name: str = "",
                style_mode: str = "default", family: str = "") -> dict:
    """基于工作表数据画图。

    Args:
        worksheet: write_data 返回的工作表引用 "[Book]Sheet"。
        plot_type: line(折线) | scatter(散点) | line_symbol(线+符号) | column(柱状)
                   | histogram(直方图) | box(箱线图) | bar(条形图)。
        x_column / y_columns: X/Y 列（默认 X=第一列，Y=其余数值列）。
        yerr_column: 可选 Y 误差棒列。
        title: 图标题。
        graph_name: 可选图页短名；重复调用同名时清旧重画（幂等，不产生 Graph2/3）。
        style_mode: default | journal | presentation —— 排版预设（字号/线宽/几何）。
        family: 可选调色板家族（ocean/nightfall/duo_warm/forest/grey_tone/
                low_saturation/paired）。
    Returns:
        {"ok": true, "graph": "<图引用>", "style": {...}, ...}
    """
    return engine.plot(worksheet, y_columns=y_columns, x_column=x_column or None,
                       plot_type=plot_type, title=title or None,
                       yerr_column=yerr_column or None, graph_name=graph_name or None,
                       style_mode=style_mode or "default", family=family or None)


@mcp.tool()
def origin_plot_file(columns: dict, plot_type: str = "line", fmt: str = "png",
                     file_path: str = "", width: int = 1200,
                     x_column: str = "", y_columns: list = None, title: str = "",
                     graph_name: str = "", style_mode: str = "default",
                     family: str = "") -> dict:
    """一键完成：写数据 -> 画图 -> 导出文件（最常用）。

    Args:
        columns: 数据 {"列名": [数值列表], ...} 或二维列表；第一列自动为 X。
        plot_type: line | scatter | line_symbol | column | histogram | box | bar。
        fmt: png | svg | pdf | tif | emf。
        file_path: 完整输出路径（含扩展名）；留空自动命名到 ~/dsch_origin_plugin/output。
        width: PNG 宽度像素。
        graph_name / style_mode / family: 同 origin_plot。
        title: 图标题。
    Returns:
        {"ok": true, "file": "绝对路径", "size": N, "format": ..., "graph": ..., "style": ...}
    """
    return engine.plot_file(
        columns, plot_type=plot_type, fmt=fmt, file_path=file_path or None,
        width=width, x_column=x_column or None, y_columns=y_columns, title=title or None,
        graph_name=graph_name or None, style_mode=style_mode or "default",
        family=family or None)


@mcp.tool()
def origin_export(graph: str, fmt: str = "png", file_path: str = "",
                  width: int = 1200) -> dict:
    """把图导出为 PNG/SVG/PDF/TIF/EMF 文件。

    Args:
        graph: origin_plot 返回的图引用（或 origin_list_graphs 的短名）。
        fmt: png | svg | pdf | tif | emf。
        file_path: 完整输出路径（含扩展名）；留空自动命名到 ~/dsch_origin_plugin/output。
        width: PNG 宽度像素。
    Returns:
        {"ok": true, "file": "绝对路径", "size": 字节数, "format": "png"}
    """
    return engine.export(graph, file_path=file_path or None, fmt=fmt, width=width)


@mcp.tool()
def origin_plot3d(data: dict, plot_type: str = "surface", fmt: str = "png",
                  file_path: str = "", width: int = 1200, title: str = "") -> dict:
    """画 3D 图并导出文件。

    Args:
        data: surface 需要 {"z": [[...],...]}（2D 网格，可选 x/y 向量）；
              scatter 需要 {"x": [...], "y": [...], "z": [...]}。
        plot_type: surface(3D 表面) | scatter(3D 散点)。
    Returns:
        {"ok": true, "graph": ..., "file": "绝对路径", ...}
    """
    return engine.plot3d(data, plot_type=plot_type, fmt=fmt,
                         file_path=file_path or None, width=width, title=title or None)


@mcp.tool()
def origin_plot_contour(data: dict, plot_type: str = "contour", fmt: str = "png",
                        file_path: str = "", width: int = 1200,
                        title: str = "") -> dict:
    """等高线/3D 线框图并导出。

    Args:
        data: {"z": [[...],...]}（2D 网格，可选 x/y 向量）。
        plot_type: contour | contour_fill | 3d_wire。
    Returns:
        {"ok": true, "graph": ..., "file": "绝对路径", ...}
    """
    return engine.plot_contour(data, plot_type=plot_type, fmt=fmt,
                               file_path=file_path or None, width=width,
                               title=title or None)


@mcp.tool()
def origin_histogram(worksheet: str, column: str = "", bins: int = 10,
                     plot: bool = False, file_path: str = "",
                     width: int = 1200) -> dict:
    """直方图统计；plot=True 时画柱状图并导出。"""
    return engine.histogram(worksheet, column or 0, bins=bins, plot=plot,
                            file_path=file_path or None, width=width)


@mcp.tool()
def origin_view_graph(graph: str = "", max_width: int = 1400) -> list:
    """把图渲染为内联图片（模型可直接看），不落盘。

    Args:
        graph: 图短名；留空用活动图。
        max_width: 渲染宽度像素上限（控制图片 token 成本）。
    Returns:
        [文本摘要, 图片内容块]；文本含 {"ok"...}，图片可直接被视觉模型理解。
    """
    r = engine.view_graph(graph=graph or None, max_width=max_width, fmt="png")
    if not r.get("ok"):
        return [_text(r)]
    summary = {"ok": True, "graph": r.get("graph"), "format": r.get("format"),
               "size": r.get("size"), "width_px": r.get("width_px"),
               "detail": r.get("detail")}
    b64 = r.get("image_png_base64", "")
    return [
        TextContent(type="text",
                    text=json.dumps(summary, ensure_ascii=False)),
        ImageContent(type="image", data=b64, mime_type="image/png"),
    ]


@mcp.tool()
def origin_apply_style(graph: str, plot_type: str = "line",
                       columns: list = None, style_mode: str = "default",
                       family: str = "") -> dict:
    """对已有图应用排版/调色板/多序列区分。

    Args:
        graph: 图短名。
        columns: Y 列名（用于推断轴标题与序列数）；留空按图内序列处理。
        style_mode: default | journal | presentation。
        family: 调色板家族。
    Returns:
        {"ok": true, "applied": ..., "style_plan": {...}}
    """
    r = engine.apply_style(graph, plot_type=plot_type, columns=columns,
                           style_mode=style_mode or "default", family=family or None)
    if r.get("ok") is not None and not r.get("ok"):
        return r
    return {"ok": True, **r}


# ---------------------------------------------------------------------------
# 数据编辑
# ---------------------------------------------------------------------------
@mcp.tool()
def origin_filter_data(worksheet: str, drop_rows: list = None,
                       x_column: str = "", x_min: float = None,
                       x_max: float = None) -> dict:
    """删除/裁剪数据点（写回原工作表）。"""
    return engine.filter_data(worksheet, drop_rows=drop_rows,
                              x_column=x_column or 0, x_min=x_min, x_max=x_max)


# ---------------------------------------------------------------------------
# 拟合与统计
# ---------------------------------------------------------------------------
@mcp.tool()
def origin_fit(worksheet: str, x_column: str = "", y_column: str = "",
               kind: str = "linear", plot_curve: bool = True,
               graph: str = "", title: str = "") -> dict:
    """对工作表数据做曲线拟合，可选把拟合曲线加到图上。"""
    return engine.fit(worksheet, x_column or 0, y_column or 1, kind=kind,
                      plot_curve=plot_curve, graph=graph or None, title=title or None)


@mcp.tool()
def origin_stats(worksheet: str, columns: list = None) -> dict:
    """描述性统计：count/mean/std/min/p25/median/p75/max/skew。"""
    return engine.stats(worksheet, columns=columns)


@mcp.tool()
def origin_transform(worksheet: str, column: str = "", op: str = "smooth",
                     window: int = 5, method: str = "moving",
                     new_x: list = None, write_back: bool = True) -> dict:
    """数据变换（结果写入新列或返回）。"""
    return engine.transform(worksheet, column or 0, op=op, window=window,
                            method=method, new_x=new_x, write_back=write_back)


@mcp.tool()
def origin_integrate(worksheet: str, x_column: str = "", y_column: str = "") -> dict:
    """数值积分（梯形法），计算曲线下面积 AUC。"""
    return engine.integrate(worksheet, x_column or 0, y_column or 1)


@mcp.tool()
def origin_fft(worksheet: str, x_column: str = "", y_column: str = "",
               plot_spectrum: bool = False, file_path: str = "",
               width: int = 1200, top: int = 5) -> dict:
    """FFT 频谱分析：返回主频列表，可选画频谱图并导出。"""
    return engine.fft(worksheet, x_column or 0, y_column or 1,
                      plot_spectrum=plot_spectrum, file_path=file_path or None,
                      width=width, top=top)


@mcp.tool()
def origin_correlate(worksheet: str, columns: list = None) -> dict:
    """列间 Pearson 相关矩阵。"""
    return engine.correlate(worksheet, columns=columns)


@mcp.tool()
def origin_peak_find(worksheet: str, x_column: str = "", y_column: str = "",
                     min_height: float = None, min_distance: int = 1) -> dict:
    """峰值检测：局部极大值 + 最小峰高 + 最小间距去重。"""
    return engine.peak_find(worksheet, x_column or 0, y_column or 1,
                            min_height=min_height, min_distance=min_distance)


# ---------------------------------------------------------------------------
# 统计批（compact profile 下不注册）
# ---------------------------------------------------------------------------
if _EXTENDED:
    @mcp.tool()
    def origin_ttest(worksheet: str, column_a: str, column_b: str = "",
                     kind: str = "two", paired: bool = False, mu: float = 0.0) -> dict:
        """t 检验：单样本 / 双样本(Welch) / 配对。

        Args:
            worksheet: 工作表引用。
            column_a / column_b: 参与检验的列名。
            kind: one(单样本 vs mu) | two(双样本 Welch) | paired(配对)。
            paired: 兼容参数：kind=two 且 paired=True 视同 kind=paired。
            mu: kind=one 时的零假设均值（默认 0）。
        Returns:
            {"ok": true, "statistic": t, "df": ..., "p_value": ..., "mean_a": ...}
        """
        k = (kind or "two").lower()
        if paired and k == "two":
            k = "paired"
        return engine.ttest(worksheet, column_a=column_a, column_b=column_b or None,
                            kind=k, mu=mu)

    @mcp.tool()
    def origin_anova(worksheet: str, columns: list) -> dict:
        """单因素方差分析（ANOVA）：每组一列，返回 F 与 p 值。"""
        return engine.anova(worksheet, columns=columns)

    @mcp.tool()
    def origin_pca(worksheet: str, columns: list = None, scale: bool = False,
                   n_components: int = None) -> dict:
        """主成分分析（把每列当作变量、每行为样本）。

        Args:
            columns: 变量列；留空全部列。
            scale: 是否标准化到单位方差。
            n_components: 返回前 N 个主成分（默认全部）。
        Returns:
            {"ok": true, "explained_variance_ratio": [...], "loadings": [...], ...}
        """
        return engine.pca(worksheet, columns=columns, scale=scale,
                          n_components=n_components)

    @mcp.tool()
    def origin_survival(worksheet: str, time_column: str, event_column: str) -> dict:
        """Kaplan-Meier 生存分析。

        Args:
            time_column: 生存时间列。
            event_column: 事件列（1=事件发生，0=删失）。
        Returns:
            {"ok": true, "events": [...KM表...], "median_survival_time": ...}
        """
        return engine.survival(worksheet, time_column=time_column,
                               event_column=event_column)


# ---------------------------------------------------------------------------
# 自测入口
# ---------------------------------------------------------------------------
def _selftest():
    print("== selftest: 引擎级完整链路 + 新能力 ==")
    out_dir = engine.DEFAULT_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    r1 = engine.write_data({"x": list(range(1, 11)),
                            "y": [v * v for v in range(1, 11)],
                            "z": [v * 2 for v in range(1, 11)]})
    print("write:", json.dumps(r1, ensure_ascii=False))
    if not r1.get("ok"):
        sys.exit(1)
    w = r1["worksheet"]

    r2 = engine.plot(w, plot_type="line", title="selftest", style_mode="journal",
                     family="paired")
    print("plot(style mode=journal, family=paired):",
          json.dumps({k: r2.get(k) for k in ("ok", "graph", "style")}, ensure_ascii=False))
    if not r2.get("ok"):
        sys.exit(1)
    g = r2["graph"]

    r3 = engine.export(g, file_path=os.path.join(out_dir, "selftest.png"))
    print("export png:", r3.get("ok"), r3.get("file"))
    if not r3.get("ok"):
        sys.exit(1)

    # 幂等命名：同名第二次画图，图名不变
    r2b = engine.plot(w, plot_type="line_symbol", graph_name=g, title="selftest-2")
    if r2b.get("graph") != g:
        print(f"idempotent FAIL: {r2b.get('graph')} != {g}")
        sys.exit(1)
    print("idempotent graph_name OK:", r2b.get("graph"))

    r4 = engine.view_graph(graph=g, max_width=900)
    print("view_graph:", r4.get("ok"), "bytes=", r4.get("size"),
          "b64len=", len(r4.get("image_png_base64", "")))
    if not r4.get("ok") or "image_png_base64" not in r4:
        sys.exit(1)

    # 错误码路径：坏工作表引用返回统一错误结构
    r5 = engine.plot("[NoSuchBook]Missing")
    print("error-path:", json.dumps({k: r5.get(k) for k in ("ok", "error_code", "recoverable")},
                                    ensure_ascii=False))
    if r5.get("ok") or "error_code" not in r5:
        sys.exit(1)

    # 统计批
    r6 = engine.ttest(w, column_a="y", column_b="z", kind="two")
    print("ttest:", r6.get("ok"), round(r6.get("statistic", 0), 3),
          round(r6.get("p_value", 0), 5))
    if not r6.get("ok"):
        sys.exit(1)
    r7 = engine.anova(w, ["y", "z"])
    print("anova:", r7.get("ok"), round(r7.get("f_statistic", 0), 3))
    if not r7.get("ok"):
        sys.exit(1)
    r8 = engine.pca(w, ["y", "z"])
    print("pca evr:", [round(x, 3) for x in r8.get("explained_variance_ratio", [])])
    if not r8.get("ok"):
        sys.exit(1)
    sw = engine.write_data({"t": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                            "ev": [1, 0, 1, 1, 0, 1, 1, 0, 1, 1]})
    r9 = engine.survival(sw["worksheet"], time_column="t", event_column="ev")
    print("survival median:", r9.get("median_survival_time"))
    if not r9.get("ok"):
        sys.exit(1)

    r10 = engine.read_worksheet(w, ["y"])
    print("read_worksheet:", r10.get("ok"), list(r10.get("columns", {})), r10.get("n_rows"))
    if not r10.get("ok"):
        sys.exit(1)

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
                for must in ("origin_plot_file", "origin_status", "origin_catalog",
                             "origin_view_graph", "origin_ttest", "origin_pca"):
                    assert must in names, f"missing {must}"

                print("== mcp-test: origin_catalog ==")
                res = await session.call_tool("origin_catalog", {})
                out = _first_text(res)
                assert '"ok": true' in out and "statistic" not in out
                print(out[:220])

                print("== mcp-test: origin_view_graph (image content) ==")
                wr = await session.call_tool("origin_write_data",
                                             {"columns": {"t": [1, 2, 3], "v": [2, 4, 9]}})
                wsheet = json.loads(_first_text(wr))["worksheet"]
                pr = await session.call_tool("origin_plot",
                                             {"worksheet": wsheet, "plot_type": "scatter",
                                              "graph_name": "mcpview", "title": "mcp test"})
                gname = json.loads(_first_text(pr))["graph"]
                res = await session.call_tool("origin_view_graph", {"graph": gname})
                has_img = any(getattr(c, "type", "") == "image" for c in res.content)
                print("view has image content:", has_img)
                assert has_img, "no image content"

                print("== mcp-test: origin_ttest ==")
                res = await session.call_tool("origin_ttest",
                                              {"worksheet": wsheet, "column_a": "t",
                                               "column_b": "v", "kind": "two"})
                out = _first_text(res)
                assert '"ok": true' in out and '"p_value"' in out
                print(out[:200])
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
        print(json.dumps({"server": "origin", "ok": True, "version": "2.0.0"}))
    else:
        mcp.run(transport="stdio")
