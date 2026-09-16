---
name: origin-plotting
description: Origin 全流程 SOP（v2.3.0，50 工具按步骤挂载）：检查环境（失败先 diagnose）→ inspect 现状 → 规划（语义不明/发表级/多组对比强制 plan→确认→execute）→ 执行 → verify 校验（只有 fail 必须修）→ 失败恢复 → 交付。含快速/正式双路径、场景速查（origin_cookbook）、错误码→恢复动作映射、领域模板、细粒度改图、通道纪律。统计推断细节见 origin-stats skill。
whenToUse: 用户要求用 Origin 画图、导入数据、拟合/FFT/统计分析、领域模板（谱图堆叠/XRD/双Y/森林/多面板）、导出交付 OPJU，或对已画好的图做微调（换某条线颜色/加粗/隐藏、改轴范围、挪图例、调页面尺寸、关掉多余窗口）时
---

# Origin SOP v2.3.0（决策流驱动；工具挂在步骤下，按序调用）

## 宿主注入的默认规范（systemPrompt 已带；没有时你按此执行）
- 折线/散点默认 `plot_type="line_symbol"`；未指定导出参数时 `fmt="png"`, `width=1200`。
- 投稿/发表图默认 `style_mode="journal"`（单栏 89mm / 双栏 183mm），导出 `width>=1800`。
- 轴标题必须语义化：物理量 + 单位（`temperature_C` → "Temperature (°C)"），拒绝 A/B/C 占位名。
- 多组对比 / 列语义不明 / 发表级图：**强制** `origin_plot_plan` → 用户确认 → `origin_execute_plan`（见 STEP 3）。

## 快速路径 vs 正式路径（先选路，再动手）
- **快速路径**（数据语义明确、临时查看）：`origin_plot_file` / `origin_load_file`+`origin_plot`，一次调用出图。
- **正式路径**（发表级 / 多组对比 / 列语义不明 / 用户没说清）：`origin_plot_plan` → 向用户确认 questions → `origin_execute_plan` → `origin_verify_graph` → `origin_export_delivery`。
- 想不起调用链 → `origin_cookbook`（离线秒回：quick_plot/from_file/journal/multi_compare/edit/fit/recover/deliver 八场景 + 高频工具推荐默认参数）。

## 主循环（先看这张图，再往下读）

```
[1] origin_status 检查环境
     │ 失败 → origin_diagnose 定位（安装/COM/残留进程/目录权限）→ 按 recovery 修复
     │ 读用户意图，四选一分流：
     ├─ 画新图 ────────────────→ [3] 规划（歧义/发表级/多组对比强制 plan 确认流）
     │                              └→ [4A] 粗粒度建图 → [5]
     ├─ 改已画好的图 ─→ [2] inspect_graph 拿现状 → [4B] 细粒度编辑 → [5]
     ├─ 关窗/整理/激活 ─→ origin_manage_pages 一步完成 → 直接结束
     └─ 只要数字不要图 ─→ 附录B 分析工具 / origin-stats skill，结果直接回复用户
[5] origin_verify_graph ── pass/warn → [7]
     └── fail → [6] 按错误码修复 → 回 [5]（同一问题 2 轮仍 fail：停止重试，如实报告）
[7] 交付：export_delivery / export / save_project → 回复用户路径 + verify 结论
```

## STEP 0 铁律（每条都对应实测过的返工，违反必出错）
1. **写后必读返回**：所有编辑工具逐项返回 status，必须逐项看（判读见 4B）。
2. **改图前必 inspect**：曲线索引/图层号/页面短名以 `origin_inspect_graph`/`origin_list_pages` 读到的为准，不要猜。
3. **不虚构数据**：列用途有歧义 → 走 [3] 的 plan 确认流，questions 非空必须先问用户；不补造缺失值。
4. **引用原样传递**：`origin_write_data`/`origin_load_file` 返回 `worksheet`，`origin_plot*` 返回 `graph`；后续调用直接传返回值。
5. **细粒度修改只用 `origin_edit_*` / `origin_manage_pages`**，不要绕道自拼 LabTalk（原因见附录 C——裸 LabTalk 会在非活动窗口上静默失败）。
6. 正式交付前必过 `origin_verify_graph`；`fail` 项修完才允许交付。
7. **同签名重新 plan 过就别执行旧计划**：`origin_execute_plan` 会校验（`plan_stale`）；确实要执行旧计划传 `force=true`。
8. 每次调用返回都带 `trace_id` + `duration_ms`：向用户报告故障时附上，便于定位失败阶段。

