# -*- coding: utf-8 -*-
"""
DSH Origin 画图插件 —— MCP 服务器（v2.1，注册式 + 交付/验证/模板/计划流）
==========================================================================

通过 Model Context Protocol (stdio) 把 Origin 能力暴露给 DSH：
  - 画图：line/scatter/line_symbol/column/histogram/box/bar + 3D surface/scatter +
    等高线；支持 style_mode(排版预设)、family(调色板) 与 style_overrides(显式样式，
    逐项 applied/rejected 回报)。
  - 预览/验证：origin_view_graph 内联图片（模型视觉自查）+ origin_verify_graph
    确定性反读（程序核对），双保险。
  - 交付：origin_save_project 可编辑 OPJU + origin_export_delivery 一键交付目录。
  - 文件：origin_load_file 导入 CSV/TXT/XLSX/XLS（中文路径/编码安全）。
  - 计划流：origin_plot_plan（离线逐列画像+角色建议+待确认问题）->
    origin_execute_plan（按 plan_id 执行）。
  - 模板：origin_plot_template 领域模板（stacked_spectra/xrd_pattern/dual_y/
    forest/multi_panel）。
  - 分析：描述统计/变换/积分/FFT/相关/峰值 + t 检验/ANOVA/PCA/生存分析。
  - 可靠：统一错误码(error_code)+recoverable+next_actions；origin_catalog 动态
    工具目录（文档即实现）；origin_status 版本能力握手（known_risks/features）。

Profile：ORIGIN_MCP_PROFILE=compact 时隐藏统计批(ttest/anova/pca/survival)。
会话：ORIGIN_SESSION=isolated 时不劫持已运行的 Origin（检测到进程即拒绝连接）。

自测:
    python origin_mcp_server.py --offline-test     # 不连 Origin：注册表/计划/文件IO/能力
    python origin_mcp_server.py --selftest         # 引擎级完整链路 + 新能力（需 Origin）
    python origin_mcp_server.py --mcp-test         # 模拟 DSH 的 MCP 协议调用（需 Origin）
    python origin_mcp_server.py --concurrency-test # 8 并发调用稳定性测试（需 Origin）
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
    version="2.1.0",
    instructions=(
        "Origin 科学绘图工具（连接本机 Origin 自动化服务器）。"
        "画图/分析前先调用 origin_help 或 origin_catalog 获取速查（秒回）。"
        "推荐一键 origin_plot_file；数据在本地文件时先 origin_load_file 导入；"
        "数据含义不确定时先 origin_plot_plan 生成计划并向用户确认再执行。"
        "出图后用 origin_view_graph（模型看图）自查，关键交付再用 "
        "origin_verify_graph 程序反读核验；正式交付用 origin_export_delivery "
        "（图片+可编辑 OPJU 集中到源文件同级目录）。"
        "科学边界：不虚构/不补数据；不确定列先问；派生列标注 derived；"
        "不静默拟合/平滑/归一化。"
        "所有工具返回 JSON：ok 字段表示成败，失败时读 error_code / "
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
    {"name": "origin_load_file", "group": "数据", "desc": "导入本地表格文件 CSV/TXT/XLSX/XLS（中文路径/编码安全）"},
    {"name": "origin_read_worksheet", "group": "数据", "desc": "读取工作表列数据（含列角色/点数）"},
    # plan / confirm
    {"name": "origin_plot_plan", "group": "规划与确认", "desc": "绘图计划（离线秒回）：逐列画像+角色建议+待确认问题 -> plan_id"},
    {"name": "origin_execute_plan", "group": "规划与确认", "desc": "按 plan_id 执行计划（写数+画图+导出）"},
    # plot / export
    {"name": "origin_plot", "group": "画图", "desc": "基于工作表画图（含 histogram/box/bar，可传 style_mode/family/style_overrides）"},
    {"name": "origin_plot_file", "group": "画图", "desc": "一键 写数+画图+导出（最常用）"},
    {"name": "origin_plot_template", "group": "画图", "desc": "领域模板：stacked_spectra/xrd_pattern/dual_y/forest/multi_panel"},
    {"name": "origin_plot3d", "group": "画图", "desc": "3D 表面 / 3D 散点"},
    {"name": "origin_plot_contour", "group": "画图", "desc": "等高线 / 填充等高线 / 3D 线框"},
    {"name": "origin_histogram", "group": "画图", "desc": "直方图统计（可画图导出）"},
    {"name": "origin_view_graph", "group": "画图", "desc": "把图渲染为内联图片（模型可看，不落盘）"},
    {"name": "origin_verify_graph", "group": "交付与验证", "desc": "确定性反读核验（轴标题/字号/几何/图例/文件完整性）"},
    {"name": "origin_apply_style", "group": "画图", "desc": "对已有图应用排版/调色板/多序列区分（显式样式逐项回报）"},
    {"name": "origin_export", "group": "画图", "desc": "导出 PNG/SVG/PDF/TIF/EMF 文件"},
    {"name": "origin_save_project", "group": "交付与验证", "desc": "保存当前项目为可编辑 OPJU"},
    {"name": "origin_export_delivery", "group": "交付与验证", "desc": "一键交付：源文件同级建目录收纳图片+OPJU 并核验"},
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
    """检查 Origin 连接状态 + 版本能力握手（先调用它可确认 Origin 是否可用）。

    首次调用会自动启动 Origin（约 5~45 秒）。返回 plot_types / templates /
    capabilities（含 origin_version_label、known_risks 版本坑清单、features
    特性可用性）。isolated 会话模式下检测到已运行 Origin 会返回
    origin_busy_user_session 而不是劫持用户窗口。
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


