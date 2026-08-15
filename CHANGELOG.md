# Changelog

## 1.1.0 (2026-08-16)

快速上手优化（解决"模型每次读文档才敢画图"的问题）：

- 新增 `origin_help` MCP 工具：不连接 Origin、约 1ms 秒回的速查
  （数据格式 + 16 工具清单 + 10 个常用任务模板 + 注意事项）；
- 新增 **`origin-plotting` skill**（DSH 原生 skill 机制）：
  `$DSH_HOME/skills/origin-plotting/SKILL.md`（两个候选根均已写入），
  模型目录可见、按需加载，加载即得完整用法，无需再读 README；
- 全部 MCP 工具 description 补充快速用法提示；
- server instructions 指引模型优先调用 `origin_help`；
- 回归：mcp-test 17 工具全绿、selftest OK、并发 8/8。

## 1.0.1 (2026-08-15)

科学分析能力大幅扩展（MCP 工具 8 → 16 个）：

### 新增工具
- `origin_filter_data` — 删除/裁剪数据点（按行索引 / x 范围，NaN 填充）
- `origin_fit` — 线性拟合（LinearFit）+ 非线性拟合（NLFit，Origin 内置函数
  ExpDec1/Gauss/Polynomial/Lorentz 等），拟合曲线自动上图
- `origin_plot3d` — 3D 表面图（matrix Z/X/Y + GLparafunc）/ 3D 散点图
- `origin_stats` — 描述性统计（count/mean/std/min/p25/median/p75/max/skew）
- `origin_transform` — 数据变换：smooth(移动平均/中值) / normalize(minmax/zscore/sum)
  / derivative / interpolate（结果写回新列）
- `origin_integrate` — 数值积分（梯形法）曲线下面积 AUC
- `origin_fft` — FFT 频谱分析（主频提取 + 可选频谱图导出）
- `origin_correlate` — Pearson 相关矩阵（不等长列自动截断）
- `origin_peak_find` — 峰值检测（局部极大值 + 最小峰高 + 最小间距）
- `origin_histogram` — 直方图统计（可画柱状图导出）
- `origin_plot_contour` — 等高线 / 填充等高线 / 3D 线框

### 画图类型扩展
- `origin_plot` 新增 `histogram`（numpy 分箱 + 柱状图）、`box`（Origin box 模板）、
  `bar`（plotxy 215）、`yerr_column` 误差棒支持

### 修复
- plotxy 列范围改为 `to_col_range`（`(n)` 索引形式对部分图型静默失败）
- box chart 改用 Origin 原生 box 模板（plotxy 无可靠代码）
- correlate 对长度不一致的列按最短截断而非崩溃
- 连接加固：强制 `ApplicationSI` 单实例语义；多 Origin 进程时返回警告
- 全量回归：science 20 用例 / advanced 12 用例 / 8 线程并发 8/8 / MCP 协议 16 工具

## 1.0.0 (2026-08-15)

首个发布：
- MCP 服务器（mcp__origin__*）5 个工具：origin_status / origin_write_data /
  origin_plot / origin_export / origin_plot_file
- 专用 COM 线程模型（线程亲和性安全）、单实例语义、唯一命名空间
- 结构化错误返回 + 中文排查提示
- 注册/卸载脚本（幂等、UTF-8 安全、自动备份）
- 自测：selftest / concurrency-test / mcp-test / Node SDK 握手测试
