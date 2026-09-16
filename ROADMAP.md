# ROADMAP v2.4+ — GitHub 高星 Origin 项目长处吸收方案

> 调研范围：GitHub 搜索 `originpro` / `origin mcp` / `originlab` 全部高星结果（11 个相关项目，
> 654★ ~ 10★），逐个提炼长处，对照本项目 v2.3.0（50 工具）形成差距矩阵与分批落地方案。
> 生成日期：2026-09-16。原则：**每项都注明来源、现状差距、改动点、验收标准**；
> 沿用项目纪律——真机探针先行、如实拒绝不可用通道、所有改动冒烟+识图。

---

## 一、调研对象一览（11 项）

| 项目 | ★ | 类型 | 最值得吸收的长处 |
|---|---|---|---|
| hang-jin/editaplot | 654 | Codex Skill | 语义确认流、render-plan 冻结、参考图安全借鉴、50 张验证图资产、隔离实例+队列 |
| leima-max/origin-pro-mcp-skill | 224 | MCP+Skill | pytest+Origin-aware skip、双语 README、sanitized 发布纪律、避开 impasc 副作用 |
| originlab/Python-Samples | 98 | 官方示例 | originpro API 全景（矩阵/图像/分析模板批处理是官方认可的一等能力） |
| Ge-Shun/origin-mcp | 96 | MCP | **Origin 内嵌 Python bridge**（UI 线程自动化）、文件白名单、doctor/agentic bootstrap |
| GG-pro-viki/Origin | 86 | EXE+Skill | SVG/CDR 多格式+**能力降级矩阵**、Graph Gallery 模板下载、部署验收任务书、任务队列 |
| garethbeaumo/originlab-mcp | 80 | MCP | 66 工具细拆、**MCP Resources 只读会话快照**、release/reconnect、本地状态面板 UI |
| Gerry2024-hub/…plotting-skill | 74 | Skill | 模板脚本 + references/troubleshooting 分层文档结构 |
| deliuou/originplot-skill | 70 | Skill | **FigureSpec 声明式协议**（DataSpec/PageSpec/LayerSpec/PlotSpec/StyleSpec/ExportSpec）+ L0-L9 层级控制 + "spec 先行→确认→再画" |
| jsbangsund/python_to_originlab | 42 | 库 | **matplotlib figure→Origin graph 直转**（从 fig/ax 句柄提取数据与线属性） |
| youngminsw/Origin-Pro-MCP | 39 | MCP | **模态对话框看门狗**（软超时轮询+报对话框标题+自动消除+硬超时杀）、LabTalk tokenizer 门禁、会话账本、禁红绿-only 无障碍纪律 |
| hzsci/ace-sci-origin | 25 | Skill | **Origin OLE 嵌入 PowerPoint**（双击可编辑组图版）、参考图=视觉契约、generation_status.json |

### 与本项目已有的重叠（不重复立项）

决策流双 SKILL、错误码+RECOVERY_MAP、三级导出回退链+文件校验、style_mode/family 调色板
（CVD-safe）、plan_hash 防过期、install.py 多客户端、COMPATIBILITY 实测失败场景表、
41 个真机冒烟案例、autokill/隔离会话、COM 队列串行（≈ editaplot job_queue）。

---

## 二、差距矩阵（核实过代码的现状）

