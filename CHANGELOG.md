# Changelog

## 2.7.2 (2026-09-18)

**两份真实使用过程（Codex 按手册安装会话 + Origin 2024 SR1 导出故障复盘）暴露的 bug 修复。**

### 修复
1. **find_graph TypeError（originpro 1.1.15 库缺陷）**：该版本 `find_graph(name)` 会把
   对象传给只接受 int/str 的 `Pages()` 而抛 TypeError。新增 `_safe_find_graph()`
   （先走库方法，TypeError 时退化为遍历页面按短名匹配），替换引擎内 8 处裸调用，
   `op_find_graph` 统一复用——异常不再冒泡，找不到图一律返回 `graph_not_found`。
2. **Origin 自动化能力不再"静默失败"**：Origin 2024 SR1 上出现过「新建文档窗口」与
   「导出文件」两类操作被运行时静默禁用（`newbook`/`expGraph` 一律返回 False 且不报错，
   排查花了半小时）。`origin_diagnose(connect_probe=true)` 新增 **automation_capability
   探测**：主动试建临时表与临图导出，以"文件是否真的落盘"裁决（不信任通道返回值，
   也不依赖读不出来的 LabTalk 系统变量），失败时明确报「Origin 自动化能力受限」
   并给修复路径（控制面板修复安装 / 或只写数据、GUI 导出）。
3. **潜在死锁**：`_diagnose_impl` 运行在 COM 线程内却调用了 `@_synchronized` 包装的
   `connect()`，改为裸 `_connect_impl()`（项目纪律：COM 线程内不得调包装函数）。

### 文档
- install-dsh-origin SKILL：**去掉写死的版本号**（不锁版本，默认 latest 即最新）；
  第 0 步改为"快查优先、不要一上来全盘递归搜 Origin64.exe"（Codex 会话实测全盘
  `-Recurse` 极慢）；第 2 步强调 `pip install -r requirements.txt` 装全套依赖
  （客户端 runtime 切换后依赖会丢失，实测坑）；排障表补"客户端看不到工具"处置。
- README 顶部增加分发渠道与适配徽章（npm / GitHub 动态徽章 + dshfind / 1024Store /
  npmmirror 收录 + Windows / Origin / Python 适配）。

### 验证
冒烟 6/6（diagnose 探测不卡死 + 能力探测 + find_graph 安全包装 + 不存在的图不崩）；
回归 offline + pytest 15 + 四 SKILL 一致性 + selftest + fine-edit + repro +
chem v2 15/15 + 视觉基准 22/22 全绿。

## 2.7.1 (2026-09-17)

**标注盲试治理 + Nature 预设 + 静默陷阱门禁**（甲烷 NMR 案例复盘：150+ 轮/30 分钟）。

### 新工具（70 → 74）
- **`origin_annotate`**：批量文本标注——一次调用加 N 个文本（统一样式/可选左对齐），
  返回逐项落位与 LabTalk 对象名（TextN）。消灭渲染-看图-再调循环。
- **`origin_layout_info`**：布局几何一次返回——数据↔像素映射（**反向轴自动翻转**）、
  轴范围、页尺寸(cm)、现有文本对象（TextN 清单）、图例位置。标注前先调它，精确落位。
- **`origin_simulate`**：物理模型谱图模拟（Lorentzian/Gaussian/Voigt 多峰+噪声），
  返回 columns 可直接喂 origin_figure；**强制 simulated=true 标记**（不虚构数据纪律）。
- **`origin_find_peaks`**：找局部极大峰（numpy），返回可转标注的峰列表。

### 增强
- **`origin_figure intent="nature"`**：Nature 单栏预设（89mm@600dpi=2100px + journal 样式，
  依据 Nature 官方 research-figure-guide：文字 5-7pt、Arial/Helvetica、线图 ≥1000dpi）。
- **`origin_figure label_peaks=true`**：自动找峰并标注（NMR/PL/拉曼标峰一次成型，
  标注自动抬升量程 6% 落峰顶上方，返回 TextN 对象名）。
- **LabTalk 静默陷阱门禁（引擎级）**：grand() / data(n1,n2) / col()[LName]$ /
  nlabels/label.count / plotxy 204/215 —— 不报错但悄悄不干活，默认拦截并给替代写法
  （force_silent=true 显式放行）。新错误码 labtalk_silent_trap。
- **`origin_labtalk` 新增 force_silent 参数**。