## STEP 1 检查环境（每个会话首次 Origin 操作前）
调 `origin_status`：
- `ok:true` → 记下 `capabilities.fine_edit`（true=细粒度编辑可用）、`known_risks`（本机版本坑）→ 进分流。
- `ok:false` → 读 `error_code + recovery.policy + next_actions` 照做：
  - `connection_error` / 连不上：调 `origin_diagnose`（免连 Origin 的系统自检：Origin 安装、COM 注册、残留进程、导出目录权限、环境策略）。报"残留 N 个进程"→ `taskkill /F /IM Origin64.exe` 等 2 秒重连（isolated 会话已默认自动清理，见附录 C 环境变量表）；
  - `origin_busy_user_session`：用户正在手动操作 Origin → 告知用户，等 5 秒重试，最多 3 次，仍失败则如实报告；
  - 其余 → 按 `next_actions` 执行，不要盲目重试。

## STEP 2 inspect（"改已有图"类任务的入口，跳过=盲改）
用户说"这条线/这个轴/图例/页面"时，先拿现状：
- `origin_inspect_graph(graph)` → 每图层几何、每条曲线（索引/颜色/线型/符号）、轴范围与字号、图例、页面尺寸（cm+dots+dpi）；
  用户说"第 2 条线" → `plot` 索引 = **1**（0 起）；颜色先用 inspect 确认，避免改错条。
- `origin_list_pages()` → 全部页面（**对象数组，含 name/type**，取 `name` 字段当短名）+ 当前活动窗口（关窗/激活/重命名都用这里的短名）；
  用户只说"刚才那张图"没给名 → `origin_list_graphs()` 列图页短名确认是哪张。
- 不确定图长什么样 → `origin_view_graph(graph)` 自己看图再决定改什么；确认流中也用它把图带到聊天里给用户看。

## STEP 3 规划（新建流；编辑流直接跳 4B）
- 数据进 Origin：
  - 本地文件 → `origin_load_file(path='D:/data/样品.csv')`（CSV/TXT/XLSX/XLS，中文路径安全）→ 返回 `worksheet`；
  - 内存数据 → `origin_write_data(columns)`：`{"列名":[...],...}` **第一列自动 X 其余 Y**，也收二维/一维列表。
- **强制走确认流的三种情形**（用户硬性要求）：①列语义不明（>2 列、不确定谁是 X/yerr/labels）②发表级图 ③多组数据对比：
  `origin_plot_plan(columns, ...)` 离线秒回逐列画像 + 角色建议 + **questions** + `plan_hash`；
  questions 非空 → **先问用户**（可用 `origin_view_graph` 在执行后把图给用户看）→ `origin_execute_plan(plan_id, expect_hash=plan_hash)`（写数+画图+导出一条龙）→ 跳 STEP 5。
  数据/列映射在 plan 之后变过 → execute 返回 `plan_stale`（含 newer_plan_id）→ 改用新计划重跑。
- 角色明确且非上述三情形 → 直接 4A。

## STEP 4A 粗粒度建图（新建流）
按图型选工具（都返回 `graph`）：

| 图型/需求 | 调用 |
|---|---|
| 写数+画图+导出一步到位（最常用） | `origin_plot_file(...)` |
| 已有工作表，画 2D | `origin_plot(worksheet, ...)`，含 line/symbol/line_symbol/box/bar |
| 领域图（谱图堆叠/XRD/双Y/森林/多面板） | `origin_plot_template(template_id, data, ...)`，模板见附录 A |
| 3D 表面/散点、等高线、直方图 | `origin_plot3d` / `origin_plot_contour` / `origin_histogram` |

`origin_plot3d` 的 data 格式（两种 surface 写法都自动识别行列方向）：
- 隐式网格 `{"z": [[...],[...]]}`（自动生成 X/Y 索引）——最稳；
- 显式网格 `{"x":[...], "y":[...], "z":[[...],...]}`：z 的行对应 y、列对应 x；
  AI 直觉的"行=x"写法也会自动转置，形状不匹配时返回友好报错（含期望形状）；