| 能力 | 我们 v2.3.0 | 高星参照 | 结论 |
|---|---|---|---|
| 模态对话框处理 | ❌ Origin 弹框时卡到超时 | youngminsw 看门狗 | **P0 补** |
| LabTalk 破坏命令门禁 | ❌ origin_labtalk 裸逃生舱 | youngminsw tokenizer 门禁 | **P0 补** |
| 释放/重连 Origin | ❌ 无"让给用户"语义 | garethbeaumo release/reconnect | **P0 补** |
| fit 初始值/固定参数/权重 | ❌ 只有 kind（supported_kinds 有） | garethbeaumo initial/fixed_params | **P0 补** |
| remove_plot / change_plot_data | ❌（edit_plot 只改样式） | garethbeaumo/youngminsw | **P0 补** |
| transpose / sort 工作表 | ❌ | youngminsw | P0（小） |
| 声明式图协议 | ⚠️ origin_plot_plan（内存 plan_id） | deliuou FigureSpec YAML、editaplot render-plan | **P1 升级** |
| MCP Resources 只读快照 | ❌（只有 list_* 工具） | garethbeaumo originlab://session | **P1 补** |
| 矩阵工具集 | ⚠️ 仅 plot3d 内部用矩阵 | youngminsw 矩阵 5 工具、官方 Matrix 大类 | **P1 补** |
| 参考图安全借鉴 | ❌ | editaplot 图形简报、hzsci 视觉契约 | **P1 补** |
| 矢量/印刷格式 | png/svg/pdf/tif/emf（无 eps） | GG-pro-viki SVG/CDR、多格式 | P2（eps） |
| 验证图视觉回归资产 | ⚠️ 41 案例有 JSON 无图库基准 | editaplot 50 张验证 PNG | P2 |
| pytest 化测试 | ⚠️ 独立 --offline-test | leima Origin-aware skip | P2 |
| PyPI/uvx 分发 | npm+GitHub+install.py | Ge-Shun/youngminsw pip/uvx | P2 |
| 双语 README | ❌ 纯中文 | leima/Ge-Shun/garethbeaumo | P2 |
| otpu 模板资产/下载 | ❌ 模板代码内置 7 个 | GG-pro-viki Gallery 下载、deliuou otpu | P2 |
| matplotlib→Origin | ❌ | jsbangsund | P2 |
| PPT OLE 组图 | ❌ | hzsci | P2 |
| 文件访问白名单 | ❌（本地单机风险低） | Ge-Shun ALLOWED_ROOTS | P2 |
| 状态面板 UI | ❌（install.py CLI） | garethbeaumo :8765 面板 | P3 |
| Origin 内嵌 bridge | ❌（外挂 COM） | Ge-Shun（根治 LabTalk 静默类问题） | P3 远期 |
| 批处理任务队列 | ❌ 单次调用 | GG-pro-viki 队列 EXE、官方分析模板批处理 | P3 |

---

## 三、分批落地方案

### P0 —— 稳健性与交互补齐（改动小、收益直接，目标 v2.4.0）

**P0-1 模态对话框看门狗**（来源：youngminsw；改动：`origin_engine.py` COM 线程执行层）
- COM 调用软超时（默认 60s，`DSH_ORIGIN_DISPATCH_TIMEOUT` 可配）后启动看门狗线程：
  用 Win32 `EnumWindows` 找 Origin 进程的模态对话框，枚举按钮文本自动点掉
  （只点 OK/Yes/Cancel/确定/取消 白名单）；超时错误返回**精确对话框标题**进
  `error` 字段 + `recovery.diagnose`；硬超时（默认 +90s）才 taskkill。
- 验收：探针脚本人为触发许可/升级弹窗类对话框 → 自动消除或带标题报错；
  既有 41 案例全绿（不能误伤正常弹窗流程）。

**P0-2 LabTalk 破坏命令门禁**（来源：youngminsw；改动：`_labtalk_impl`）
- tokenizer 分词后按命令首 token 匹配：`delete/win -c/doc -s/save -i/impASC` 等
  破坏/清空类命令默认拒绝，要求 `confirm=true` 参数才放行；字符串字面量与注释豁免。
- 验收：`delete Book1` 被拒（error_code=labtalk_blocked）+ confirm 后放行；
  `wks.colWidth=...` 等正常命令零影响（fine_edit/d15 冒烟）。

**P0-3 release_origin / reconnect_origin**（来源：garethbeaumo；改动：engine+server+SKILL）
- `origin_release`：断开 COM 引用但**不关 Origin**，返回"已交还用户，窗口保持"；
  后续工具调用自动重连（现有 `_connect_impl` 路径天然支持）。