@mcp.tool()
def origin_load_file(path: str, worksheet: str = "", sheet: str = "",
                     max_preview_rows: int = 5) -> dict:
    """导入本地表格文件到 Origin 工作表（CSV/TXT/TSV/DAT/XLSX/XLSM/XLS）。

    中文路径/中文列名安全；编码自动探测（utf-8-sig/gbk/utf-16）；分隔符自动
    嗅探（逗号/分号/制表符/竖线/空白）；首行表头自动识别；XLSX 需要 openpyxl。

    Args:
        path: 文件绝对路径。
        worksheet: 已有工作表引用；留空则新建唯一工作表。
        sheet: XLSX/XLS 的工作表名或序号（从 1 起）；留空取第一个。
        max_preview_rows: 返回的预览行数（默认 5）。
    Returns:
        {"ok": true, "worksheet": "[Book]Sheet", "columns": [...], "rows": N,
         "column_types": {...}, "preview_rows": [[...], ...]}
    """
    return engine.load_file(path, worksheet=worksheet or None,
                            sheet=sheet or None, max_preview_rows=max_preview_rows)


@mcp.tool()
def origin_plot_plan(columns: dict, plot_type: str = "line",
                     style_mode: str = "default", family: str = "",
                     x_column: str = "", y_columns: list = None,
                     yerr_column: str = "", title: str = "", fmt: str = "png",
                     graph_name: str = "") -> dict:
    """生成绘图计划（不连 Origin，离线秒回）。数据含义不确定时先走这一步。

    返回逐列画像（dtype/缺失/范围/单调性）、角色建议（X/Y/误差棒/标签）、
    图形元素清单（图型/排版/调色板+使用约束/轴标题）、待确认问题 questions
    （混合列/高缺失/多个X候选/无单调X）。**questions 非空时应先向用户确认
    再执行**；计划缓存于服务端，执行只需 plan_id。

    Args:
        columns: 数据 {"列名": [值...]}。
        其余参数同 origin_plot。
    Returns:
        {"ok": true, "plan_id": "...", "columns_meta": [...], "roles": {...},
         "elements": {...}, "questions": [...], "science_boundaries": [...]}
    """
    import origin_plan as oplan
    return oplan.build_plan(
        columns, plot_type=plot_type, style_mode=style_mode or "default",
        family=family or None, x_column=x_column or None,
        y_columns=y_columns, yerr_column=yerr_column or None,
        title=title or None, fmt=fmt or "png", graph_name=graph_name or None)


