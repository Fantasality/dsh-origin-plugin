---
name: origin-plotting
description: 使用 DSH 的 Origin 画图/分析工具（mcp__origin__* 系列）快速出图与科学分析的方法速查（35 个工具），含文件导入、绘图计划确认流、领域模板、交付与验证、数据格式、排版/统计、错误码、常用任务模板。
whenToUse: 用户要求用 Origin 画图、导入数据文件、排版美化、拟合、FFT 频谱、3D 图、等高线、领域模板（多谱线堆叠/XRD/双Y轴/森林图/多面板）、数据分析、统计（t 检验/ANOVA/PCA/生存分析）、删点、导出 PNG/SVG 或交付 OPJU 工程时
---

# Origin 快速上手（mcp__origin__* 工具速查 v2.1）

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
- `style_mode`: default | journal(单栏89mm/双栏183mm, 投稿尺寸) | presentation；
- `family`: 调色板（ocean/nightfall/duo_warm/forest/grey_tone/low_saturation/paired），
  每套带使用约束（origin_status 可查；如 duo_warm 红蓝有方向含义勿用于无序分类）；
- 传同名 `graph_name` 重复调用会**清旧重画**（幂等，图名稳定）；
- 轴标题从列名自动推断（`temperature_C` → "Temperature (°C)"）。

## 科学边界（必须遵守）
- 不虚构/不补造数据：源数据缺失时报告缺口，等用户决定；
- **不确定用途的列先问，不自动画成新曲线**；
- 派生列（拟合/平滑/归一化产物）必须标注 derived；
- 不做未经要求的统计推断；结论经用户确认才写进图。

## 绘图计划/确认流（数据含义不确定时走这条）
1. `origin_plot_plan(columns, plot_type, ...)`：离线秒回，返回逐列画像 +
   角色建议（X/Y/误差棒/标签）+ 元素清单 + **questions 待确认问题**
   （混合列/高缺失/多个X候选）；
2. **questions 非空 → 先向用户逐条确认，再执行**；
3. `origin_execute_plan(plan_id)`：按计划 写数→画图→导出（数据留在服务端缓存，
   无需回传）。

## 出图后双保险校验
- `origin_view_graph(graph)`：渲染为**内联图片**返回，模型直接看（图型/配色/遮挡）；
- `origin_verify_graph(graph, expected_series=..., min_font_pt=8)`：**程序反读**
  （轴标题/字号/图层几何/图例/文件完整性），返回逐项 pass/fail/unreadable。
  正式交付前建议两个都跑。

## 正式交付（可编辑工程）
- `origin_export_delivery(graph, source_path='数据.csv', fmts='png,pdf')`：
  在**源文件同级**建 `<数据名>_Origin_<时间戳>/`，收纳多格式图片 + **可编辑 OPJU**
  并逐文件核验；原始数据只读不覆盖；
- `origin_save_project(path)`：单独保存 OPJU。

## 领域模板（origin_plot_template）
| template_id | 用途 | data |
|---|---|---|
| `stacked_spectra` | 多谱线纵向堆叠偏移（XPS/UV-Vis/PL/FTIR 多样品） | `{"x":[..], "spectra":{"名":[..],..}}`，`offset="auto"`, `reverse_x` |
| `xrd_pattern` | XRD 三件套（Observed 散点+Calculated 线+Difference 下移，可加相刻线） | `{"two_theta":[..],"observed":[..],"calculated":[..],"difference":[..]?,"phases":{"相":[2θ..]}?}` |
| `dual_y` | 双 Y 轴（左右轴各一条） | `{"x":[..],"left":[..],"right":[..],"left_name","right_name"}` |
| `forest` | 森林图（效应量+置信区间+零参考线） | `{"labels":[..],"effect":[..],"ci_low":[..],"ci_high":[..]}` |
| `multi_panel` | 多面板纵向堆叠（共享 X） | `{"x":[..]?,"panels":{"面板名":[..],..}}` |

## 显式样式覆盖（用户指定值优先）
`origin_plot/origin_plot_file/origin_apply_style` 支持
`style_overrides={"series_colors":["#RRGGBB",..], "line_width_pt":2.0,
"x_title":"..", "y_title":".."}`；每项回报 **applied / kept_default / rejected**，
未验证字段明确拒绝（不静默忽略）。参考图只作风格建议：不复制其中数据/文字/
拟合结果/水印，用户明确指定值 > 参考图建议 > 模板默认。