- `origin_reconnect`：显式重连+状态报告。
- SKILL：交付后建议 release，让用户手动微调不被自动化锁干扰。
- 验收：release → 用户可正常操作 Origin → reconnect → 工具链路照常。

**P0-4 fit 参数级控制**（来源：garethbeaumo；改动：`_fit_impl`）
- 新参数：`initial_params`（dict）、`fixed_params`（dict）、`weight_col`（yerr 权重列）。
  经 `op.fit`/NLFit 参数映射传给 Origin；结果回传参数误差与置信区间（已有 d_Km 类）。
- 验收：MM 拟合 d12 案例 + 新断言：给定 Km 初值收敛更快（迭代数下降）、
  固定 Vmax 后 d_Vmax=0；chem v2 全量回归。

**P0-5 remove_plot / change_plot_data / transpose / sort**（来源：garethbeaumo、youngminsw）
- `edit_plot` 扩 `action="remove"|"change_data"`（remove 走 `gl.remove_plot`/LabTalk
  `layer -r <idx>`，change_data 换列映射）；`origin_manage_sheets` 扩 `transpose/sort`。
- 验收：3 曲线图删 1 条读回 2 条；换数据列后 verify series 更新；transpose 行列数互换。

### P1 —— 协议与能力升级（结构性，目标 v2.5.0）

**P1-1 FigureSpec 声明式图协议**（来源：deliuou + editaplot；改动：新 `origin_spec.py` + plan 工具族）
- `origin_plot_plan` 升级：计划可**导出/导入 YAML**（data/page/layer/plot/style/export
  分节），`origin_execute_plan` 接受 spec 文件路径；确认流沿用 plan_hash——
  **spec 先行 → 展示用户 → 确认 → 执行**；spec 与 plan_id 双向兼容（老调用不破坏）。
- 验收：d14/d07 场景改写为 spec 文件流程跑通；spec 落盘可 diff、可重放（同 spec 同结果）。

**P1-2 MCP Resources 只读会话快照**（来源：garethbeaumo；改动：`origin_mcp_stdio.py`）
- stdio 模式注册 Resources：`origin://session`（全项目快照）、`origin://worksheets`、
  `origin://graphs`、`origin://worksheet/{book}/{sheet}`——AI 不改项目即可 inspect，
  列表型工具保留（DSH 模式不受影响）。
- 验收：mcp_handshake 增 resources/list 断言；读快照与 list_* 结果一致。

**P1-3 矩阵工具集**（来源：youngminsw + 官方 Python-Samples；改动：新 `origin_matrix.py`）
- `origin_matrix_write / origin_matrix_read / origin_worksheet_to_matrix` +
  plot3d/contour 接受矩阵名输入（当前仅内存数据）；为热图大数据（>10k 点）铺路。
- 验收：50×50 矩阵写入→热图→verify；worksheet 升维转换一致。

**P1-4 参考图安全借鉴**（来源：editaplot + hzsci；改动：SKILL 指引 + `origin_style_hint`）
- 用户给参考 PNG →（AI 识图，SKILL 规定）产出"图形简报"JSON：面板布局/图表家族/
  线型符号/配色 family 建议 → 传给 apply_style 的 `style_overrides`。
- **红线写进 SKILL**：禁止从像素反推数据、禁止复制文字/Logo、不承诺 1:1 复刻
  （与 editaplot 同纪律）。
- 验收：给 Nature 风格参考图 → 产出简报 → d01 场景风格趋近；冒烟+识图。

**P1-5 验证图视觉基准**（来源：editaplot 50 张验证图；改动：`smoke/visual_baseline/`）
- 从 41 冒烟案例选 12 张代表产物入库（PNG+sha256 清单）；新增 `smoke/visual_diff.py`：
  重跑产物与基准做感知哈希（pHash）对比，>阈值即报——防外观回归（本次图例换行
  类问题就可被它拦住）。
- 验收：visual_diff 在当前基线 0 差异；人为改配色后能报差异。