### 文档
- **新增 docs/FIGURE-STYLE-GUIDE.md**：化学各领域图样式规范知识库——Nature 官方硬性
  规范（89/183mm、5-7pt、1000dpi、Okabe-Ito 色盲调色板）+ NMR/XRD/Raman/FTIR/UV-Vis/
  电化学(CV·GCD·EIS·Tafel)/SEM·TEM/热分析 各子领域轴方向与标注惯例 + 通用规范与本项目快捷入口。

### 验证
甲烷 NMR 同任务复刻：simulate→figure(label_peaks)→annotate→delivery 全链真机通过；
回归 offline + pytest 15 + 四 SKILL 一致性 + 视觉基准 22/22 全绿。

## 2.7.0 (2026-09-17)## 2.7.0 (2026-09-17)

**战略评估后的阶段 A/B/C 全量实施**（70 工具 / 34 错误码）。

### 阶段 A：提速（性能分析：80% 感知耗时在模型决策轮次）
- **`origin_figure` 端到端**：一次调用完成 导入/写数 → 画图 →（可选）验证 → 导出 →
  （可选）交付，返回逐步 `steps`、`timings.total_ms` 与汇总 `proof_level`。
  实测 journal 全流程 ~1.2s（原路径需 6-10 次工具调用）。
- `origin_warmup` 预热（295ms，把 Origin 冷启动挪出用户视野）。
- `origin_pages_gc` 页堆积治理（实测 793 页让 `list_pages` 慢到 30.9s、`close` 59.2s）。
- SKILL 主循环新增 **[0] 提速判断**：默认走 `origin_figure` 快路径，仅在细改/精修时进完整流程。

### 阶段 B：入口形态（解决"进不了用户的手"）
- **Origin App（.opx）**：`origin_app/`（App.ini + launch.ogs + bridge_manager.py）+
  `scripts/build_origin_app.py` 一键生成并打印正确的 mkOPX 命令；Apps Gallery 点按钮启停。
  **默认 HTTP transport**（stdio 不适合 detached 常驻——实测拉起即退出）。
- **零安装脚本 Skill**：`skills/origin-scripting/SKILL.md`——AI 生成 Origin Python/LabTalk
  脚本，用户粘进 Script Window 执行；零依赖、任何 agent 可用（竞品空白点）。
- **HTTP transport**：`origin_mcp_http.py`（127.0.0.1:8731，`/mcp` + `/health` +
  Bearer token），多 AI 客户端共享一个 Origin。
- **模板资产**：`origin_template.py` save/list/apply。
  **实测 `.otpu` 保存通道不可用**（`save -i/-t/-it` 静默不落盘、COM 无 SaveTemplate）
  → 降级为 JSON 样式快照 + opju 备份，套用实测 47 项 applied，100% 可用。

### 阶段 C：深度可信
- `origin_capabilities`：从本机 `oPlotIDs.h` 提取真实图型表（**实测 116 条**），
  `origin_capability_diff` 与硬编码表比对，暴露文档/实现脱节。
- `origin_proof`：`proof_level` 三级（verified / readback_only / unverified），
  落实"readback is never proof"，单测 17 项。
- CI：`.github/workflows/ci.yml`（offline + pytest + SKILL 一致性 + proof 单测，多 Python 版本）。
- 视觉基准：12 → **22 张**（新增四种调用方式 × 物化案例）。

### 文档
- 新增 **QUICKSTART.md**（小白版：4 种调用方式决策表 + 第一次出图 + 常见问题）。
- 主 README 仅加一行 QUICKSTART 超链接；README.en.md 重写（70 工具能力清单 + 6 种安装路径）。

### 修复
- `bridge_manager`：默认 transport stdio → http；`do_status` 改用**端口探测**
  （PID 探测对 detached 子进程会误判为已退出，实测进程在跑却报未运行）。

### 验证
四种调用方式 × 4 个大学物理化学案例（理想气体等温线族 / 朗伯-比尔标准曲线 /
酸碱滴定曲线 / 阿伦尼乌斯图）全部通过：A App 形态 10/10、B 零安装脚本 4/4、
C MCP 客户端 4/4（70 工具）、D npm 包内 1/1。
回归：offline + pytest 15 + proof 17 + selftest + fine-edit + repro + chem v2 15/15
+ 视觉基准 22/22 + SKILL 一致性（三 SKILL 零幽灵）。

## 2.6.2 (2026-09-16)

**npm 安装契约修复**（桌面市场可安装三道闸门）：

