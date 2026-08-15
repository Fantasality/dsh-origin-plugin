---
name: origin-plotting
description: 使用 DSH 的 Origin 画图/分析工具（mcp__origin__* 系列）快速出图与科学分析的方法速查，含数据格式、工具清单、常用任务模板。
whenToUse: 用户要求用 Origin 画图、拟合、FFT 频谱、3D 图、等高线、数据分析、统计、删点或导出 PNG/SVG 时
---

# Origin 快速上手（mcp__origin__* 工具速查）

## 数据格式
- `columns = {"列名": [数值, ...], ...}`：**第一列自动设为 X，其余为 Y**；
  也可传二维列表 `[[..],[..]]` 或一维列表（自动命名 Y）。

## 最快路径（一步出图）
调用 `mcp__origin__origin_plot_file`：
```json
{"columns": {"x": [1,2,3,4,5], "y": [1,4,9,16,25]},
 "plot_type": "line", "fmt": "png",
 "file_path": "C:/Users/<用户>/dsch_origin_plugin/output/demo.png",
 "width": 1200, "title": "示例"}
```
返回 `{"ok": true, "file": "绝对路径"}`；`file_path` 省略时自动命名到
`~/dsch_origin_plugin/output`。

## 工具速查
| 工具 | 用途 | 关键参数 |
|---|---|---|
| `origin_status` | 检查连接（未启动自动启动 Origin，首连 5~45 秒） | 无 |
| `origin_help` | 使用速查（不确定用法时调用） | 无 |
| `origin_write_data` | 写多列数据 | `columns` |
| `origin_plot` | 画图（line/scatter/line_symbol/column/histogram/box/bar，支持 yerr_column 误差棒） | `worksheet`, `plot_type` |
| `origin_export` | 导出 PNG/SVG | `graph`, `fmt`, `file_path` |
| `origin_plot_file` | 一键 写数+画图+导出 | `columns`, `plot_type`, `fmt` |
| `origin_filter_data` | 删点/裁剪（drop_rows 索引或 x_min/x_max） | `worksheet` |
| `origin_fit` | 拟合：kind="linear" 或 Origin 函数名（ExpDec1/Gauss/Polynomial/Lorentz），拟合曲线上图 | `worksheet`, `kind` |
| `origin_plot3d` | 3D surface（{"z": 2D网格}）/ scatter（{"x","y","z"}） | `data`, `plot_type` |
| `origin_stats` | 描述统计（count/mean/std/min/p25/median/p75/max/skew） | `worksheet`, `columns` |
| `origin_transform` | 平滑/归一化/微分/插值（写回新列） | `worksheet`, `op`, `method`, `window` |
| `origin_integrate` | 梯形法积分 AUC | `worksheet`, `x_column`, `y_column` |
| `origin_fft` | FFT 频谱（top 主频；plot_spectrum=True 出频谱图） | `worksheet`, `plot_spectrum` |
| `origin_correlate` | Pearson 相关矩阵 | `worksheet`, `columns` |
| `origin_peak_find` | 峰值检测（min_height/min_distance） | `worksheet` |
| `origin_histogram` | 直方图统计（plot=True 画图导出） | `worksheet`, `bins`, `plot` |
| `origin_plot_contour` | 等高线/填充等高线/3D 线框 | `data`, `plot_type` |

## 常用任务模板（直接照抄改数据）
1. 折线/散点/柱状：`origin_plot_file(columns, plot_type="line"|"scatter"|"column")`
2. 直方图：`origin_histogram(worksheet, column, bins=10, plot=True)`
3. 箱线图：`origin_plot(worksheet, y_columns=[列名], plot_type="box")`
4. 误差棒：`origin_plot(worksheet, y_columns=[y], x_column=x, yerr_column=err)`
5. 拟合：`origin_write_data` 后 `origin_fit(worksheet, x_column, y_column, kind="ExpDec1")`
6. FFT：`origin_fft(worksheet, x_column, y_column, plot_spectrum=True)`
7. 3D 表面：`origin_plot3d({"z": [[..],..]}, plot_type="surface")`
8. 3D 散点：`origin_plot3d({"x":[...],"y":[...],"z":[...]}, plot_type="scatter")`
9. 等高线：`origin_plot_contour({"z": [[..],..]})`
10. 删异常点：`origin_filter_data(worksheet, x_min=.., x_max=..)` 再 `origin_plot`

## 注意事项
- 所有工具返回 JSON；`ok=false` 时读 `error`/`hint` 字段自查。
- 分步流程：`origin_write_data`（拿 worksheet）→ `origin_plot`（拿 graph）→ `origin_export`。
- **不要读取仓库 README.md**——本 skill 就是完整速查；仍有疑问时调用 `origin_help`。
- 数据量 <1000 点秒级完成；并发安全，多个任务可同时进行。