- 3D 散点 `{"x":[...],"y":[...],"z":[...]}` 三等长一维列表。

最快路径示例（记住这个参数组合，可直接抄）：
```json
{"columns": {"temperature_C":[20,25,30,35], "pressure_kPa":[95,101,112,118]},
 "plot_type": "line_symbol", "fmt": "png", "width": 1200,
 "style_mode": "journal", "family": "ocean", "title": "示例"}
```
- `style_mode`: default | journal（单栏 89mm/双栏 183mm）| presentation；
- `family`: 调色板（用法约束 `origin_status` 可查）；显式覆盖用
  `style_overrides={"series_colors":["#RRGGBB",..], "line_width_pt":2.0, "x_title":"..", "y_title":".."}`；
- 同名 `graph_name` 重复调用**清旧重画**（幂等，改参数直接重调）；
- 轴标题从列名自动推断（`temperature_C` → "Temperature (°C)"）——**仍要人工过目是否符合语义**。

## STEP 4B 细粒度编辑（编辑流；每次改完看逐项 status）
意图 → 工具映射（按新手高频排序）：

| 想做的事 | 调用 |
|---|---|
| 只把第 2 条线换红色 | `origin_edit_plot(graph, [{"plot":1, "color":"#D55E00"}])` |
| 第 1 条线加粗 2.5pt | `origin_edit_plot(graph, [{"plot":0, "line_width_pt":2.5}])` |
| 虚线/换符号/半透明 | `{"line_style":1}` / `{"symbol_kind":2,"symbol_size":9}` / `{"transparency":30}` |
| 隐藏一条曲线（不删数据） | `{"plot":2, "visible":false}` |
| 改轴标题/范围/网格/字号 | `origin_edit_axis(graph, axis="y", title="Pressure (kPa)", from_value=80, to_value=140, props={"show_grids":1, "label_font_pt":12, "label_bold":1})`（`axis`: x/y/x2/y2，`layer` 默认 0，`scale`: 2=log10） |
| 图例挪四角/隐藏/字号 | `origin_edit_legend(graph, {"position":"tr"})` / `{"visible":false}` / `{"font_size_pt":12}`（position: tl/tr/bl/br 最省心；`left/top` 为物理坐标整数） |
| 投稿单栏页 8.9cm | `origin_edit_page(graph, page_size_cm={"width":8.9,"height":6.5})` |
| 调图层位置大小（%页） | `origin_edit_page(graph, layer=0, layer_geometry_pct={"left":14,"top":8,"width":80,"height":60})` |
| 关掉多余窗口 | `origin_manage_pages("close", pages=["Book3","Book4"])`（短名来自 `origin_list_pages`） |
| 删除图内某条曲线 | `origin_manage_plots(graph, "remove", plot_index=2)`（索引从 0 起；区别于 visible:false 隐藏） |
| 曲线换数据源（不改样式） | `origin_manage_plots(graph, "change_data", plot_index=0, data_worksheet="[B2]Sheet1", x_col="x2", y_col="y2")` |
| 加峰位/条件标注 | `origin_add_text(graph, "peak @ 30 C", x=30, y=118)` |
| 画辅助线（Tafel 外推/阈值） | `origin_add_line(graph, kind="vertical", x=..)` |
| 整图换排版/调色板 | `origin_apply_style(graph, style_mode="journal", family="...")` |

每次调用后逐项判读 `status`：

| status | 含义 | 动作 |
|---|---|---|
| `applied` | 写入且读回一致 | 过 |
| `applied_adjusted` | Origin 钳制了值，`readback` 是实际落点 | 可接受；不满足就换参数重调 |
| `applied_unverified` | 写入成功但无读回（如线宽） | `origin_view_graph` 目视确认 |
| `rejected` | 参数被拒 | 读 `message` 改参数，重试 1 次；再拒就换方案并告知用户 |
| `unsupported` | 该版本不支持 | 换等价属性或告知用户 |

多条小改可以合并成一次 `edits` 列表；全部改完 → STEP 5。

