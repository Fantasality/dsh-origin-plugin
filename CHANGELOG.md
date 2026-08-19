# Changelog


## 2.0.1 (2026-08-19)

插件市场安装修复（对应"nothing installable: …ship no prebuilt artifacts"报错）：

- 新增 `index.js` 预构建入口产物（ESM，零依赖），并声明 `main`/`exports` →
  市场 `entryArtifactExists` 判定通过，安装**不再需要 allowBuilds 放行**；
- **修复 2.0.0 tarball 漏包**：`files` 补充 `origin_errors.py` / `plot_style.py` /
  `origin_analysis.py`（此前缺失会导致装上的插件 import 即崩）；
- 本地按市场同款逻辑预验证通过（entryArtifactExists=true，hasDshManifest=true）。

## 2.0.0 (2026-08-18)

排版与统计大版本（工具 16 → 28 个）。设计上参考知名 origin-mcp 的"调色板/轴标题
语义化/按图型排版"思路并重新实现（clean-room，未复制其代码/文档），新增自研
OKLab/CVD 可读性度量：

### 排版（origin_plot / origin_plot_file 新增参数）
- `style_mode`（default/journal/presentation）：期刊单栏 89mm/双栏 183mm、
  字号/线宽/刻度/几何预设，真正落图；
- `family`（ocean/nightfall/duo_warm/forest/grey_tone/low_saturation/paired）：
  OKLab 感知色差 + 白底对比度 + 色盲(CVD)模拟后筛选的调色板；
- 多序列自动区分：线型/符号循环 + 颜色 → 色盲可读；
- `graph_name` 幂等命名：重复调用同名清旧重画，图名稳定（不再 Graph2/3）；
- 语义轴标题：从列名推断单位标题（如 temperature_C → "Temperature (°C)"），
  经真机验证 GLayer.axis('x'/'y').title 可靠落图；
- 密集数据自动降符号（marker_downscale），防止糊成一条线。
- 新增 `plot_style.py`：OKLab 色彩空间 + CVD 模拟 + 对比度 + 可读性计划，
  每一步给出 reason，可审计。

### 稳定错误码（新增 `origin_errors.py` + `origin_error_codes`）
- 全部调用返回统一 `error_code / recoverable / next_actions`；
- `_synchronized` 边界把遗留 `{ok:false}` 自动升级为结构化错误。

### 画图修复
- **bar 改用 Origin 官方 bar 模板**（真机验证 plotxy 204/215 在 2026b 会渲成
  面积图或不出图）；
- box/bar 默认列改为"第二列（首列为 X 的惯例）"；
- 3D 散点（plotxy 310）经 `wks.activate()` + 页名差检测修复，真机可出图；
- histogram 校验回归修复。

### 新增工具
- `origin_catalog`（动态工具目录，文档即实现）、`origin_error_codes`、
  `origin_list_graphs`、`origin_list_sheets`、`origin_read_worksheet`；
- `origin_view_graph`：把图渲染为**内联图片**返回（mcp ImageContent），
  视觉模型可直接看图（你的识图 kimi2.6 也验证过输出）；不落盘、可 `max_width` 控成本；
- `origin_apply_style`：对已有图补应用排版/调色板/多序列区分；
- 统计批（纯 numpy 自研，无 scipy）：`origin_ttest`（one/两样本 Welch/paired）、
  `origin_anova`（单因素 F/p）、`origin_pca`（SVD 载荷/解释方差/得分）、
  `origin_survival`（Kaplan-Meier + 中位生存时间）；
  - `ORIGIN_MCP_PROFILE=compact` 可隐藏统计批工具。

### 回归
- selftest（含新能力）/ mcp-test（28 工具 + 跨协议图片内容）/ concurrency 8/8 /
  science / advanced / com_smoke / demo 全绿；
- 视觉校验：styled 3 序列图（X=温度(°C) Y=压力(kPa)，蓝圈/橙三角）、bar 柱状图、
  密集散点——均经识图模型核对。

## 1.2.0 (2026-08-18)

DSH 插件市场（awesome-dsh-plugin / dsh-market）收录准备：

- 新增 `package.json`，声明 **`dsh.bundle`**（→ `cordis.patch.yml`），成为标准
  bundle 插件，可通过 `dsh plugin --profile web add github:Fantasality/dsh-origin-plugin`
  一键安装/收录；
- 新增 `cordis.patch.yml`：注册 `mcp-origin`（`@deepseek-ai/dsh-mcp-client`，stdio），
  server 连不上时静默降级不致命；路径可用 profile 层覆盖；
- 新增 `requirements.txt`：统一声明 `mcp / originpro / pywin32 / numpy` 依赖；
- 新增 `run_origin_server.cmd`：venv 自适应启动器（有 `.venv` 用 venv python，
  否则退回系统 python）；
- README 补充「以 bundle 方式安装」小节。
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
