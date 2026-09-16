---
name: origin-stats
description: Origin 统计分析 SOP（v2.3.0，11 个统计工具）：描述统计 / 变换 / 积分 / FFT / 相关 / 峰值 / 峰拟合 / t 检验 / ANOVA / PCA / 生存分析。含科学边界（自研统计仅探索用）、confidence_note 说明、结果解读与常见陷阱。
whenToUse: 用户要求统计分析（均值/显著性检验/方差分析/降维/相关/生存曲线/频谱/峰值检测/数据变换），或画图流程中需要先加工数据（ln/归一化/平滑/求导）时
---

# Origin 统计 SOP v2.3.0（origin-stats；配合 origin-plotting 主 SOP 使用）

## 科学边界（铁律）
1. 所有统计结果都带 `confidence_note`：**自研统计（numpy 实现）仅供探索性分析与图表初稿；正式发表用 SPSS/R 或 Origin 原生统计功能复核**。向用户报告结论时如实转述这一边界。
2. 不做未经要求的统计推断；统计结论写进图（如标注 p 值）前必须先给用户看数字。
3. 数据有缺失/异常先处理（`origin_filter_data` / `origin_mask_points`）再统计，统计工具不做静默补值。
4. 每次调用返回带 `trace_id` + `duration_ms`；失败按 `error_code + recovery` 处理，别盲目重试。

## 工具速查（按意图）

| 想知道 | 调用 | 关键参数/返回 |
|---|---|---|
| 一列的 mean/std/分位数/偏度 | `origin_stats(worksheet, columns=[..])` | 返回逐列 count/mean/std/min/p25/median/p75/max/skew |
| 两组有没有显著差异 | `origin_ttest(worksheet, column_a, column_b, kind="two")` | kind: one(对 mu)/two(独立样本 Welch)/paired；返回 t/p/自由度 |
| 多组均值是否全相等 | `origin_anova(worksheet, columns=[组1列,组2列,...])` | 每组一列；返回 F/p |
| 多变量降维/主成分 | `origin_pca(worksheet, columns, scale=False, n_components=None)` | 行=样本 列=变量；返回载荷/解释方差/得分 |
| 两两相关 | `origin_correlate(worksheet, columns)` | Pearson 矩阵；列长不齐自动按最短截断（返回里注明） |
| 事件时间数据 | `origin_survival(worksheet, time_column, event_column)` | Kaplan-Meier；event 列 1=事件 0=删失 |
| 频谱/周期 | `origin_fft(worksheet, x_column, y_column, plot_spectrum=True)` | 返回 top 主频 + 频谱图 |
| 找峰 | `origin_peak_find(worksheet, x_column, y_column, min_height=, min_distance=)` | 返回峰位/峰高列表 |
| 分峰拟合（谱图去卷积） | `origin_peak_fit(worksheet, ...)` | Gauss/Lorentz 组合；XPS/拉曼/红外用 |
| 曲线下面积 | `origin_integrate(worksheet, x_column, y_column, baseline="min")` | baseline: "min"/"first"/数值（DSC 焓变扣基线） |
| 数据加工（统计前处理） | `origin_transform(worksheet, column, op=..)` | smooth/normalize/derivative/interpolate/ln/log10/reciprocal/exp/sqrt/abs |

## 结果解读要点（给用户转述时用）
- **t/ANOVA**：p<0.05 说"差异显著"，同时报效应方向（均值差）与样本量；只报 p 值不专业。
- **相关矩阵**：相关≠因果；n<30 时提醒用户结论不稳。
- **PCA**：报前 2~3 个主成分的累计解释方差；载荷看绝对值大的变量。
- **FFT**：先说采样间隔假设；主频旁瓣可能是泄漏，必要时先 smooth。
- **peak_fit**：拟合优度（R²/残差）差时不要硬解读峰面积；峰太多先想物理意义。

## 常见失败
- `column_empty` / `column_all_nan`：上游数据没写进去或公式没生效 → `origin_read_worksheet` 复核上游。
- `no_data_to_fit` / `insufficient_data`：拟合列全空 / 有效点 <3 → 修数据或改用描述统计。
- `worksheet_not_found` / `column_not_found`：引用错 → `origin_list_sheets` + `origin_read_worksheet` 重建引用。
- 连不上 Origin → `origin_diagnose`（安装/COM/残留进程/目录权限），残留进程 `taskkill /F /IM Origin64.exe` 等 2 秒重连。

## 与主 SOP 的衔接
- 统计结果要画图 → 用 origin-plotting 主 SOP（`origin_plot` / `origin_plot_template`）。
- 变换产物 ≤2000 点时返回 `values` 数组可直接喂 `origin_plot_template`（不用 read_worksheet）。
- 正式交付 → `origin_verify_graph` + `origin_export_delivery`（主 SOP STEP 5/7）。