## STEP 5 verify（完成判据；只有 fail 必须修）
`origin_verify_graph(graph, expected_series=6, min_font_pt=8)`——程序**跨全部图层**反读核验：
曲线数、轴标题文本、轴标题字号、图层几何（占页 % 越界告警）、图例状态、交付文件完整性。

| 结果 | 动作 |
|---|---|
| `passed:true`（全 pass） | → STEP 7 交付 |
| 有 `warn` | 可交付，回复时向用户提一句 |
| 有 `fail` | → STEP 6 修复后**重新 verify** |
| `unreadable` | 该项读不到（多因窗口未激活）：先 `manage_pages("activate")` 再验一次 |
| 拿不准图效果 | `origin_view_graph` 自己看 |

## STEP 6 失败恢复（error_code → 动作；完整映射调 `origin_error_codes`，每个失败返回自带 recovery.policy + recovery.diagnose）

| 信号 | 含义 | 动作 |
|---|---|---|
| `connection_error` / 连不上 | COM 未就绪/多实例/权限 | `origin_diagnose` → 按 diagnose 修；残留进程 `taskkill /F /IM Origin64.exe` 等 2 秒重连 |
| `export_error` | 三级导出通道全失败 | 读返回里 `attempts`（save_fig→COM→LabTalk 各自失败原因）+ `origin_diagnose` 查目录权限 |
| `plan_not_found` | plan_id 过期/不存在 | 重新 `origin_plot_plan` |
| `plan_stale` | 数据/映射变了或执行了旧计划 | 用返回的 `newer_plan_id` 重跑，或重新 plan；确要旧计划传 `force=true` |
| `formula_no_effect` | 列公式没生效 | 查函数名：LabTalk 底 10 对数是 `log()` **不是** `log10()`（会静默无效）；复核源列 |
| `no_data_to_fit` / `insufficient_data` | 拟合列全空 / 点数 <3 | `origin_read_worksheet` 复核数据；修数或改描述统计 |
| `column_empty` / `column_all_nan` | 列空 / 全空值 | 查上游 `origin_column_formula`/写入是否生效（全 NaN 常因公式无效或源列含文本） |
| `manual_save_required` | 策略禁止自动存 .opju | 提醒用户在 Origin 内按 **Ctrl+S** 手动保存；或取消 `DSH_ORIGIN_NO_AUTO_SAVE` |
| `window_activation_failed` | 目标图页没切成活动窗口 | `origin_manage_pages("activate", pages=[短名])` → 重试原调用 |
| `layer_not_found` | 图层索引错 | `origin_inspect_graph` 拿真实索引重调 |
| edit 返回 `rejected` | 参数非法 | 读 message 改参数重试 1 次 |
| `template_unavailable` | 模板名在本机版本不存在 | `origin_status` 查可用模板（双 Y 实测可用 `doubley`/`righty`） |
| `worksheet_not_found` / `column_not_found` | 引用错 | `origin_list_sheets` + `origin_read_worksheet` 重建引用 |
| `origin_busy_user_session` | 用户在手动操作 | 等 5s 重试 ≤3 次 → 告知用户 |
| `labtalk_blocked` | LabTalk 含破坏命令（delete/doc -s/exit/win -c）被门禁拦截 | 确认确需执行 → 带 `confirm=true` 重发；否则换非破坏命令 |
| `com_blocked_by_dialog` | Origin 被模态对话框卡住且看门狗未能自动解除（error 含对话框标题） | 在 Origin 窗口手动关闭该对话框 → 原样重试；无响应时 taskkill |
| 读回值是 `NaN` | LabTalk 静默失败（通道在当前图/版本无效） | **不要重试同一通道**；换 `origin_edit_*` 的 COM 通道，或 `origin_view_graph` 目视确认 |
| `layer_overlap:*` 检查 fail/warn | 图层 bbox 重叠（部分重叠=fail；完全重叠=warn） | 多面板布局损坏 → 重建；双 Y 类共享绘图区属正常 → verify 传 `allow_full_overlap=true` |

同一问题修复 2 轮仍 fail：停止重试，如实向用户报告已做/未做 + `origin_view_graph` 渲染图给用户决策。