- `@deepseek-ai/dsh-mcp-client` 从精确 pin `0.1.0-rc.7` 放宽为 `>=0.1.0-rc.1`
  ——dshfind 的 desktopPreviewVerdict 七项复核第 6 条要求 `@deepseek-ai/dsh*`
  range 覆盖桌面端运行时（0.1.1-rc.2），精确 pin 判 `runtime-range`，
  导致市场只展示不进可安装列表。
- 其余六项复核逐条核对通过（name/version、无 deprecated、无 lifecycle 脚本、
  repository https 回链、dsh.bundle.patch 安全相对路径、dist 官方源+sha512）。
- package.json 描述 50→62 工具。
## 2.6.1 (2026-09-16)

**npm 包修复版**：2.6.0 的 `files` 白名单遗漏 v2.5.0/v2.6.0 新增模块
（origin_bridge/origin_gallery/origin_spec/origin_matrix——stdio 模式调用
矩阵/spec/桥接/PPT/Gallery 工具会 ImportError）。2.6.1 补全白名单并新增
tests/、pyproject.toml、README.en.md 入包（npm pack 清单已逐项复核）。
代码与 2.6.0 完全一致，仅打包范围修复；**请直接安装 2.6.1**。

## 2.6.0 (2026-09-16)

**P2 生态批次**（ROADMAP #P2 八项全落地；62 工具 / 30 错误码）。

### 分发与测试
- **pyproject.toml**：`pip install .` 本地构建可用（PyPI publish 待指令）；
  requirements 对齐（新增 pyyaml/Pillow/matplotlib/python-pptx）；
- **pytest 化**（来源 leima-max）：`tests/test_offline.py`（错误码表/门禁
  tokenizer/白名单/FigureSpec 闭环/PLAN_STALE/目录一致性，无 Origin 可跑）
  + `tests/test_live.py`（Origin-aware skip，无 Origin64.exe 自动跳过）；
- **双语 README**：README.en.md 英文精简版（中文为准）。

### 功能
- **EPS 导出**：fmt 白名单 + `%!PS` 文件头校验（探针实证 21921B 产物）；
- **matplotlib 桥**（来源 jsbangsund）：`origin_import_matplotlib(pickle)`
  反序列化 Figure → 逐 Line2D 提取数据/颜色/线宽/符号 → 建图；
  X 并集线性插值对齐；实测颜色/符号映射生效、verify passed；
- **PPT 组图交付**（来源 hzsci）：`origin_export_pptx` 高清 PNG 嵌入
  python-pptx 页 + 面板字母 + 来源注记；真 OLE 双击编辑路径未打通
  （Origin50.Graph OLE 类已确认存在，COMPATIBILITY #16 如实记录）；
- **Gallery 模板搜索**：`origin_template_search` Graph Gallery 搜索/
  .zip 下载（浏览器 UA；结构变化/403 如实报告不猜）。

### 安全
- **文件访问白名单**（来源 Ge-Shun）：`DSH_ORIGIN_ALLOWED_ROOTS`（分号
  分隔前缀）约束 load/export/save/delivery 路径，越界返回 `path_not_allowed`。

### 修复
- **PLAN_STALE 时序缺陷**：`_newer_same_signature` 依赖 LRU 顺序，
  get_plan 的 touch 会重排顺序漏报陈旧计划 → 改用创建序号 `_seq` 比较
  （pytest 实测暴露并修复）。

## 2.5.0 (2026-09-16)

**P1 协议与能力批次**（ROADMAP #P1 五项；59 工具）。

### FigureSpec 声明式图协议（来源 deliuou + editaplot）
- `origin_spec_export`：把 plan 导出为四节 YAML spec（data 内联完整数据/
  plot/style/export）——可 diff、可重放、可版本化；同 spec 重导入得到同一
  plan_id（内容哈希闭环实测）；
- `origin_spec_import`：读 YAML spec 重建计划，复用现有 questions 确认流与
  check_stale 防陈旧机制，不发明第二套确认流；
- "spec 先行 → 用户确认 → 再执行"写入 SKILL。

### MCP Resources 只读会话快照（来源 garethbeaumo）
- stdio 服务器新增 `resources/list` + `resources/read`（capabilities 已声明）：
  `origin://session`（全项目快照）/ `origin://worksheets` / `origin://graphs` /
  `origin://worksheet/[Book]Sheet`——AI 不改项目即可 inspect；工具面不受影响。

### 矩阵工具集（来源 youngminsw + 官方 Python-Samples）
- `origin_matrix_write`（from_np 自动 resize + 写回一致性复核）/
  `origin_matrix_read`（2D 列表）/ `origin_matrix_plot`（surface/scatter/
  contour/contour_fill/3d_wire，读矩阵后复用现有已验证绘图路径）；