## 工具速查（35 个）
| 工具 | 用途 | 关键参数 |
|---|---|---|
| `origin_status` | 连接 + **能力握手**（版本/known_risks/features） | 无 |
| `origin_help` / `origin_catalog` | 使用速查 / 动态工具目录 | 无 |
| `origin_error_codes` | 全部稳定错误码 + 恢复建议 | 无 |
| `origin_list_graphs` / `origin_list_sheets` | 列出图页/工作表 | 无 |
| `origin_load_file` | **导入本地文件** CSV/TXT/XLSX/XLS | `path`, `sheet` |
| `origin_write_data` | 写多列数据 | `columns` |
| `origin_read_worksheet` | 读取工作表列数据 | `worksheet` |
| `origin_plot_plan` | **绘图计划**（离线） | `columns` |
| `origin_execute_plan` | **执行计划** | `plan_id` |
| `origin_plot` | 基础画图 7 种 + 误差棒 | `worksheet`, `plot_type` |
| `origin_plot_file` | 一键 写数+画图+排版+导出 | `columns`, `plot_type` |
| `origin_plot_template` | **领域模板** 5 种 | `template_id`, `data` |
| `origin_plot3d` | 3D surface / scatter | `data` |
| `origin_plot_contour` | 等高线/填充/3D线框 | `data` |
| `origin_histogram` | 直方图统计（plot=True 画图导出） | `worksheet`, `bins` |
| `origin_view_graph` | 图→内联图片（模型可看） | `graph` |
| `origin_verify_graph` | **程序反读核验** | `graph`, `expected_*` |
| `origin_apply_style` | 补应用排版/调色板/显式样式 | `graph`, `style_overrides` |
| `origin_export` | 导出 PNG/SVG/PDF/TIF/EMF | `graph`, `fmt` |
| `origin_save_project` | **保存 OPJU 工程** | `path` |
| `origin_export_delivery` | **一键交付目录**（图片+OPJU） | `graph`, `source_path` |
| `origin_filter_data` | 删点/裁剪 | `worksheet` |
| `origin_fit` | 拟合 linear/ExpDec1/Gauss/... | `worksheet`, `kind` |
| `origin_stats` | 描述统计 | `worksheet` |
| `origin_transform` | smooth/normalize/derivative/interpolate | `worksheet`, `op` |
| `origin_integrate` | 梯形法 AUC | `worksheet` |
| `origin_fft` | FFT 频谱 | `worksheet` |
| `origin_correlate` | Pearson 相关矩阵 | `worksheet` |
| `origin_peak_find` | 峰值检测 | `worksheet` |
| `origin_ttest` | t 检验 one/two/paired | `column_a`, `column_b` |
| `origin_anova` | 单因素方差分析 | `columns` |
| `origin_pca` | 主成分分析 | `columns`, `scale` |
| `origin_survival` | Kaplan-Meier 生存分析 | `time_column`, `event_column` |

## 常用任务模板（直接照抄改数据）
1. **文件出图**：`origin_load_file(path)` → `origin_plot_file(worksheet=..., plot_type="line", style_mode="journal")`
2. **不确定先规划**：`origin_plot_plan(columns)` → 确认 questions → `origin_execute_plan(plan_id)`
3. **正式交付**：出图 → `origin_view_graph` 自查 → `origin_verify_graph` 核验 → `origin_export_delivery(graph, source_path=原数据路径)`
4. 多谱线对比：`origin_plot_template("stacked_spectra", {...})`
5. XRD 精修：`origin_plot_template("xrd_pattern", {...})`
6. 直方图：`origin_histogram(worksheet, column, bins=10, plot=True)`
7. 箱线图：`origin_plot(worksheet, y_columns=[列名], plot_type="box")`
8. 误差棒：`origin_plot(worksheet, y_columns=[y], x_column=x, yerr_column=err)`
9. 拟合：`origin_fit(worksheet, x_column, y_column, kind="ExpDec1")`
10. FFT：`origin_fft(worksheet, x_column, y_column, plot_spectrum=True)`
11. 3D/等高线：`origin_plot3d` / `origin_plot_contour`
12. 检验：`origin_ttest(worksheet, column_a, column_b, kind="two")`；多组 `origin_anova`

## 错误码约定
- 所有工具返回 JSON；`ok=false` 时读三件套：
  `error_code`（稳定枚举）+ `recoverable`（可重试？）+ `next_actions`（建议）。
  常见：`worksheet_not_found` / `column_not_found` / `invalid_request` /
  `plan_not_found` / `template_unavailable` / `origin_busy_user_session`。
  需要枚举时调 `origin_error_codes`。

## 注意事项
- 分步流程：写数/导入 → `origin_plot` → `origin_view_graph` → `origin_export`。
- **不要读取仓库 README.md**——本 skill 就是完整速查；仍有疑问时调 `origin_catalog`/`origin_help`。
- `origin_status` 的 `capabilities.known_risks` 是版本坑清单（如 plotxy 204/215
  在 2026b 不可靠——box/bar 已内置走官方模板）。
- `ORIGIN_SESSION=isolated` 时不劫持已打开的 Origin（检测到进程会拒绝并提示）。
- 统计批（ttest/anova/pca/survival）纯 numpy 实现；`ORIGIN_MCP_PROFILE=compact` 可隐藏。
- 数据 <1000 点秒级完成；并发安全，多任务可同时进行。
