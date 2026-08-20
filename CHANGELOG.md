# Changelog


## 2.0.6 (2026-08-20)

**修复「已安装，重启后生效」永续显示（v2.0.5 修复方向修正）**：

- v2.0.5 给 index.js 加了 no-op `apply()`，但修复方向错误：问题不在 apply 方法，
  而在于 dshmarket `verify.js` 的 `liveIncludes(live, name)` 检查的是 **insert 行
  的 `name:` 字段值**（loader entry names），不是 loader 注册表里的 fiber。
  dsh-origin-plugin 的 insert name 是 `@deepseek-ai/dsh-mcp-client`（不是自己的
  包名），所以 `liveIncludes(live, 'dsh-origin-plugin')` 永远 false →
  `loaderLive=false` → 命中 `state='restart'` 分支。
- **v2.0.6 修复**：在 cordis.patch.yml 加一条 no-op insert
  `{ id: dsh-origin-plugin, name: dsh-origin-plugin }`，让 loader 为 dsh-origin-plugin
  自身创建 fiber → live 集合包含 `dsh-origin-plugin` → `loaderLive=true` →
  `state='live'`（已安装）。apply() 是 no-op；真正的工具来自 mcp-origin insert。
- 功能零影响：28 个 Origin 工具、绘/统计/导出全部不变。


## 2.0.5 (2026-08-20)

**修复「已安装，重启后生效」永续显示**：市场安装后重启，dshmarket 仍显示
「已安装，重启后生效」而非「已安装」。

- **根因**：`index.js` 的 default 导出是纯描述符对象（无 `apply` 方法）→
  cordis loader 无法为 dsh-origin-plugin 创建 fiber → dshmarket 的
  `verify.js` `loaderLive` 检查（L109/L149）永远 false → 命中 L162
  `state='restart'`（重启后生效）分支，即使重启后 mcp-origin fiber 已 active。
- **修复**：给 default 导出加 no-op `apply()` 方法 → loader 创建 fiber →
  `loaderLive=true` → 命中 L149 `state='live'`（已安装）。apply 体为空（bundle
  的运行时价值在 cordis.patch.yml 的 insert mcp-origin，不在 JS 代码）。
- 功能零影响：28 个 Origin 工具、绘/统计/导出全部不变。

## 2.0.4 (2026-08-20)

**修复 60s boot 挂起（第三次崩溃根因）**：v2.0.3 发布后用户通过 DSH 插件市场
重装，但 npm registry 仍是 2.0.1 旧码（npm 未认证无法 publish），导致桌面第三次
在 60s 签名崩溃。即便装上 v2.0.3，根因仍在 —— 现已定位并彻底修复。

- **服务器 stdio 传输改为纯同步 JSON-RPC 循环**（`origin_mcp_server.py` 新增
  `_sync_stdio_server()`，替代 `mcp.run(transport="stdio")`）。根因链：
  mcp 2.0.0 的 stdio 传输走 `anyio.run` → `asyncio.ProactorEventLoop.__init__`
  → `_make_self_pipe` → `_socket.socketpair()` fallback（Windows Python 无
  `_socket.socketpair`，走 127.0.0.1 listen+connect+accept）。在防火墙/安全软件
  屏蔽回环 accept 的环境下，accept() 永久阻塞 → 事件循环建不起来 → 服务器永不
  响应 `initialize` → MCP SDK `DEFAULT_REQUEST_TIMEOUT_MSEC=60000`（60s）超时 →
  dsh-mcp-client `apply()` 阻塞在 `await connection.ready` → DSH boot 挂 60s →
  desktop guard 回滚。**同步循环用 `sys.stdin.readline` +
  `sys.stdout.buffer.write+flush`，毫秒级握手，零事件循环/回环依赖。** 已实测：
  initialize + tools/list（28 工具）+ tools/call(origin_catalog) 全部 <1s 返回，
  无 stderr，无挂起。28 个工具函数 + `--selftest`/`--mcp-test`/`--json-echo` 路径
  全部保留不变（功能零影响）。
- **args 加 `-u` + env 加 `PYTHONUNBUFFERED=1`**（cordis.patch.yml +
  register_to_dsh.ps1）：双重保险，确保任何 stdout 写入即时 flush（同步循环已
  显式 `sys.stdout.buffer.flush()`，此为兜底防 stray print 缓冲）。