- 探针实证：heatmap 的 LabTalk `plotm` 全变体静默失败 → 明确拒绝
  （COMPATIBILITY #14），不冒充支持。

### 参考图安全借鉴（来源 editaplot + hzsci）
- SKILL 新增 4A+ 节：用户给参考图时产出"图形简报"（只提取布局/家族/线型/
  配色/图例等图形语法），映射到 family/style_overrides/edit_legend；
- **硬性红线**：禁止像素反推数据、不复制文字/标注/Logo、不承诺 1:1 复刻。

### 验证图视觉基准（来源 editaplot）
- 新增 `smoke/visual_diff.py`：12 张代表性产物基准（sha256 + dHash 感知哈希），
  `--capture` 入库 / `--check` 对比，汉明距离 >14/64 报差异——堵住数值断言
  漏掉的外观回归（图例换行类问题的自动化防线）。

### 修复
- **COM 线程死锁**：`origin_matrix_plot` 在 COM 线程内误调 `@_synchronized`
  公开函数（二次投递队列自等待）→ 改调裸 impl；看门狗 105s 超时机制首次
  实战捕获该死锁并给出明确错误；
- MBook.lname 为空（originpro 缺陷）→ 引用改用 `obj.GetName()`。

## 2.4.0 (2026-09-16)

**P0 稳健性批次**（ROADMAP #P0 五项；54 工具 / 29 错误码）。

### 看门狗与连接管理
- **模态对话框看门狗**（来源 youngminsw）：COM 调用软超时（默认 90s，
  `DSH_ORIGIN_DISPATCH_TIMEOUT`）后自动枚举 Origin 的模态对话框并点击
  白名单按钮（OK/确定/取消类，EnumWindows+BM_CLICK）；解除则正常返回并附
  `watchdog_dismissed`；仍未解除按 autokill 策略处置，返回
  `com_blocked_by_dialog`（含对话框标题）。`DSH_ORIGIN_WATCHDOG_GRACE`
  控制点击后等待（默认 15s）。
- **origin_release / origin_reconnect**（来源 garethbeaumo）：释放自动化连接
  但保持 Origin 打开（用户可立即手动操作）；下次任意工具调用自动重连。
  实测约束：release 不停 COM 线程（线程绑定 CoInitialize 状态，停线程
  重连会原生崩溃）。

### 安全与拟合
- **LabTalk 破坏命令门禁**（来源 youngminsw）：`delete / exit / quit / kill /
  purge / doc -s / win -c` 默认拦截（`labtalk_blocked`），tokenizer 分词、
  字符串字面量豁免；`confirm=true` 显式放行。
- **origin_fit 收敛控制**（来源 garethbeaumo；探针实证 originpro API）：
  `initial_params`（NLFit set_param）、`fixed_params`（NLFit fix_param /
  linear fix_slope·fix_intercept）、`weight_col`（NLFit set_data yerr 通道；
  linear 加权明确拒绝）。固定参数误差（e_*=0）显式回传作"确实没动"证据。

### 细粒度操作
- **origin_manage_plots**：remove 删曲线（`gl.remove_plot`，探针实证）/
  change_data 换数据源（`pl.change_data(wks, x=, y=)`）。
- **origin_manage_data**：sort 按列排序整表（`wks.sort(col, dec)`）/
  transpose 行列转置（Python 侧转置写新表；>1000 行保护性拒绝）。

### 修复
- fine_edit 测试的 list_pages 断言解除环境耦合（closeAll 清场后 0 页属正常）。

## 2.3.0 (2026-09-16)

**健壮性 + 解耦接入 + 文档体系大版本**（50 工具 / 27 错误码），另修复 1 项
本轮引入的 multi_panel 回归。

### 引擎健壮性
- **autokill 连接前置清理**：连接 COM 前 taskkill 残留 Origin 进程并等待
  （默认策略：`ORIGIN_SESSION=isolated` 时开、attach 模式默认关以保护用户
  手动会话；`DSH_ORIGIN_AUTOKILL=1/0` 显式覆盖）；
- **导出三级回退链**：`save_fig` → COM ImageExport → LabTalk `expGraph`，
  每级以文件存在性 + 魔数（PNG/PDF 头）裁决，返回 `attempts` 归因明细
  （LabTalk 静默失败特性下，文件校验是最终裁决）；
