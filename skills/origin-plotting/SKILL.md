---
name: origin-plotting
description: 使用 DSH 的 Origin 画图/分析工具（mcp__origin__* 系列）快速出图、科学分析与细粒度改图的方法速查（43 个工具），含文件导入、绘图计划确认流、领域模板、交付与验证、逐条曲线/轴/图例/页面微调、窗口管理、数据格式、排版/统计、错误码、常用任务模板。
whenToUse: 用户要求用 Origin 画图、导入数据文件、排版美化、拟合、FFT 频谱、3D 图、等高线、领域模板（多谱线堆叠/XRD/双Y轴/森林图/多面板）、数据分析、统计（t 检验/ANOVA/PCA/生存分析）、删点、导出 PNG/SVG、交付 OPJU 工程，或对已有图做微调（换某条线颜色/加粗/隐藏、改轴范围、挪图例、调纸张尺寸、关掉多余窗口）时
---

# Origin 快速上手（mcp__origin__* 工具速查 v2.2）

## 数据格式
- `columns = {"列名": [数值, ...], ...}`：**第一列自动设为 X，其余为 Y**；
  也可传二维列表 `[[..],[..]]` 或一维列表（自动命名 Y）。
- **本地文件直接导**：`origin_load_file(path='D:/data/样品.csv')` 支持
  CSV/TXT/XLSX/XLS，中文路径/列名安全，返回 worksheet + 列类型 + 预览。
- 工具间用引用传递：`origin_write_data`/`origin_load_file` 返回 `worksheet`，
  `origin_plot` 返回 `graph`。

## 最快路径（一步出图 + 排版）
```json
{"columns": {"temperature_C":[20,25,30,35], "pressure_kPa":[95,101,112,118]},
 "plot_type": "line_symbol", "fmt": "png", "width": 1200,
 "style_mode": "journal", "family": "ocean", "title": "示例"}
```
- `style_mode`: default | journal(单栏89mm/双栏183mm) | presentation；
- `family`: 调色板（带使用约束，`origin_status` 可查）；
- 传同名 `graph_name` 重复调用会**清旧重画**（幂等）；
- 轴标题从列名自动推断（`temperature_C` → "Temperature (°C)"）。

## 🎛 细粒度改图（新手最常问："帮我只改这一条线 / 关掉几个窗口"）
**改前先看现状**：`origin_inspect_graph(graph)` → 图层几何、每条曲线颜色符号、
轴范围字号、图例位置、页面尺寸（cm+dots+dpi）一次看清；
`origin_list_pages()` → 全部页面短名（关窗/改名要准确短名）。

| 想做的事 | 调用 |
|---|---|
| 只把第 2 条线换成红色 | `origin_edit_plot(graph, [{"plot":1, "color":"#D55E00"}])` |
| 把第 1 条加粗成 2.5pt | `origin_edit_plot(graph, [{"plot":0, "line_width_pt":2.5}])` |
| 某条线改虚线/换符号/半透明 | `line_style:1` / `symbol_kind:2, symbol_size:9` / `transparency:30` |
| 暂时隐藏一条曲线 | `origin_edit_plot(graph, [{"plot":2, "visible":false}])` |
| 改轴标题/范围/网格/字号 | `origin_edit_axis(graph, axis="y", title="Pressure (kPa)", from_value=80, to_value=140, props={"show_grids":1, "label_font_pt":12, "label_bold":1})` |
| 图例挪到右上角/隐藏/改字号 | `origin_edit_legend(graph, {"position":"tr"})` / `{"visible":false}` / `{"font_size_pt":12}` |
| 改纸张尺寸（投稿单栏 8.9cm） | `origin_edit_page(graph, page_size_cm={"width":8.9,"height":6.5})` |
| 调图层位置大小（%页） | `origin_edit_page(graph, layer=0, layer_geometry_pct={"left":14,"top":8,"width":80,"height":60})` |
| 关掉多余窗口 | `origin_manage_pages("close", pages=["Book3","Book4"])` |
| 加一条峰位标注 | `origin_add_text(graph, "peak @ 30 C", x=30, y=118)` |

**通道纪律（很重要）**：
- 每次改动都返回 `status`：`applied`（写入并读回一致）/ `applied_unverified`
  （通道写入成功但该属性无读回，如线宽，**需 origin_view_graph 目视确认**）/
  `applied_adjusted`（Origin 钳制了值，readback 是实际落点）/ `rejected`（附原因）；
- 图例位置推荐用四角锚点 `position` 或 dots 坐标 `left/top`；
  `legend.x/y` 是混合单位通道，除用户给了截图坐标否则别用；
- 若返回 `window_activation_failed`：说明没能把目标图页切成活动窗口——
  用 `origin_manage_pages("activate", pages=[短名])` 显式激活后重试；