## STEP 7 交付（按用户要什么给什么）
- 要"完整交付物" → `origin_export_delivery(graph, source_path='数据.csv', fmts='png,pdf')`：
  源文件同级建 `<数据名>_Origin_<时间戳>/` 收纳图片 + **可编辑 OPJU** + 数据 csv + 报告 txt 并逐文件核验（原始数据只读不动）。
  设置了 `DSH_ORIGIN_NO_AUTO_SAVE=1` 时 OPJU 不自动写，issues 里会提示 → **提醒用户 Ctrl+S**。
- 只要图片 → `origin_export(graph, fmt='png', width=1200)`（png/svg/pdf/tif/emf；引擎内置三级回退链与文件头校验）。
- 只存工程 → `origin_save_project(path)`（省略扩展名自动补 .opju）。
- 最终回复 = 交付文件路径 + verify 结论一句话（warn 一并告知）。

## 附录 A 领域模板（origin_plot_template）
所有模板都支持 `x_title` / `y_title` 覆盖轴标题（多系列 y 轴不再自动猜标题，
不传时模板用内置默认）；`stacked_spectra` 另支持 `gradient=true`（系列渐变色，
温度/浓度序列一目了然；导出已拆独立 COM 任务，渐变+导出不会丢曲线）。
| template_id | 用途 | data 要点 |
|---|---|---|
| `stacked_spectra` | 多谱线纵向堆叠偏移（XPS/UV-Vis/PL/FTIR） | `{"x":[..], "spectra":{"名":[..]}}`，`offset="auto"`, `reverse_x` |
| `xrd_pattern` | XRD 三件套（双层：上层 Observed 小散点+Calculated 满量程，下层 Difference 独立量程+零线+**红色加粗相刻线**，X 轴严格对齐） | `{"two_theta":[..],"observed":[..],"calculated":[..],"difference":[..]?,"phases":{"相":[2θ..]}?}` |
| `dual_y` | 双 Y 轴（左右轴标题走写后读回验证通道） | `{"x":[..],"left":[..],"right":[..],"left_name","right_name"}`；verify 时传 `allow_full_overlap=true` |
| `forest` | 森林图（效应量+CI+零参考线；CI/零线不进图例，研究名逐行标注） | `{"labels":[..],"effect":[..],"ci_low":[..],"ci_high":[..]}` |
| `multi_panel` | 多面板纵向堆叠（图层几何自动均分去重叠） | `{"x":[..]?,"panels":{"面板名":[..]}}` |

## 附录 B 分析工具（结果直接回复用户；统计推断细节见 origin-stats skill）
- **变换** `origin_transform(worksheet, column, op, ...)`：op 现有
  smooth / normalize / derivative / interpolate / **ln / log10 / reciprocal /
  exp / sqrt / abs**（动力学 ln[A]、Arrhenius 1/T、二级 1/[A] 直接用，不用自己算）；
  结果 ≤2000 点时回传 `values` 数组，**可直接喂 plot_template**（如 TGA+DTG 双 Y：
  transform(derivative) 拿 values → plot_template dual_y，不用 read_worksheet）；
  对数/倒数对 0/负数会产出无效点，返回里 `invalid_points` 提示先用 filter_data 清洗。
- **拟合** `origin_fit(..., kind=...)`：linear 之外可用 Origin 内置 NLFit 名——
  `ExpDec1 / ExpGrow1 / Gauss / Lorentz / Boltzmann / DoseResp /
  MichaelisMenten / Logistic / Poly2`（返回值带 `supported_kinds` 清单）。
  默认自动关闭 NLFit 的 FitLine*/Residual* 报告副产品页（`removed_report_pages`
  记录清单），不会再爆窗口；空列/点数不足会以 `no_data_to_fit`/`insufficient_data` 明确拒绝。
  收敛控制：`initial_params={"A":1.5}` 初值、`fixed_params={"A":true}` 固定参数
  （linear 用 `{"slope":0}` 固定斜率）、`weight_col="误差列"` 加权（仅 NLFit）。
- **积分** `origin_integrate(..., baseline=...)`：baseline 可传 `"min"`/`"first"`
  或数值——DSC 焓变等需要扣基线的场景用。