- **register_to_dsh.ps1 路径修正**：venv 路径 `dsch_origin_plugin` →
  `dsh_origin_plugin`（匹配实际 venv 位置）；server 指向已安装 bundle 的
  `node_modules/dsh-origin-plugin/origin_mcp_server.py`；profile patch 路径改为
  `%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml`（当前 DSH Desktop 布局）。
- **MCP SDK spawn 语义取证**：`@modelcontextprotocol/sdk` 的
  `StdioClientTransport` 用 `cross-spawn` + `shell: false`（非 shell:true），含空格
  路径不会被拆断；`DEFAULT_REQUEST_TIMEOUT_MSEC=60000`（protocol.js L8/L12）。
  mcp 2.0.0 `stdout_writer()` 确实 `await stdout.flush()`（stdio.py L205）→ 排除
  缓冲假设，根因锁定在 anyio/ProactorEventLoop 的回环 socketpair fallback。

> 注：npm registry 上 `dsh-origin-plugin` latest 仍是 2.0.1（npm 未认证）。
> v2.0.4 通过 GitHub Release 发布 tgz。用户需从 GitHub Release 手动安装，或
> 自行 `npm publish` 同步。


## 2.0.3 (2026-08-19)

**发布事故修复**：2.0.2 的修复提交（commit `b488b76`）当时**没有推送到 GitHub**，
远端 tag `v2.0.1` / `v2.0.2` 都指向了修复前的旧提交 `5eb94d4`，导致用户重新克隆/
重装拿到的仍是崩溃旧码。2.0.3 = 2.0.2 的全部修复 + 依赖补齐，**本次已真正推送**。

- **新增运行时依赖 `@deepseek-ai/dsh-mcp-client@0.1.0-rc.7`**：bundle 的
  `cordis.patch.yml` 通过 `name: '@deepseek-ai/dsh-mcp-client'` 注册工具，而该包
  此前**不在任何依赖里**，市场安装后 profile 的 node_modules 里没有它 →
  loader 无法按 name 解析该条目 → **工具静默永不注册**（DSH 照常启动、无报错）。
  现在市场 `pnpm add dsh-origin-plugin` 会把它作为传递依赖装进 profile，
  `mcp-origin` 条目才能真正激活；（对照证据：profile 里 node_modules 存在该包的
  dsh-mini/openclaw-bridge 均正常激活，缺失的仅本条目被跳过）
- 其余（均已在 2.0.2 完成并保留）：`!!js` 自定位 server 绝对路径 + 显式 `cwd` /
  `PYTHONIOENCODING`；显式 `failOnStartupError: false` 启动安全；
  `register_to_dsh.ps1` 改 config-only 覆盖、不再产生 duplicate loader entry id。
- 桌面侧日志复盘（2026-08-19 12:46 第二次崩溃）：崩溃重装与 10:39 为同一签名，
  均源于装回**旧码**（相对路径 + 旧注册脚本完整 insert）。新码装上后请确认
  node_modules/show 该依赖已注入。


## 2.0.2 (2026-08-19)

插件市场适配：**装上不坏 DSH**，同时修复打包安装后工具永不注册的 bug。

- **修复相对路径 bug**：bundle 默认 `command: python` + 相对 `origin_mcp_server.py`
  会把 server 路径解析到 DSH 可执行目录（`python: can't open file ...
  origin_mcp_server.py`），工具永远不注册。现改为 `!!js` 按
  `<DSH_HOME>/profiles/web/node_modules/dsh-origin-plugin/` 启动时自定位绝对路径
  （DSH_HOME 缺省回退 `%USERPROFILE%/.dsh`），并显式设置 `cwd` 与
  `PYTHONIOENCODING=utf8`；
- **启动安全**：显式 `failOnStartupError: false` —— server 连不上只记日志、
  不注册工具，绝不让 DSH 启动失败/挂起；
- **修复 duplicate loader entry id 启动失败**：旧版 `register_to_dsh.ps1` 写入
  完整 insert，与 bundle 层撞成两条 `mcp-origin` 会让 DSH 直接启动崩溃。
  脚本改为写 **config-only 覆盖**（同一 id 合并，不再重复）；bundle 层保证只
  插入一条 `mcp-origin`；README 明确「永不写第二个完整同名条目」；
- 全部改动不影响 28 个工具与功能（仅 bundle 装配层与注册脚本）。


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