- 改完建议 `origin_view_graph` 看一眼，正式交付再 `origin_verify_graph`。

## 科学边界（必须遵守）
- 不虚构/不补造数据；**不确定用途的列先问**；派生列标注 derived；不做未经要求的统计推断。

## 绘图计划/确认流（数据含义不确定时走这条）
1. `origin_plot_plan(columns, ...)`：离线秒回，返回逐列画像 + 角色建议 +
   元素清单 + **questions 待确认问题**；2. questions 非空 → 先问用户；
3. `origin_execute_plan(plan_id)` 执行。

## 出图后双保险校验
- `origin_view_graph(graph)`：内联图片（模型直接看）；
- `origin_verify_graph(graph, expected_series=6, min_font_pt=8)`：程序反读
  （**跨全部图层**数曲线、轴标题与字号、图层几何、图例、文件完整性；逐项
  pass/fail/warn/unreadable/unreliable，**只有 fail 需要修**）。

## 正式交付（可编辑工程）
- `origin_export_delivery(graph, source_path='数据.csv', fmts='png,pdf')`：
  源文件同级建 `<数据名>_Origin_<时间戳>/`，收纳图片 + **可编辑 OPJU** 并核验；
- `origin_save_project(path)`：单独保存 OPJU。

## 领域模板（origin_plot_template）
| template_id | 用途 | data |
|---|---|---|
| `stacked_spectra` | 多谱线纵向堆叠偏移（XPS/UV-Vis/PL/FTIR） | `{"x":[..], "spectra":{"名":[..]}}`，`offset="auto"`, `reverse_x` |
| `xrd_pattern` | XRD 三件套（**双层**：上层 Observed 散点+Calculated 线满量程；下层 Difference 独立量程+零线+相刻线，X 轴严格对齐） | `{"two_theta":[..],"observed":[..],"calculated":[..],"difference":[..]?,"phases":{"相":[2θ..]}?}` |
| `dual_y` | 双 Y 轴 | `{"x":[..],"left":[..],"right":[..],"left_name","right_name"}` |
| `forest` | 森林图（效应量+CI+零参考线） | `{"labels":[..],"effect":[..],"ci_low":[..],"ci_high":[..]}` |
| `multi_panel` | 多面板纵向堆叠 | `{"x":[..]?,"panels":{"面板名":[..]}}` |

## 显式样式覆盖
`origin_plot/origin_plot_file/origin_apply_style` 支持
`style_overrides={"series_colors":["#RRGGBB",..], "line_width_pt":2.0,
"x_title":"..", "y_title":".."}`，逐项 applied/kept_default/rejected。
参考图只作风格建议，不复制其中数据/文字/水印。

## 工具速查（43 个）
连接诊断：`origin_status`（含版本/已知坑/特性握手）、`origin_help`、`origin_catalog`、
`origin_error_codes`、`origin_list_graphs`、`origin_list_sheets`、`origin_list_pages`
数据：`origin_load_file`、`origin_write_data`、`origin_read_worksheet`
规划：`origin_plot_plan`、`origin_execute_plan`
画图：`origin_plot`、`origin_plot_file`、`origin_plot_template`、`origin_plot3d`、
`origin_plot_contour`、`origin_histogram`、`origin_view_graph`、`origin_apply_style`
细粒度编辑：`origin_inspect_graph`、`origin_edit_plot`、`origin_edit_axis`、
`origin_edit_legend`、`origin_edit_page`、`origin_manage_pages`、`origin_add_text`
交付验证：`origin_verify_graph`、`origin_save_project`、`origin_export_delivery`、`origin_export`
数据编辑：`origin_filter_data`
拟合统计：`origin_fit`、`origin_stats`、`origin_transform`、`origin_integrate`、
`origin_fft`、`origin_correlate`、`origin_peak_find`、`origin_ttest`、`origin_anova`、
`origin_pca`、`origin_survival`

## 错误码约定
`ok=false` 时读三件套：`error_code` + `recoverable` + `next_actions`。
常见：`worksheet_not_found` / `column_not_found` / `invalid_request` /
`plan_not_found` / `template_unavailable` / `window_activation_failed` /
`layer_not_found` / `origin_busy_user_session`。

## 注意事项
- **不要读取仓库 README.md**——本 skill 就是完整速查；有疑问调 `origin_catalog`/`origin_help`。
- `origin_status.capabilities.known_risks` 是版本坑清单（plotxy 204/215 在 2026b
  不可靠→box/bar 已走官方模板；多 Origin 实例会让 COM 连错）。
- `ORIGIN_SESSION=isolated` 时不劫持已打开的 Origin（检测到进程会拒绝并提示）。
- 统计批纯 numpy 实现；`ORIGIN_MCP_PROFILE=compact` 可隐藏。