### P2 —— 生态与分发（目标 v2.6.0）

- **P2-1 PyPI 分发**（Ge-Shun/youngminsw 模式）：`pip install dsh-origin-plugin`，
  pyproject 化；install.py 保留为免 PyPI 路径。
- **P2-2 双语 README**（leima 模式）：README.zh-CN.md + README.md 对翻。
- **P2-3 pytest 化**（leima 模式）：offline 断言迁 pytest，Origin 依赖用例自动 skip；
  CI 无 Origin 机器可跑。
- **P2-4 EPS 导出 + 能力降级矩阵**（GG-pro-viki）：fmt 增加 eps；export 返回
  `capabilities`（本机可出什么格式）；COMPATIBILITY 增格式×Origin 版本矩阵。
- **P2-5 otpu 模板资产 + Graph Gallery 下载器**（GG-pro-viki/deliuou）：
  内置 2-3 个 .otpu（journal/答辩/海报）；`origin_template_search` 关键词下载
  OriginLab Graph Gallery .zip（浏览器 UA+Referer，403 重试提示）。
- **P2-6 matplotlib→Origin 桥**（jsbangsund）：`origin_import_matplotlib(fig_path|.pkl)`
  或接受 mpl Figure 句柄（同进程时），提取 axes/lines 数据+线属性映射到 plot 工具。
- **P2-7 PPT OLE 组图交付**（hzsci）：`origin_export_delivery` 增 `pptx=true` 选项：
  图页 OLE 写入 PowerPoint（双击可编辑）+ 面板字母标注 + generation_status.json。
- **P2-8 文件白名单**（Ge-Shun）：`DSH_ORIGIN_ALLOWED_ROOTS` 限制
  load/export/save 的路径前缀（默认不设，安全场景开启）。

### P3 —— 远期（架构级）

- **P3-1 Origin 内嵌 bridge**（Ge-Shun 架构）：Origin 内装 Start/Stop App +
  内嵌 Python 跑 127.0.0.1 token 桥——从根上消除 COM 线程与 LabTalk 静默失败
  一类问题。工程量大，待 v2.x 稳定后评估。
- **P3-2 批处理任务队列**（GG-pro-viki）：多文件/多 spec 顺序执行+断点续跑报告。
- **P3-3 状态面板 UI**（garethbeaumo）：本地网页（连接测试/会话快照/一键写客户端配置，
  复用 install.py 逻辑）。

---

## 四、不做的事（评估后明确拒绝）

| 项 | 来源 | 拒绝理由 |
|---|---|---|
| EXE 分发内置 Python | GG-pro-viki | 体积 50MB+，MCP 生态不需要；npm/PyPI 已覆盖 |
| CDR（CorelDRAW）输出 | GG-pro-viki | 用户面极窄，SVG 已满足排版需求 |
| 抓真实 Windows 安全令牌/沙箱探测 | editaplot | 过度工程；DSH/Claude 环境无需 |
| 彩虹式 41 模板全抄 | editaplot | 医学/SHAP 等模板离开其数据契约无验证价值；按需逐个加 |
| 禁红绿-only 硬编码 | youngminsw | 我们的 CVD-safe palette 已用色盲距离选色，约束更强 |

---

## 五、执行顺序与验收总纲

1. **P0 五项一个批次**（预计一炉）：每项先真机探针再实现，全部落地后跑
   七套冒烟 + 识图抽查 → v2.4.0。
2. **P1 五项一个批次**：FigureSpec 与 P1-5 视觉基准优先（其余依赖它们），
   41 案例中挑 6 个改写为 spec 流程对照验证 → v2.5.0。
3. **P2 按需分拆**：PyPI+双语+pytest 打一个"工程化"小版本；
   eps/otpu/mpl桥/PPT-OLE 按用户场景优先级排。
4. 每批收尾固定动作：CHANGELOG、SKILL 一致性校验、COMPATIBILITY 增补、
   git commit（不 push，待指令）、记忆更新。