- **描述统计/检验/降维/生存**（`origin_stats` / `origin_ttest` / `origin_anova` /
  `origin_pca` / `origin_correlate` / `origin_survival` / `origin_fft` /
  `origin_peak_find` / `origin_peak_fit` / `origin_filter_data` /
  `origin_histogram`）：用法与科学边界见 **origin-stats skill**；
  这些结果均带 `confidence_note`（自研统计仅探索用，正式发表用 SPSS/R/Origin 复核）。
- **数据加工**：`origin_column_formula`（Origin 原生列公式；函数名自动纠正，
  无效公式硬失败 `formula_no_effect`）、`origin_mask_points`（NaN 化屏蔽，可逆）、
  `origin_manage_data`（sort 按列排序整表 / transpose 行列转置写新表）。
- **窗口清理**：`origin_manage_pages("closeAll")` 一键关闭项目内全部页面
  （会话结束恢复干净状态用；返回 removed 清单）。
- **交付后让机**：用户要手动微调时 `origin_release()`（Origin 保持打开、
  自动化断开，用户操作不被干扰）；后续任意工具调用自动重连，
  `origin_reconnect()` 可显式恢复。

## 附录 C 通道纪律与环境变量（排障时读）
**通道纪律**（这是"必须用 edit 工具、别绕 LabTalk"的原因）：
- **COM 作用域通道永远可靠**（与哪个窗口活动无关）：`plot_list`、`axis` 对象、`p.color`、`set_int` 几何。
- **LabTalk 裸表达式只在"目标图页恰为活动窗口"时解析**，否则静默返回 NaN 或写进别的窗口
  （实测 `layer.left` 读到过工作簿几何）。`origin_edit_*` 已内置 激活→执行→读回→NaN 判定，所以直接用工具就是对的。
- 单位：`page.width/height` 是 dots（600dpi，cm=dots/600*2.54）；`layer_geometry_pct` 是 %页；图例用四角锚点最稳。

**环境变量**（路径定位不写死目录；DSH 与独立部署共用）：

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `ORIGIN_SESSION` | attach | `isolated`=不劫持已打开的 Origin（检测到进程即拒绝 `origin_busy_user_session`） |
| `DSH_ORIGIN_AUTOKILL` | isolated 时开 | 连接前 `taskkill /F /IM Origin64.exe` 清残留并等 2 秒；attach 默认关（保护手动会话，误杀未保存工作） |
| `DSH_ORIGIN_NO_AUTO_SAVE` | 未设（允许自动保存） | `1`=禁止脚本自动写 .opju，返回 `manual_save_required` 提示用户 Ctrl+S |
| `ORIGIN_MCP_PROFILE` | full | `compact`=隐藏统计批（ttest/anova/pca/survival） |
| `ORIGIN_IPC_LOCK` | 关 | `1`=跨进程命名互斥体（多 DSH 实例共用一个 Origin 时防踩踏） |
| `DSH_ORIGIN_DISPATCH_TIMEOUT` | 90 | COM 调用软超时秒数；超时后看门狗自动点掉 Origin 模态对话框（OK/取消类），仍卡死则按 `DSH_ORIGIN_AUTOKILL` 策略处置并返回 `com_blocked_by_dialog` |
| `DSH_ORIGIN_WATCHDOG_GRACE` | 15 | 看门狗点击对话框后的额外等待秒数 |

- 逃生舱：`origin_labtalk`（任意 LabTalk 执行，带激活+读回+NaN 防护+**破坏命令门禁**：delete/doc -s/exit/win -c 需 `confirm=true`）——只在 edit 工具覆盖不到的场景用。
- 其它：`origin_status.capabilities.known_risks` 是本机版本坑清单（如 plotxy 在 2026b 的 box/bar 已走官方模板规避）。
- 部署/客户端兼容性（Kimi Code / Cursor / Claude Desktop / WorkBuddy / DSH）见仓库 **COMPATIBILITY.md**。

## 备注
- **不要读取仓库 README.md**——本 skill 即完整操作规程；参数拿不准调 `origin_help` / `origin_catalog` / `origin_error_codes` / `origin_cookbook`。
- 全部 50 工具已按上述步骤挂载完毕（统计细节在 origin-stats skill）；新增需求先想"挂在哪一步"再选工具。