@mcp.tool()
def origin_execute_plan(plan_id: str, fmt: str = "", file_path: str = "",
                        graph_name: str = "", width: int = 1200) -> dict:
    """按 plan_id 执行绘图计划：写数 -> 画图 -> 导出。

    Args:
        plan_id: origin_plot_plan 返回的计划 ID（服务端缓存，重启失效）。
        fmt / file_path / graph_name / width: 可选，覆盖计划中的对应参数。
    Returns:
        {"ok": true, "file": ..., "graph": ..., "worksheet": ..., "plan_id": ...}
        计划含未确认 questions 时附带 confirmation_reminder 提醒。
    """
    return engine.execute_plan(plan_id, fmt=fmt or None,
                               file_path=file_path or None,
                               graph_name=graph_name or None, width=width)


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
                style_mode: str = "default", family: str = "",
                style_overrides: dict = None) -> dict:
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
        style_overrides: 可选显式样式 {"series_colors": ["#RRGGBB", ...],
                "line_width_pt": 2.0, "x_title": "...", "y_title": "..."}；
                每项回报 applied/kept_default/rejected，未验证字段明确拒绝不静默忽略。
    Returns:
        {"ok": true, "graph": "<图引用>", "style": {...}, "style_decisions": [...]}
    """
    return engine.plot(worksheet, y_columns=y_columns, x_column=x_column or None,
                       plot_type=plot_type, title=title or None,
                       yerr_column=yerr_column or None, graph_name=graph_name or None,
                       style_mode=style_mode or "default", family=family or None,
                       style_overrides=style_overrides)


@mcp.tool()
def origin_plot_file(columns: dict, plot_type: str = "line", fmt: str = "png",
                     file_path: str = "", width: int = 1200,
                     x_column: str = "", y_columns: list = None, title: str = "",
                     graph_name: str = "", style_mode: str = "default",
                     family: str = "", style_overrides: dict = None) -> dict:
    """一键完成：写数据 -> 画图 -> 导出文件（最常用）。

    Args:
        columns: 数据 {"列名": [数值列表], ...} 或二维列表；第一列自动为 X。
        plot_type: line | scatter | line_symbol | column | histogram | box | bar。
        fmt: png | svg | pdf | tif | emf。
        file_path: 完整输出路径（含扩展名）；留空自动命名到 ~/dsch_origin_plugin/output。
        width: PNG 宽度像素。
        graph_name / style_mode / family / style_overrides: 同 origin_plot。
        title: 图标题。
    Returns:
        {"ok": true, "file": "绝对路径", "size": N, "format": ..., "graph": ..., "style": ...}
    """
    return engine.plot_file(
        columns, plot_type=plot_type, fmt=fmt, file_path=file_path or None,
        width=width, x_column=x_column or None, y_columns=y_columns, title=title or None,
        graph_name=graph_name or None, style_mode=style_mode or "default",
        family=family or None, style_overrides=style_overrides)


@mcp.tool()
def origin_plot_template(template_id: str, data: dict, graph_name: str = "",
                         title: str = "", style_mode: str = "default",
                         family: str = "", offset: str = "auto",
                         reverse_x: bool = False, fmt: str = "",
                         file_path: str = "", width: int = 1200) -> dict:
    """领域模板绘图（科研高频图型，全部由真机验证过的原语组合）。

    Args:
        template_id: stacked_spectra(多谱线纵向堆叠偏移, XPS/UV-Vis/PL/FTIR 多样品
                     对比) | xrd_pattern(XRD 三件套: Observed 散点+Calculated 线+
                     Difference 下移线, 可加 phases 相刻线) | dual_y(双 Y 轴) |
                     forest(森林图: 效应量+置信区间+零参考线) |
                     multi_panel(多面板纵向堆叠, 共享 X)。
        data: 按 template_id 提供，见各模板说明（如 stacked_spectra 需
              {'x': [..], 'spectra': {'谱线名': [..], ...}}）。
        offset: stacked_spectra 的相邻谱线间距，'auto' 或数值。
        reverse_x: stacked_spectra 反转 X 轴（结合能从高到低惯例）。
        graph_name / title / style_mode / family: 同 origin_plot。
        fmt / file_path: 可选，成功后导出文件。
    Returns:
        {"ok": true, "graph": ..., "template": ..., "style": {...}, ...}
    """
    return engine.plot_template(
        template_id, data, graph_name=graph_name or None, title=title or None,
        style_mode=style_mode or "default", family=family or None,
        offset=offset or "auto", reverse_x=bool(reverse_x), fmt=fmt or None,
        file_path=file_path or None, width=width)


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
                       family: str = "", style_overrides: dict = None) -> dict:
    """对已有图应用排版/调色板/多序列区分。

    Args:
        graph: 图短名。
        columns: Y 列名（用于推断轴标题与序列数）；留空按图内序列处理。
        style_mode: default | journal | presentation。
        family: 调色板家族。
        style_overrides: 可选显式样式（用户明确指定值优先于模板/参考建议），
            支持 series_colors/line_width_pt/x_title/y_title；每项回报
            applied/kept_default/rejected，未验证字段明确拒绝。
    Returns:
        {"ok": true, "applied": ..., "style_plan": {...}, "style_decisions": [...]}
    """
    r = engine.apply_style(graph, plot_type=plot_type, columns=columns,
                           style_mode=style_mode or "default", family=family or None,
                           style_overrides=style_overrides)
    if r.get("ok") is not None and not r.get("ok"):
        return r
    return {"ok": True, **r}


@mcp.tool()
def origin_verify_graph(graph: str = "", expected_x_title: str = "",
                        expected_y_title: str = "", min_font_pt: float = None,
                        expected_series: int = None, legend_visible: bool = None,
                        files: list = None) -> dict:
    """确定性反读核验（程序把图页对象属性读回来逐项比对）。

    与 origin_view_graph（模型看渲染图）互补，双保险。检查项：图层/曲线数、
    轴标题文本（可与期望值比对）、轴标题字号（journal 要求 >= 8pt）、图层几何
    （占页百分比越界告警）、图例状态、交付文件完整性。每项 status ∈
    pass/fail/warn/unreadable（读不到不崩溃，标 unreadable）。

    Args:
        graph: 图短名；留空用活动图。
        expected_x_title / expected_y_title: 期望的轴标题文本（可选）。
        min_font_pt: 轴标题字号下限（可选，如 journal 用 8）。
        expected_series: 期望曲线数（可选）。
        legend_visible: 期望图例显示/隐藏（可选）。
        files: 交付文件路径列表，核验存在且非空（可选）。
    Returns:
        {"ok": true, "passed": bool, "checks": [...], "issues": [...]}
    """
    return engine.verify_graph(graph=graph or None,
                               expected_x_title=expected_x_title or None,
                               expected_y_title=expected_y_title or None,
                               min_font_pt=min_font_pt,
                               expected_series=expected_series,
                               legend_visible=legend_visible, files=files)


@mcp.tool()
def origin_save_project(path: str) -> dict:
    """保存当前 Origin 项目为可编辑 OPJU（正式交付物，可在 Origin 中继续编辑）。

    保存的是当前项目全部页面（工作簿+图页）；省略扩展名自动补 .opju。
    """
    return engine.save_project(path)


@mcp.tool()
def origin_export_delivery(graph: str, source_path: str = "",
                           output_dir: str = "", fmts: str = "png,pdf",
                           width: int = 1200, save_opju: bool = True) -> dict:
    """一键交付：建规整目录收纳图片 + 可编辑 OPJU 并逐文件核验。

    传 source_path 时在源数据文件同级新建 <数据名>_Origin_<时间戳>/ 目录
    （不覆盖原始数据）；否则用 output_dir 或默认输出目录。原始数据只读。

    Args:
        graph: 图短名。
        source_path: 源数据文件路径（决定交付目录位置，推荐）。
        output_dir: 显式指定输出目录（与 source_path 二选一）。
        fmts: 逗号分隔格式，如 "png,pdf,tif"。
        save_opju: 是否同时保存 OPJU（默认 true）。
    Returns:
        {"ok": true, "delivery_dir": ..., "files": [{format,file,size,ok}],
         "opju": ..., "issues": [...], "all_ok": bool}
    """
    return engine.export_delivery(graph, source_path=source_path or None,
                                  output_dir=output_dir or None, fmts=fmts,
                                  width=width, save_opju=save_opju)


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

    # ---------------- v2.1 新能力（P0/P1） ----------------
    import tempfile
    import origin_plan as oplan

    # 1) 文件导入（utf-8-sig + 中文列名 + 误差棒列）
    tmpdir = tempfile.mkdtemp(prefix="dsh_origin_smoke_")
    csv_path = os.path.join(tmpdir, "样品数据.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("temperature_C,pressure_kPa,pressure_err\n"
                 "20,95,0.5\n25,101,0.6\n30,112,0.5\n35,118,0.7\n")
    rl = engine.load_file(csv_path)
    print("load_file:", rl.get("ok"), rl.get("worksheet"),
          json.dumps(rl.get("column_types"), ensure_ascii=False))
    if not rl.get("ok"):
        sys.exit(1)

    # 2) 计划流：build -> execute -> 交付物核验
    plan = oplan.build_plan(
        {"time_s": [1.0, 2, 3, 4, 5],
         "signal_a": [2.0, 4, 9, 16, 25],
         "signal_b": [1.0, 3, 6, 10, 15]},
        plot_type="line_symbol", style_mode="journal", family="ocean",
        title="plan-test")
    if not plan.get("ok"):
        print(json.dumps(plan, ensure_ascii=False))
        sys.exit(1)
    print("plan:", plan["plan_id"], "x =", plan["roles"]["x"],
          "y =", plan["roles"]["y"], "questions =", len(plan["questions"]))
    rexe = engine.execute_plan(plan["plan_id"])
    print("execute_plan:", rexe.get("ok"), rexe.get("graph"), rexe.get("file"))
    if not rexe.get("ok"):
        print(json.dumps(rexe, ensure_ascii=False))
        sys.exit(1)

    # 3) 确定性反读（双保险的程序侧）
    rvf = engine.verify_graph(graph=rexe["graph"], expected_series=2,
                              min_font_pt=8)
    print("verify_graph:", rvf.get("ok"), "passed =", rvf.get("passed"),
          "fail =", rvf.get("n_fail"), "unreadable =", rvf.get("n_unreadable"))
    if not rvf.get("ok") or not rvf.get("passed"):
        print(json.dumps(rvf.get("checks"), ensure_ascii=False))
        sys.exit(1)

    # 4) 领域模板：确定性三项（stacked_spectra / forest / xrd_pattern）必须过
    rt = engine.plot_template(
        "stacked_spectra",
        {"x": [400.0, 450, 500, 550, 600],
         "spectra": {"sample_A": [0.1, 0.5, 0.9, 0.4, 0.2],
                     "sample_B": [0.3, 0.6, 0.8, 0.5, 0.3]}}, fmt="png")
    print("template stacked_spectra:", rt.get("ok"), rt.get("graph"))
    if not rt.get("ok"):
        print(json.dumps(rt, ensure_ascii=False))
        sys.exit(1)
    rf = engine.plot_template(
        "forest",
        {"labels": ["S1", "S2", "S3"], "effect": [0.2, -0.1, 0.5],
         "ci_low": [0.05, -0.3, 0.2], "ci_high": [0.35, 0.1, 0.8]})
    print("template forest:", rf.get("ok"), rf.get("graph"))
    if not rf.get("ok"):
        print(json.dumps(rf, ensure_ascii=False))
        sys.exit(1)
    tw = [10.0 + 2.0 * i for i in range(35)]
    import math as _math
    obs = [100.0 * _math.exp(-((t - 26.5) ** 2) / 1.2)
           + 80.0 * _math.exp(-((t - 44.0) ** 2) / 1.6) + 3.0 for t in tw]
    calc = [100.0 * _math.exp(-((t - 26.6) ** 2) / 1.2)
            + 80.0 * _math.exp(-((t - 44.1) ** 2) / 1.6) + 3.0 for t in tw]
    rxrd = engine.plot_template(
        "xrd_pattern",
        {"two_theta": tw, "observed": obs, "calculated": calc,
         "difference": [o - c for o, c in zip(obs, calc)],
         "phases": {"TiO2_anatase": [25.3, 37.8, 48.0],
                    "TiO2_rutile": [27.4, 36.1]}}, fmt="png")
    print("template xrd_pattern:", rxrd.get("ok"), rxrd.get("graph"))
    if not rxrd.get("ok"):
        print(json.dumps(rxrd, ensure_ascii=False))
        sys.exit(1)
    # 依赖本机模板库/图层面板的项：best-effort，失败仅告警不判失败
    rdy = engine.plot_template(
        "dual_y", {"x": [1.0, 2, 3, 4, 5], "left": [1.0, 2, 3, 4, 5],
                   "right": [10.0, 20, 15, 30, 25],
                   "left_name": "Voltage (V)", "right_name": "Current (mA)"})
    print("template dual_y (best-effort):", rdy.get("ok"),
          rdy.get("graph") or rdy.get("error_code"))
    rmp = engine.plot_template(
        "multi_panel", {"x": [1.0, 2, 3, 4],
                        "panels": {"p1": [1, 4, 9, 16], "p2": [2, 3, 5, 7],
                                   "p3": [1, 3, 2, 4]}})
    print("template multi_panel (best-effort):", rmp.get("ok"),
          rmp.get("graph") or rmp.get("error_code"))

    # 5) 一键交付：源文件同级目录 + 图片 + OPJU + 完整性核验
    rdv = engine.export_delivery(rexe["graph"], source_path=csv_path,
                                 fmts="png", width=900)
    print("export_delivery:", rdv.get("ok"), rdv.get("delivery_dir"),
          "all_ok =", rdv.get("all_ok"))
    if not rdv.get("ok") or not rdv.get("all_ok"):
        print(json.dumps(rdv, ensure_ascii=False))
        sys.exit(1)
    vfiles = [f["file"] for f in rdv.get("files", []) if f.get("ok")]
    if rdv.get("opju"):
        vfiles.append(rdv["opju"])
    rvf2 = engine.verify_graph(graph=rexe["graph"], files=vfiles)
    print("verify files:", rvf2.get("passed"), "n_checks =", len(rvf2.get("checks", [])))
    if not rvf2.get("passed"):
        sys.exit(1)

    # 6) 显式样式覆盖：逐项 applied/kept_default/rejected 回报
    rso = engine.apply_style(rexe["graph"], style_mode="presentation",
                             family="nightfall",
                             style_overrides={"series_colors": ["#0072B2", "#D55E00"],
                                              "line_width_pt": 2.0,
                                              "y_title": "Signal (a.u.)",
                                              "page_size_cm": {"w": 16, "h": 12}})
    print("apply_style overrides:", rso.get("ok"),
          json.dumps(rso.get("style_decisions"), ensure_ascii=False))
    if not rso.get("ok"):
        sys.exit(1)

    # 7) 能力握手：版本 + 已知坑 + 特性表
    cap = engine.status().get("capabilities", {})
    print("capabilities:", cap.get("origin_version_label"),
          "| risks =", len(cap.get("known_risks", [])),
          "| xlsx =", cap.get("features", {}).get("xlsx_via_openpyxl"))

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


def _offline_test():
    """离线自测：不连 Origin、不拉起 COM，CI 可直接跑。

    校验：工具注册表一致性 / 错误码完整性 / 计划流（构建-缓存-角色建议）/
    模板与计划执行的前置校验（失败必须发生在连接 Origin 之前）/ 文件 IO
    （多编码+中文路径）/ 样式使用约束 / 能力矩阵静态完整性。
    """
    import inspect
    import tempfile

    import origin_errors as oerr
    import origin_fileio as fio
    import origin_plan as oplan
    import plot_style as pst

    print("== offline-test: 无 Origin 依赖检查 ==")

    # 1) 注册表一致性：TOOL_CATALOG 每一项都有可调用实现，inputSchema 可构建
    registry = _build_tool_registry()
    missing = [t["name"] for t in TOOL_CATALOG if t["name"] not in registry]
    assert not missing, f"TOOL_CATALOG 缺少实现: {missing}"
    for name, meta in registry.items():
        assert meta["inputSchema"]["type"] == "object"
        assert isinstance(meta["description"], str) and meta["description"]
    print(f"registry OK: {len(registry)} tools, schema built")

    # 2) 错误码完整性
    for code, (rec, acts) in oerr.CODE_META.items():
        assert isinstance(rec, bool) and isinstance(acts, list) and acts, code
    f = oerr.fail("worksheet_not_found", "x")
    assert f["ok"] is False and f["recoverable"] is True and f["next_actions"]
    print("error codes OK:", len(oerr.CODE_META))

    # 3) 计划流：构建 -> 缓存取回 -> 角色建议 -> 未命中
    cols = {"time_s": [1.0, 2, 3, 4, 5],
            "temperature_C": [20.0, 25, 30, 35, 40],
            "pressure_kPa": [95.0, 101, 112, 118, 130],
            "pressure_err": [0.5, 0.6, 0.5, 0.7, 0.6],
            "sample": ["A", "B", "C", "D", "E"]}
    plan = oplan.build_plan(cols, plot_type="line_symbol", style_mode="journal",
                            family="ocean", title="t")
    assert plan.get("ok"), plan
    pid = plan["plan_id"]
    got = oplan.get_plan(pid)
    assert got and got["plan_id"] == pid
    roles = plan["roles"]
    assert roles["x"] in ("time_s", "temperature_C"), roles
    assert "pressure_kPa" in roles["y"], roles
    assert roles["yerr"] == "pressure_err", roles
    assert isinstance(plan["questions"], list)
    assert plan["science_boundaries"], "科学边界清单不能为空"
    assert oplan.get_plan("no-such-plan") is None
    print("plan OK:", pid, "| roles.x =", roles["x"], "| yerr =", roles["yerr"],
          "| questions =", len(plan["questions"]))

    # 4) 模板/计划执行的前置校验（离线路径：失败必须发生在连接 Origin 之前）
    bad = engine.plot_template("no_such_template", {})
    assert not bad.get("ok") and bad.get("error_code") == "invalid_request", bad
    bad2 = engine.plot_template("stacked_spectra", {"x": [1, 2]})
    assert not bad2.get("ok") and bad2.get("error_code") == "invalid_request", bad2
    bad3 = engine.execute_plan("no-such-plan-id")
    assert not bad3.get("ok") and bad3.get("error_code") == "plan_not_found", bad3
    bad4 = engine.load_file("")
    assert not bad4.get("ok"), bad4
    print("template/plan/load offline validation OK")

    # 5) 文件 IO：utf-8-sig 中文列名 CSV / GBK 空白分隔 TXT / 混合列
    tmpdir = tempfile.mkdtemp(prefix="dsh_origin_offline_")
    p1 = os.path.join(tmpdir, "中文 样品数据.csv")
    with open(p1, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("时间_s,温度_C,压强_kPa\n1,20,95\n2,25,101\n3,30,112\n")
    r1 = fio.read_table(p1)
    assert r1.get("ok"), r1
    assert list(r1["columns"]) == ["时间_s", "温度_C", "压强_kPa"], r1["columns"]
    assert r1["n_rows"] == 3 and r1["column_types"]["温度_C"] == "numeric"
    p2 = os.path.join(tmpdir, "gbk_space.txt")
    with open(p2, "w", encoding="gbk", newline="") as fh:
        fh.write("x y err\n1 2.0 0.1\n2 4.0 0.2\n3 9.0 0.3\n")
    r2 = fio.read_table(p2)
    assert r2.get("ok"), r2
    assert r2["column_types"]["x"] == "numeric" and r2["n_rows"] == 3
    p3 = os.path.join(tmpdir, "mixed.csv")
    with open(p3, "w", encoding="utf-8", newline="") as fh:
        fh.write("v,label\n1.5,A\n2.5,B\n,C\n")
    r3 = fio.read_table(p3)
    assert r3.get("ok") and r3["column_types"]["label"] == "text", r3
    badf = fio.read_table(os.path.join(tmpdir, "missing_file.csv"))
    assert not badf.get("ok") and badf.get("error_code") == "file_error", badf
    xlsx_ok = False
    try:
        import openpyxl  # noqa: F401
        p4 = os.path.join(tmpdir, "book.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["wavelength_nm", "absorbance"])
        ws.append([300, 0.1])
        ws.append([400, 0.8])
        wb.save(p4)
        r4 = fio.read_table(p4)
        assert r4.get("ok") and r4["n_rows"] == 2, r4
        xlsx_ok = True
    except ImportError:
        pass
    print("fileio OK (xlsx:", xlsx_ok, ")")

    # 6) 样式/调色板：使用约束元数据
    pl = pst.choose_palette(3, family="ocean")
    assert pl.get("usage") and "suitable" in pl["usage"], pl
    cat = pst.palette_catalog()
    assert set(cat) == set(pst.PALETTES)
    sp = pst.full_style_plan("line", ["a", "b"], 100, style_mode="journal")
    assert sp["palette"].get("usage")
    print("style OK:", sorted(cat))

    # 7) 能力矩阵静态完整性
    risks = engine.CAPABILITY_KNOWN_RISKS
    assert risks and all("code" in r and "workaround" in r for r in risks)
    assert set(engine.PLOT_TEMPLATES) == {"stacked_spectra", "xrd_pattern",
                                          "dual_y", "forest", "multi_panel"}
    print("capabilities OK:", len(risks), "known risks,",
          len(engine.PLOT_TEMPLATES), "templates")

    print("OFFLINE-TEST OK")


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


# ---------------------------------------------------------------------------
# 同步 stdio JSON-RPC 服务器（无 anyio / 无事件循环 / 显式 flush）
# ---------------------------------------------------------------------------
# 设计动机：mcp 2.0.0 的 stdio 传输走 anyio.run → asyncio.ProactorEventLoop，
# 其 _make_self_pipe 用 _socket.socketpair() fallback（127.0.0.1 listen+connect+
# accept）。在某些 Windows 环境（防火墙/安全软件屏蔽回环 socketpair 的 accept，
# 或 Python 构建无 _socket.socketpair）下 accept() 永久阻塞 → 事件循环创建不出
# 来 → 服务器永不响应 initialize → MCP SDK 60s 超时(DEFAULT_REQUEST_TIMEOUT_MSEC)
# → DSH boot 挂 60s → desktop guard 回滚。本实现用纯同步 sys.stdin.readline +
# sys.stdout.buffer.write+flush 规避全部事件循环/回环依赖，握手毫秒级返回。
# 所有 28 个工具函数（@mcp.tool 装饰的原函数）照常调用，功能零影响。
_PROTO_FALLBACK = "2024-11-05"


def _infer_json_type(ann):
    """把 Python 类型注解映射到 JSON Schema type 字符串。"""
    if ann in (str,) or ann is None or ann is type(None):
        return "string"
    if ann is int:
        return "integer"
    if ann is float:
        return "number"
    if ann is bool:
        return "boolean"
    if ann in (list, tuple):
        return "array"
    if ann is dict:
        return "object"
    origin = getattr(ann, "__origin__", None)
    if origin in (list, tuple):
        return "array"
    if origin is dict:
        return "object"
    return "string"


def _build_tool_registry():
    """从 TOOL_CATALOG + 本模块全局函数构建同步分发注册表（name→meta+fn）。"""
    import inspect
    registry = {}
    for entry in TOOL_CATALOG:
        name = entry["name"]
        fn = globals().get(name)
        if not callable(fn):
            continue
        sig = inspect.signature(fn)
        properties = {}
        required = []
        for pname, param in sig.parameters.items():
            ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
            properties[pname] = {"type": _infer_json_type(ann)}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        doc = (fn.__doc__ or entry.get("desc", "") or "").strip()
        registry[name] = {
            "name": name,
            "description": doc,
            "inputSchema": {"type": "object", "properties": properties, "required": required},
        }
    return registry


def _result_to_content_blocks(result):
    """把工具返回值（dict / list[TextContent|ImageContent]）转成 MCP content 块。"""
    if isinstance(result, list):
        blocks = []
        for item in result:
            t = getattr(item, "type", None)
            if t is None and isinstance(item, dict):
                t = item.get("type")
            if t == "text":
                txt = getattr(item, "text", None)
                if txt is None and isinstance(item, dict):
                    txt = item.get("text")
                blocks.append({"type": "text", "text": txt or str(item)})
            elif t == "image":
                data = getattr(item, "data", None)
                if data is None and isinstance(item, dict):
                    data = item.get("data", "")
                mime = getattr(item, "mime_type", None) or getattr(item, "mimeType", None)
                if mime is None and isinstance(item, dict):
                    mime = item.get("mimeType") or item.get("mime_type")
                blocks.append({"type": "image", "data": data or "", "mimeType": mime or "image/png"})
            elif isinstance(item, dict):
                blocks.append(item)
        return blocks or [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
    if isinstance(result, dict):
        return [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
    return [{"type": "text", "text": str(result)}]


def _write_jsonrpc(resp):
    """写一行 JSON-RPC 到 stdout（二进制 buffer + 显式 flush，避免 CRLF/缓冲吞响应）。"""
    payload = json.dumps(resp, ensure_ascii=False) + "\n"
    try:
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.buffer.flush()
    except (AttributeError, ValueError, OSError):
        sys.stdout.write(payload)
        sys.stdout.flush()


def _sync_stdio_server():
    """纯同步 stdio JSON-RPC 服务器（MCP 协议兼容）。

    替代 mcp.run(transport="stdio")：不依赖 anyio / 事件循环 / 回环 socket。
    支持 initialize / notifications/initialized / ping / tools/list / tools/call。
    握手毫秒级返回，永不因事件循环阻塞导致 60s 超时。
    """
    import inspect
    registry = _build_tool_registry()
    tool_fns = {name: globals()[name] for name in registry if callable(globals().get(name))}

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # 坏行静默跳过
        if not isinstance(req, dict):
            continue
        msg_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        # 通知（id 为 None）无需响应
        if msg_id is None:
            continue

        if method == "initialize":
            client_pv = params.get("protocolVersion") if isinstance(params, dict) else None
            pv = client_pv if client_pv else _PROTO_FALLBACK
            _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": pv,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "origin", "version": "2.0.0"},
            }})
        elif method == "ping":
            _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id,
                            "result": {"tools": list(registry.values())}})
        elif method == "tools/call":
            tname = params.get("name", "") if isinstance(params, dict) else ""
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            fn = tool_fns.get(tname)
            if fn is None:
                _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": json.dumps(
                        {"ok": False, "error": f"unknown tool: {tname}",
                         "error_code": "UNKNOWN_TOOL"}, ensure_ascii=False)}],
                    "isError": True,
                }})
            else:
                try:
                    sig = inspect.signature(fn)
                    valid = {k: v for k, v in args.items() if k in sig.parameters}
                    result = fn(**valid)
                    _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id, "result": {
                        "content": _result_to_content_blocks(result),
                        "isError": False,
                    }})
                except Exception as exc:
                    import traceback
                    _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id, "result": {
                        "content": [{"type": "text", "text": json.dumps({
                            "ok": False, "error": str(exc),
                            "error_code": "TOOL_EXCEPTION",
                            "traceback": traceback.format_exc(),
                        }, ensure_ascii=False)}],
                        "isError": True,
                    }})
        else:
            _write_jsonrpc({"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32601, "message": f"method not found: {method}",
            }})


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--offline-test":
        _offline_test()
    elif arg == "--selftest":
        _selftest()
    elif arg == "--mcp-test":
        _mcp_test()
    elif arg == "--concurrency-test":
        _concurrency_test()
    elif arg == "--json-echo":  # 供外部快速探测
        print(json.dumps({"server": "origin", "ok": True, "version": "2.1.0"}))
    else:
        _sync_stdio_server()