- **禁自动保存**：`origin_save_project` 默认拒绝（`DSH_ORIGIN_NO_AUTO_SAVE=0`
  可开），返回 `manual_save_required` 错误码并提示用户 Ctrl+S；
  `export_delivery` 遇此码优雅降级（其余产物照常交付）；
- **错误码 → 恢复动作映射**：`RECOVERY_MAP` 全表 27 码，五类 policy
  （fix_args_then_retry / retry_same_args / restart_origin_then_retry /
  replan_then_retry / no_retry），每个失败返回自动内嵌 `recovery` 字段，
  `origin_error_codes` 可单独查询。

### 新能力（工具 43 → 50）
- `origin_diagnose`：7 检查项（安装/进程/autokill/COM 注册/引擎连接/
  环境策略/导出目录）+ 建议，失败排障第一入口；
- `origin_cookbook`：8 类常见场景（浓度/动力学/滴定/光谱/电化学/XRD/
  相图/发表级）配方 + 默认参数 + 路径约定，前缀匹配；
- **trace_id + duration_ms**：每个工具调用返回可追溯 ID 与耗时；
- **推荐默认参数**：origin_cookbook 内置 9 项语义规范默认值；
- 新模板 `cycle_overlay`（多圈叠放渐变色 + legend_mode）与
  `eis_nyquist`（等轴比 Nyquist）；
- 6 个自研统计工具返回附加 `confidence_note`（明确 "这是引擎自算" 边界）。

### 计划与统计
- `origin_plot_plan` 计划哈希纳入列角色（x_column/y_columns/yerr_pick）；
  `origin_execute_plan` 新增 `expect_hash` 与 `force`，数据变更后执行
  返回 `plan_stale` + replan 策略（三重校验：完整性/expect_hash/
  同签名更新计划）。

### 解耦与接入（脱离 DSH 也能跑）
- `origin_engine.py` 零 MCP/DSH SDK 依赖（纯 originpro + 标准库）；
- 新增 `origin_mcp_stdio.py` 独立 stdio 入口（`--print-config` 输出
  7 客户端配置片段、`--doctor` 一键体检）；
- 新增 `install.py`：一键注册/卸载到 Claude Desktop / Cursor / VSCode /
  Cline / Continue / DSH / Kimi Code（自动检测、备份、合并写）；
- `package.json` 提供真实 `bin`（`npx dsh-origin-mcp`），自动挑 Python
  并预检依赖；打包 files 修正遗漏的 origin_edit.py；
- DSH 插件 `index.js` 卸载清理走 `ctx.effect` + engine 优雅停机
  （排空队列 + 毒丸 + join，COM 线程死后自愈重启）。

### 文档体系
- 主 SKILL（origin-plotting）v2.3.0：宿主注入规范节、快速/正式双路径、
  强制确认流三情形 + plan_hash、错误表扩至 16 行、环境变量附录；
- 新增 `skills/origin-stats`：11 个统计工具速查 + 科学边界铁律（从主
  SKILL 拆出，主流程不再被统计细节稀释）；
- 新增 `COMPATIBILITY.md`：接入方式矩阵、兼容性、**11 条实测失败场景**
  （渐变+同任务导出毒化、log10 无效点、missing 值哨兵等）；
- README 首屏重定位（国内可用 · DSH 生态 · MCP 桥接），安装方式 A–D。

### 修复
- **multi_panel 校验孤儿代码**：`_validate_template_data` 中 forest 分支
  尾部残留错挂在 multi_panel 分支下，任何 multi_panel 调用必触发
  `UnboundLocalError`（selftest best-effort=False 与 c23 案例根因，
  冒烟中发现并修复，selftest 复测 True）。

## 2.2.2 (2026-09-16)

**26 个化学场景实测（大学基础 → Nature 级）暴露的 20 项缺点全量修复**，
另修复 1 项 P0 级"verify 假绿"。

### P0 —— 输出错误 / 假成功
- **multi_panel 三面板图层完全重叠**：add_layer 默认同位叠放 → 现按层纵向
  均分 %页几何（层间 3% 间隙），y 标题逐层写入，除末层面板外隐藏 X 刻度；
- **verify_graph 假绿**：新增**层间 bbox 重叠检测**（部分重叠 2%~90% = fail；
  完全重叠 = warn，双 Y 类共享绘图区布局可传 `allow_full_overlap=true` 放行）；
