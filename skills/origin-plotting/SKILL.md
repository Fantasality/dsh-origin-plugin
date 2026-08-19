---
name: origin-plotting
description: 使用 DSH 的 Origin 画图/分析工具（mcp__origin__* 系列）快速出图与科学分析的方法速查（28 个工具），含数据格式、排版/统计、错误码、常用任务模板。
whenToUse: 用户要求用 Origin 画图、排版美化、拟合、FFT 频谱、3D 图、等高线、数据分析、统计（t 检验/ANOVA/PCA/生存分析）、删点或导出 PNG/SVG 时
---

# Origin 快速上手（mcp__origin__* 工具速查 v2）

## 数据格式
- `columns = {"列名": [数值, ...], ...}`：**第一列自动设为 X，其余为 Y**；
  也可传二维列表 `[[..],[..]]` 或一维列表（自动命名 Y）。
- 工具间用引用传递：`origin_write_data` 返回 `worksheet`（`[Book]Sheet`），
  `origin_plot` 返回 `graph`（短名）。

## 最快路径（一步出图 + 排版）
```json
{"columns": {"temperature_C":[20,25,30,35], "pressure_kPa":[95,101,112,118]},
 "plot_type": "line_symbol", "fmt": "png", "width": 1200,
 "style_mode": "journal", "family": "ocean",
 "title": "示例"}
```
- `style_mode`: default | journal(单栏89mm/双栏183mm, 投稿尺寸) | presentation；
- `family`: 调色板（ocean/nightfall/duo_warm/forest/grey_tone/low_saturation/paired）；
- 传同名 `graph_name` 重复调用会**清旧重画**（幂等，图名稳定）；
- 轴标题从列名自动推断（`temperature_C` → "Temperature (°C)"）。

## 视觉校验
- `origin_view_graph(graph)`：把图渲染为**内联图片**返回，模型可直接看，
  无需导出文件；可用 `max_width` 控制 token 成本。出图后建议调用它自查一次。

## 工具速查
| 工具 | 用途 | 关键参数 |
|---|---|---|
| `origin_status` | 检查连接（未启动自动启动 Origin，首连 5~45 秒） | 无 |
| `origin_help` / `origin_catalog` | 使用速查 / 动态工具目录（按分类） | 无 |
| `origin_error_codes` | 全部稳定错误码 + 恢复建议 | 无 |
| `origin_list_graphs` / `origin_list_sheets` | 列出图页/工作表 | 无 |
| `origin_write_data` | 写多列数据 | `columns` |
| `origin_read_worksheet` | 读取工作表列数据 | `worksheet` |
| `origin_plot` | 画图 line/scatter/line_symbol/column/histogram/box/bar + 误差棒 | `worksheet`, `plot_type`, `style_mode`, `family` |
| `origin_export` | 导出 PNG/SVG/PDF/TIF/EMF | `graph`, `fmt` |
| `origin_plot_file` | 一键 写数+画图+排版+导出 | `columns`, `plot_type`, `fmt` |
| `origin_view_graph` | 图→内联图片（模型可看，不落盘） | `graph` |
| `origin_apply_style` | 对已有图补应用排版/调色板/多序列区分 | `graph`, `style_mode`, `family` |
| `origin_filter_data` | 删点/裁剪（drop_rows 或 x_min/x_max） | `worksheet` |
| `origin_fit` | 拟合 linear/ExpDec1/Gauss/Polynomial/Lorentz，曲线上图 | `worksheet`, `kind` |
| `origin_plot3d` | 3D surface / scatter（plotxy 310 真机可用） | `data`, `plot_type` |
| `origin_stats` | 描述统计 count/mean/std/min/p25/median/p75/max/skew | `worksheet` |
| `origin_transform` | smooth/normalize/derivative/interpolate（写回新列） | `worksheet`, `op` |
| `origin_integrate` | 梯形法积分 AUC | `worksheet` |
| `origin_fft` | FFT 频谱（top 主频 + 可选频谱图） | `worksheet`, `plot_spectrum` |
| `origin_correlate` | Pearson 相关矩阵 | `worksheet` |
| `origin_peak_find` | 峰值检测 | `worksheet`, `min_height`, `min_distance` |
| `origin_histogram` | 直方图统计（plot=True 画图导出） | `worksheet`, `bins` |
| `origin_plot_contour` | 等高线/填充等高线/3D 线框 | `data`, `plot_type` |
| `origin_ttest` | t 检验 one/两样本(Welch)/paired | `column_a`, `column_b`, `kind`, `mu` |
| `origin_anova` | 单因素方差分析（每组一列） | `columns` |
| `origin_pca` | 主成分分析（载荷/解释方差/得分） | `columns`, `scale` |
| `origin_survival` | Kaplan-Meier 生存分析 | `time_column`, `event_column` |

## 常用任务模板（直接照抄改数据）
1. 折线/散点/柱状：`origin_plot_file(columns, plot_type="line"|"scatter"|"column", style_mode="journal")`
2. 直方图：`origin_histogram(worksheet, column, bins=10, plot=True)`
3. 箱线图：`origin_plot(worksheet, y_columns=[列名], plot_type="box")`
4. 条形图：`origin_plot(worksheet, y_columns=[值列], plot_type="bar")`
5. 误差棒：`origin_plot(worksheet, y_columns=[y], x_column=x, yerr_column=err)`
6. 可视自查：出图后 `origin_view_graph(graph)`
7. 拟合：`origin_write_data` → `origin_fit(worksheet, x_column, y_column, kind="ExpDec1")`
8. FFT：`origin_fft(worksheet, x_column, y_column, plot_spectrum=True)`
9. 3D 表面/散点：`origin_plot3d({"z":[[..],..]}, plot_type="surface")` / ({"x","y","z"}, "scatter")
10. 等高线：`origin_plot_contour({"z":[[..],..]})`
11. 删异常点：`origin_filter_data(worksheet, x_min=.., x_max=..)` 再 `origin_plot`
12. 检验：两列差异 `origin_ttest(worksheet, column_a, column_b, kind="two")`；两组以上 `origin_anova`

## 错误码约定
- 所有工具返回 JSON；`ok=false` 时读三件套：
  `error_code`（稳定枚举，可编程分支）+ `recoverable`（可重试？）+ `next_actions`（建议）。
  常见：`worksheet_not_found` / `column_not_found` / `invalid_request` /
  `origin_operation_error`。需要枚举时调 `origin_error_codes`。

## 注意事项
- 分步流程：`origin_write_data`（拿 worksheet）→ `origin_plot`（拿 graph）→
  `origin_view_graph`（视觉自查）→ `origin_export`（落盘）。
- **不要读取仓库 README.md**——本 skill 就是完整速查；仍有疑问时调 `origin_catalog`/`origin_help`。
- 数据量 <1000 点秒级完成；并发安全，多任务可同时进行。
- 统计批（ttest/anova/pca/survival）为纯 numpy 实现，无 scipy 依赖；
  `ORIGIN_MCP_PROFILE=compact` 时可隐藏。
