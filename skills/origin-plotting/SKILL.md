---
name: origin-plotting
description: Origin 全流程 SOP（v2.2.1，43 工具按步骤挂载）：检查环境 → inspect 现状 → 规划（新建/编辑）→ 执行 → verify 校验（只有 fail 必须修）→ 失败恢复 → 交付。含意图→工具速查、错误码→动作映射、领域模板、细粒度改图、通道纪律。
whenToUse: 用户要求用 Origin 画图、导入数据、拟合/FFT/统计分析、领域模板（谱图堆叠/XRD/双Y/森林/多面板）、导出交付 OPJU，或对已画好的图做微调（换某条线颜色/加粗/隐藏、改轴范围、挪图例、调页面尺寸、关掉多余窗口）时
---

# Origin SOP v2.2.1（决策流驱动；工具挂在步骤下，按序调用）

## 主循环（先看这张图，再往下读）

```
[1] origin_status 检查环境
     │ 读用户意图，四选一分流：
     ├─ 画新图 ────────────────→ [3] 规划（列角色有歧义走 plan 确认流）
     │                              └→ [4A] 粗粒度建图 → [5]
     ├─ 改已画好的图 ─→ [2] inspect_graph 拿现状 → [4B] 细粒度编辑 → [5]
     ├─ 关窗/整理/激活 ─→ origin_manage_pages 一步完成 → 直接结束
     └─ 只要数字不要图 ─→ 附录B 分析工具，结果直接回复用户
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

## STEP 1 检查环境（每个会话首次 Origin 操作前）
调 `origin_status`：
- `ok:true` → 记下 `capabilities.fine_edit`（true=细粒度编辑可用）、`known_risks`（本机版本坑）→ 进分流。
- `ok:false` → 读 `error_code + recoverable + next_actions` 照做：
  - `origin_busy_user_session`：用户正在手动操作 Origin → 告知用户，等 5 秒重试，最多 3 次，仍失败则如实报告；
  - 其余（Origin 未开/多实例/COM 连接失败）→ 按 `next_actions` 执行，不要盲目重试。

## STEP 2 inspect（"改已有图"类任务的入口，跳过=盲改）
用户说"这条线/这个轴/图例/页面"时，先拿现状：
- `origin_inspect_graph(graph)` → 每图层几何、每条曲线（索引/颜色/线型/符号）、轴范围与字号、图例、页面尺寸（cm+dots+dpi）；
  用户说"第 2 条线" → `plot` 索引 = **1**（0 起）；颜色先用 inspect 确认，避免改错条。
- `origin_list_pages()` → 全部页面短名 + 当前活动窗口（关窗/激活/重命名都用这里的短名）；
  用户只说"刚才那张图"没给名 → `origin_list_graphs()` 列图页短名确认是哪张。
- 不确定图长什么样 → `origin_view_graph(graph)` 自己看图再决定改什么。

## STEP 3 规划（新建流；编辑流直接跳 4B）
- 数据进 Origin：
  - 本地文件 → `origin_load_file(path='D:/data/样品.csv')`（CSV/TXT/XLSX/XLS，中文路径安全）→ 返回 `worksheet`；
  - 内存数据 → `origin_write_data(columns)`：`{"列名":[...],...}` **第一列自动 X 其余 Y**，也收二维/一维列表。
- 列角色有歧义（>2 列、不确定谁是 X/yerr/labels）→ `origin_plot_plan(columns, ...)`：
  离线秒回逐列画像 + 角色建议 + **questions**；questions 非空 → **先问用户** → `origin_execute_plan(plan_id)`（写数+画图+导出一条龙）→ 跳 STEP 5。
- 角色明确 → 直接 4A。

## STEP 4A 粗粒度建图（新建流）
按图型选工具（都返回 `graph`）：

| 图型/需求 | 调用 |
|---|---|
| 写数+画图+导出一步到位（最常用） | `origin_plot_file(...)` |
| 已有工作表，画 2D | `origin_plot(worksheet, ...)`，含 line/symbol/line_symbol/box/bar |
| 领域图（谱图堆叠/XRD/双Y/森林/多面板） | `origin_plot_template(template_id, data, ...)`，模板见附录 A |
| 3D 表面/散点、等高线、直方图 | `origin_plot3d` / `origin_plot_contour` / `origin_histogram` |

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
- 轴标题从列名自动推断（`temperature_C` → "Temperature (°C)"）。

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
| 加峰位/条件标注 | `origin_add_text(graph, "peak @ 30 C", x=30, y=118)` |
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

## STEP 6 失败恢复（error_code / 现象 → 动作）

| 信号 | 含义 | 动作 |
|---|---|---|
| `window_activation_failed` | 目标图页没切成活动窗口 | `origin_manage_pages("activate", pages=[短名])` → 重试原调用 |
| `layer_not_found` | 图层索引错 | `origin_inspect_graph` 拿真实索引重调 |
| edit 返回 `rejected` | 参数非法 | 读 message 改参数重试 1 次 |
| `plan_not_found` | plan_id 过期/不存在 | 重新 `origin_plot_plan` |
| `template_unavailable` | 模板名在本机版本不存在 | `origin_status` 查可用模板（双 Y 实测可用 `doubley`/`righty`） |
| `worksheet_not_found` / `column_not_found` | 引用错 | `origin_list_sheets` + `origin_read_worksheet` 重建引用 |
| `origin_busy_user_session` | 用户在手动操作 | 等 5s 重试 ≤3 次 → 告知用户 |
| 读回值是 `NaN` | LabTalk 静默失败（通道在当前图/版本无效） | **不要重试同一通道**；换 `origin_edit_*` 的 COM 通道，或 `origin_view_graph` 目视确认 |

同一问题修复 2 轮仍 fail：停止重试，如实向用户报告已做/未做 + `origin_view_graph` 渲染图给用户决策。

## STEP 7 交付（按用户要什么给什么）
- 要"完整交付物" → `origin_export_delivery(graph, source_path='数据.csv', fmts='png,pdf')`：
  源文件同级建 `<数据名>_Origin_<时间戳>/` 收纳图片 + **可编辑 OPJU** 并逐文件核验（原始数据只读不动）。
- 只要图片 → `origin_export(graph, fmt='png', width=1200)`（png/svg/pdf/tif/emf）。
- 只存工程 → `origin_save_project(path)`（省略扩展名自动补 .opju）。
- 最终回复 = 交付文件路径 + verify 结论一句话（warn 一并告知）。

## 附录 A 领域模板（origin_plot_template）
| template_id | 用途 | data 要点 |
|---|---|---|
| `stacked_spectra` | 多谱线纵向堆叠偏移（XPS/UV-Vis/PL/FTIR） | `{"x":[..], "spectra":{"名":[..]}}`，`offset="auto"`, `reverse_x` |
| `xrd_pattern` | XRD 三件套（双层：上层 Observed 散点+Calculated 满量程，下层 Difference 独立量程+零线+相刻线，X 轴严格对齐） | `{"two_theta":[..],"observed":[..],"calculated":[..],"difference":[..]?,"phases":{"相":[2θ..]}?}` |
| `dual_y` | 双 Y 轴 | `{"x":[..],"left":[..],"right":[..],"left_name","right_name"}` |
| `forest` | 森林图（效应量+CI+零参考线） | `{"labels":[..],"effect":[..],"ci_low":[..],"ci_high":[..]}` |
| `multi_panel` | 多面板纵向堆叠 | `{"x":[..]?,"panels":{"面板名":[..]}}` |

## 附录 B 只要数字（不建图，结果直接回复用户）
`origin_fit`（拟合，可上图）、`origin_stats`、`origin_transform`（smooth/normalize/derivative/interpolate）、
`origin_integrate`、`origin_fft`、`origin_correlate`、`origin_peak_find`、`origin_filter_data`（删点写回工作表）、
`origin_ttest` / `origin_anova` / `origin_pca` / `origin_survival`（统计批，纯 numpy）。
需要后续上图时用 `origin_plot` 返回 `graph` 再走 STEP 5。

## 附录 C 通道纪律（排障时读；这是"必须用 edit 工具、别绕 LabTalk"的原因）
- **COM 作用域通道永远可靠**（与哪个窗口活动无关）：`plot_list`、`axis` 对象、`p.color`、`set_int` 几何。
- **LabTalk 裸表达式只在"目标图页恰为活动窗口"时解析**，否则静默返回 NaN 或写进别的窗口
  （实测 `layer.left` 读到过工作簿几何）。`origin_edit_*` 已内置 激活→执行→读回→NaN 判定，所以直接用工具就是对的。
- 单位：`page.width/height` 是 dots（600dpi，cm=dots/600*2.54）；`layer_geometry_pct` 是 %页；图例用四角锚点最稳。
- 其它：`ORIGIN_SESSION=isolated` 时不劫持已打开的 Origin；`ORIGIN_MCP_PROFILE=compact` 隐藏统计批；
  `origin_status.capabilities.known_risks` 是本机版本坑清单（如 plotxy 在 2026b 的 box/bar 已走官方模板规避）。

## 备注
- **不要读取仓库 README.md**——本 skill 即完整操作规程；参数拿不准调 `origin_help` / `origin_catalog` / `origin_error_codes`。
- 全部 43 工具已按上述步骤挂载完毕，无未提及工具；新增需求先想"挂在哪一步"再选工具。