- **dual_y 右轴标题被 LabTalk 占位符机制吞掉**（"库仑效率 (%)" → "% (1.2)"、
  "mAh/g" → "mah/g"）：新增 `set_axis_title_checked` 写后读回验证器
  （COM → LabTalk 转义 → 对偶轴 y2 三级通道），dual_y/stacked/xrd/multi_panel
  全部接入；`_apply_style_impl` 新增 `apply_axis_titles` 开关，模板分支不再被
  推断标题二次覆盖；
- **forest 模板**：labels 逐行标注（此前只显示第一个）；CI 线/零参考线改用
  `draw -l` 绘制，不再泄漏进图例。

### P1 —— 工作流断裂 / 高摩擦
- `transform` 新增 **ln / log10 / reciprocal / exp / sqrt / abs** 算子（动力学
  ln[A]、Arrhenius 1/T、二级 1/[A] 不再需要 AI 自算回写）；结果 ≤2000 点直接
  回传 `values` 数组，可与 plot_template 内存数据接口直通（TGA/DTG 双 Y 链路
  从四段拼接降为两步）；对 0/负数取对数等无效点在 `invalid_points` 提示；
- `plot3d` surface 显式网格**形状自适应**（行=x 与行=y 两种写法都接受，自动
  转置），形状不匹配时返回友好报错（此前是 numpy 底层 "inhomogeneous" 天书）；
- 多系列 y 轴标题不再自动硬编（k_Pt_C → "K pt" 的失真根因：主干互不相同时
  返回空标题，交由 x_title/y_title 或 edit_axis 显式指定）；`capitalize()`
  改为仅首字符大写（"mAh/g" 不再被压成 "mah/g"）；
- **xrd_pattern**：相刻线改红色 + 1.5pt 加粗（不再与 Difference 同色同宽混为
  噪声）；Observed 密集散点缩小（symbol_size 3），Calculated 线不再被淹没；
  上层残留 x 标题对象清空（"two_theta" 悬浮字修复）。

### P2 —— 功能补齐 / 文档
- `fit` 返回 `supported_kinds` 模型名清单（此前是暗知识；MichaelisMenten/
  Boltzmann/DoseResp 等 Origin 内置 NLFit 名直通）；默认自动关闭 NLFit 的
  FitLine*/Residual* 报告副产品页（实测 7 次 fit 多开 14 页的根治）；
- `integrate` 新增 `baseline`（"min"/"first"/数值，DSC 焓变扣基线）；
- `histogram` 新增 `color`（默认接入调色板首色，不再纯黑）；
- `manage_pages` 新增 `closeAll`（会话产物一键清理）；
- 模板支持 `x_title`/`y_title` 覆盖；`stacked_spectra` 支持 `gradient=true`
  系列渐变色；
- SKILL.md：plot3d data 格式、NLFit 模型名、fit 副作用、list_pages 返回结构、
  transform 新算子、重叠检查语义全部写入。

## 2.2.1 (2026-09-16)

**skill 重构：工具参考手册 → 决策流 SOP**（代码零改动，工具数不变仍为 43）。
"AI 不怕工具多，怕没有策略"——工具越多搜索空间越大，越需要工作流约束。

- 以端到端工作流为骨架：主循环决策图 + 七步（检查环境 → inspect → 规划 →
  执行（4A 粗粒度建图 / 4B 细粒度编辑）→ verify 校验 → 失败恢复 → 交付），
  43 个工具全部挂载到对应决策点，AI 按序调用而非在工具目录里搜索；
- 完成判据显式化：verify 只有 fail 必须修；同一问题 2 轮修复仍 fail 即停止
  重试、如实报告并渲染图给用户决策（防死循环式"假成功"）；
- 失败恢复表：error_code / NaN 读回 / 窗口激活失败 → 唯一确定动作；
- 细粒度编辑五态 status（applied / applied_adjusted / applied_unverified /
  rejected / unsupported）逐态判读表；通道纪律降级为排障附录；
- 一致性校验：SKILL 提及的工具名与 TOOL_CATALOG 逐一比对，零幽灵零遗漏
  （修复 v2.2 文档漏挂的 `origin_list_graphs`）；同步修正 package.json
  description 中过期的 "35 tools" 表述。

## 2.2.0 (2026-09-15)

工具 35 → **43 个**。新增「细粒度编辑」层（让 AI 能像人一样微调已有图），并修复
三条实测缺陷。全部改动建立在**真机探针**之上（smoke/labtalk_probe2..8），
结论已数据化进代码与能力矩阵。

### 新增：细粒度编辑（8 工具，`origin_edit.py`）

| 工具 | 用途 |
|---|---|
| `origin_list_pages` | 列出全部页面（图页/工作簿/矩阵）+ 当前活动窗口 |
| `origin_inspect_graph` | 巡检现状：图层几何/逐条曲线样式/轴设置/图例/页面尺寸（cm+dots+dpi） |
| `origin_edit_plot` | 逐条微调：颜色/线宽/线型/符号形状大小填充/透明度/显示隐藏 |
| `origin_edit_axis` | 轴标题/起止范围/刻度类型/网格/刻度长度/标签字号加粗小数位 |
| `origin_edit_legend` | 显示隐藏/字号/边框/背景/四角锚点定位/自定义文本/恢复自动图例 |
| `origin_edit_page` | 纸张尺寸（cm）、页面背景、图层位置与大小（%页） |
| `origin_manage_pages` | 关闭/激活/重命名/隐藏/显示/复制页面（含工作簿；"帮我关掉几个窗口"） |
| `origin_add_text` | 添加文本标注（峰位/条件说明） |

设计要点（均由探针证据支撑）：
- **COM 优先**：originpro 图层/曲线作用域读写（`plot_list`/`gl.get_int`/`set_int`/
  `axis` 属性/`p.color`/`p.set_int('show')`）与活动窗口**无关**，任何时候可靠；
- **LabTalk 先复核激活**：`ensure_active_graph()` 用 COM `activate()` + `is_active()`/
  `page.name$` 复核，不符即返回 `window_activation_failed`，绝不"以为激活成功"；
- **写入必读回**：每项改动返回 `{item, requested, status, readback}`，
  status ∈ applied / applied_unverified / applied_adjusted / rejected / unsupported；
  **NaN 读回一律判未生效**（NaN 比较恒为 False 会假成功——已修）；
- **单位显式**：页面 dots↔cm（600dpi 实测）、图层 `layer.unit`（1=%页/3=cm/5=pixel）、
  图例锚点用 dots 计算（`page.width/height` + `legend.width/height`），
  避开 LabTalk `legend.x/y` 的混合单位坑。

### 修复：三条实测缺陷（均带回归测试 `smoke/repro_defects.py --expect-fixed`）

1. **verify 误判曲线数为 0（会主动骗人）**：原实现只统计 `gp[0]` 一层，多层图
   （如分层 XRD）会数成 0 并报 `fail 需修复后复核`。现**跨全部图层统计**并给出
   逐层明细；同时 `layer_count`/`bounds`/字号改为 COM 作用域通道，LabTalk 类检查
   只在激活复核通过后使用，读不回来一律 `unreadable`，**只有"上下文可信且与期望
   不符"才判 fail**（新增 `unreliable` 状态与 `context` 块）。
2. **LabTalk 静默失靶**：`xb.*`/`legend.*`/`layer.*` 在活动窗口不是图页时静默返回
   NaN 或落到别的窗口（实测：`layer.left` 读到工作簿的几何）。现新增
   `_ensure_active_graph()` 与 `_lt_write_checked()`（激活复核 + 写入读回 + NaN 守卫），
   错误码 `window_activation_failed`；轴标题一律改走 COM `gl.axis().title`
   （探针证实 LabTalk `xb.text$` 在本机即使激活也不生效）。
3. **xrd_pattern 量程压缩（Rietveld 三件套不可用）**：原实现把差谱向下偏移后与主谱
   共用一条 Y 轴，主峰仅占量程 ~62% 且下部 25% 为空。现改为**双层布局**：上层
   Observed 散点 + Calculated 线（满量程，实测主峰占 76.9%），下层 Difference
   独立量程 + 零参考线 + 相刻线，两层 X 轴严格对齐（实测 7.9–82.1 完全一致），
   上层隐藏 X 轴刻度只留最下层；`add_layer` 不可用时降级单层并在返回中标注。

### 其他
- `origin_status.capabilities` 新增 `fine_edit` 与 `channel_policy`（COM/LabTalk 通道策略）；
- 新增错误码 `window_activation_failed` / `layer_not_found`；
- 新增探针脚本 7 个（probe2..8）+ 冒烟 `smoke/fine_edit_test.py`（25 项断言）+
  缺陷回归 `smoke/repro_defects.py`；
- 回归：offline-test / selftest / Node SDK 握手（43 工具）全绿；细粒度编辑冒烟
  含"换色读回""加粗前后 PNG 差异""关窗复核""跨层计数"等端到端断言。

## 2.1.0 (2026-09-15)

工具 28 → **35 个**。补齐「可信交付」短板：文件导入、可编辑 OPJU 交付、
确定性反读验证、绘图计划确认流、领域模板、版本能力握手（真机 Origin 2026 +
venv Python 3.13 全量自测通过：offline-test / selftest / Node SDK 握手三绿）。

### P0 · 文件与交付
- **`origin_load_file`**：导入 CSV/TXT/TSV/DAT/XLSX/XLSM/XLS —— 中文路径/列名
  安全，编码自动探测（utf-8-sig/gbk/utf-16），分隔符自动嗅探（含空白分隔），
  表头自动识别，逐列类型标注 numeric/text/mixed + 预览（新增 `origin_fileio.py`，
  XLSX 走 openpyxl，requirements 已加）；
- **`origin_save_project`**：保存当前项目为可编辑 OPJU（`op.save` + ASCII 临时
  搬运 + LabTalk 三重兜底）；
- **`origin_export_delivery`**：一键交付 —— 源文件同级建 `<数据名>_Origin_<时间戳>/`
  收纳多格式图片 + OPJU，逐文件核验完整性；
- **`origin_verify_graph`**（新增 `origin_verify.py`）：确定性反读 —— 图层/曲线数、
  轴标题文本与字号（xb/yl.fsize）、图层几何、图例状态、交付文件完整性，
  逐项 pass/fail/warn/unreadable，与 `origin_view_graph` 组成"程序核+模型看"双保险；
- `origin_export` 格式扩展：png/svg 之外支持 **pdf/tif/emf**；
- SKILL.md 新增科学边界条款：不虚构/不补数据、不确定列先问、派生列标注
  derived、不静默拟合/平滑/归一化。

### P1 · 模板与计划流
- **`origin_plot_template`**（5 条领域模板，全部由真机验证过的原语组合）：
  `stacked_spectra`（多谱线纵向堆叠偏移，offset/reverse_x）、`xrd_pattern`
  （Observed 散点+Calculated 线+Difference 下移+phases 相刻线）、`dual_y`
  （双 Y 轴，模板名按 dualy/doubley/righty 探测——2026 实测 DUALY 不存在）、
  `forest`（效应量+CI+零参考线，NaN 断线法）、`multi_panel`（多面板纵向堆叠）；
- **`origin_plot_plan` / `origin_execute_plan`**（新增 `origin_plan.py`）：离线
  秒回的绘图计划 —— 逐列画像（dtype/缺失/单调性）、角色建议（X/Y/误差棒/标签）、
  元素清单（排版+调色板+使用约束+轴标题）、待确认问题（混合列/高缺失/多个X
  候选/无单调X）；plan_id 内容哈希 + 服务端 LRU 缓存（32），执行零回传；
- **版本能力握手**：`origin_status` 新增 `capabilities` —— Origin 版本标签
  （LabTalk @V）、**已知坑矩阵 known_risks**（plotxy 204/215 @2026b、多实例
  COM 冲突、线程亲和性、模态对话框挂起、双Y模板名差异——全部真机探针结论
  数据化）、features 特性表。

### P2 · 治理与体验
- **隔离会话守卫**：`ORIGIN_SESSION=isolated` 时检测到已运行 Origin 即返回
  `origin_busy_user_session`（不劫持用户窗口），默认 attach 不变；
- **`style_overrides` 显式样式**：series_colors/line_width_pt/x_title/y_title
  逐项回报 applied/kept_default/rejected，未验证字段（legend/页面尺寸）明确拒绝
  不静默忽略；
- **调色板使用约束**：每套 family 标注 suitable/avoid（如 duo_warm 红蓝有方向
  含义勿用于无序分类），`origin_status`/计划流可查；
- **CI**：GitHub Actions（windows-latest，Py3.10/3.12）跑 `--offline-test`
  （无 Origin 依赖）+ compileall；新增 CONTRIBUTING.md / SECURITY.md；
- **`--offline-test`**：注册表一致性/错误码/计划流/模板前置校验/文件 IO/能力
  矩阵，CI 可跑；smoke/mcp_handshake_test.mjs 重写为机器无关自动探测，
  校验 35 工具 + capabilities + 计划流。

### 修复
- `_execute_plan_impl` 缺失 `import json`；
- OPJU 保存：originpro 1.1.15 无 `save_project`，改用 `op.save` + 双兜底；
- mcp-handshake 测试脚本硬编码旧机器路径。

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

排版与统计大版本（工具 16 → 28 个）。按"调色板/轴标题语义化/按图型排版"的
设计方向独立实现（clean-room，未复制任何第三方代码/文档），新增自研
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
