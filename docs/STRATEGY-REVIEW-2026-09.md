# dsh-origin-plugin 战略评估：竞品对照 + 我的判断 + 改进与路线

> 日期：2026-09-17 ｜ 基线：v2.6.2（62 工具 / 30 错误码）
> 本文只做分析，不改代码。所有"建议"都标注了收益/成本/验证方式，可逐条立项。
> 标注 ★ 的段落是**我的独立判断**（可能有争议，但这是我的真实看法）。

---

## 0. 一句话结论

**你的护城河不是"能驱动 Origin"，而是"驱动得可信且可复现"**——前者现在至少有 4 个开源项目能做，后者（读回验证 + 视觉基准 + FigureSpec + 稳定错误码）目前只有你在做。
**当前最大风险不是功能不够，是"进不了用户的手"**：三个竞品都做了 **Origin App（.opx）一键形态**，你没有；而性能问题 80% 不在执行层，**在 AI 的决策轮次**。

### 我的五条核心判断（先给结论，后面逐条论证）

| # | 判断 | 置信度 |
|---|---|---|
| 1 | ★ **进程隔离 bridge 是伪刚需，别做**。当前架构 + 看门狗已拿到 90% 收益；真正该做的是 **Origin App 形态**（把"启动"从命令行变成按钮） | 高 |
| 2 | ★ **62 个工具是负债不是资产**。方向不是"拆成更多 skill"，而是**收敛成 ~10 个粗粒度工具**，专家工具隐藏在 compact/full 之后 | 高 |
| 3 | ★ **AutoFigure 不是威胁，是相邻赛道**。它做"方法框图/示意图"，你做"实验数据图"，重叠 <10%。真正的威胁是 **Ge-Shun/origin-mcp**（同赛道、同形态、已上 PyPI、有模板库） | 高 |
| 4 | ★ **速度问题的解药是"砍轮次"不是"砍耗时"**。SKILL 文本只能省决策时间；要真快，必须配**端到端粗粒度工具 + 默认参数 + 预热** | 高 |
| 5 | ★ **新手层最大缺口是 Origin App 与"零安装脚本模式"**。MCP 对研究生/实验员是天书，一个 .opx 按钮或一段可粘贴脚本才是他们的语言 | 中高 |

---

## 1. 竞品全景（2026-09 核实）

| 项目 | 形态 | 核心定位 | 强项 | 对你的启示 |
|---|---|---|---|---|
| **Ge-Shun/origin-mcp**（74★, MIT, PyPI） | 外部 MCP server + **Origin 内嵌 Python bridge** | Origin 全功能自动化 | ①localhost + **per-session token 鉴权** ②`ORIGIN_MCP_ALLOWED_ROOTS` ③**模板库**（存图→搜索匹配→复用）④Nature 风格预设 ⑤矩阵/图像/Data Connector ⑥`install-origin-app` 生成 Start/Stop 两个 .opx | 同赛道最像你的对手。**模板资产**是你缺的一环 |
| **erannave/originlab-mcp** | Origin App .opx → 点按钮 → **sidecar 进程**（HTTP `127.0.0.1:8000/mcp`） | 极简 LabTalk 通道 | ①**HTTP transport**（多客户端共享一个 Origin、WSL 可达）②Origin 关自动退出 ③三进程隔离（不占 UI 线程） | **HTTP transport + App 形态**值得抄 |
| **Yike-Ye/OriginLab-MCP**（1.0.0, Origin 2024 实测） | pip 包 + Origin App bridge | "Origin 悄悄忽略你写的东西" | ①**213 项 option 表从 `oPlotIDs.h` 提取**（不是文档）②写入前拒绝未知名 ③写后报告"可能矛盾的证据"④名言：**"readback is a readback, never as proof"** ⑤明确列出 known gaps | 与你的通道纪律**同构且更深**——option 表做法可吸收 |
| **origin-mcp-kimi**（fork） | 同上 + 自适应路径 | 适配 Kimi Code CLI | 自动探测 Origin 内嵌 Python site-packages（解决 pandas 缺失） | 印证：**适配特定国产客户端**是差异化（你已有 install.py 覆盖 7 个客户端，更强） |
| **AutoFigure**（ICLR 2026 + Edit ACL 2026） | Web/SaaS + Python SDK | **文本→可编辑 SVG 示意图** | 5 阶段（VLM 光栅→SAM3 分割→SVG 模板→组装→精修），输出 SVG/mxGraph（draw.io），FigureBench 3300 对 | **相邻赛道，非威胁**。但它证明"可编辑矢量 + 可复现"是刚需 |
| **originplot-skill**（deliuou） | Skill | FigureSpec 声明式 | 图意图与实现解耦 | **你已在 v2.5.0 落地** ✓ |
| **hzsci/ace-sci-origin** | Python | PPT OLE 组图 | 面板字母规范 | 你已落地 python-pptx 版 ✓ |
| **jsbangsund/python_to_originlab** | Python | matplotlib→Origin | 同进程 fig 直传 | 你已落地 pickle 桥 ✓ |
| **Origin-Pro-MCP** | MCP | `list_skills`/`get_skill` 渐进披露 | 场景化加载 | 你已落地（双 SKILL + cookbook）✓ |
| **leima-max / GG-pro-viki / 官方 Samples** | 工具 | pytest / Gallery 下载 / 官方示例 | — | 你已落地 ✓ |

**读这张表的正确方式**：打勾的那些说明你**没有落后**；真正刺眼的是 **Ge-Shun 的模板库**和 **erannave 的 HTTP + App 形态**——这两项你完全没有。

---

## 2. 十二个维度逐项诊断

### D1 软件架构层：进程隔离之争，我给个定论

**现状**：单进程（MCP server 内嵌专用 COM 线程 + originpro），带看门狗（90s 软超时 → 自动点掉模态框 → 仍卡死按 autokill 处置）。

**竞品做法**：Ge-Shun/Yike-Ye 把 bridge 放 **Origin 内嵌 Python**（UI 线程内，版本与 Origin 绑定）；erannave 用 **sidecar 进程**（脱离 UI 线程，仍 attach 回 COM）。

**★ 我的判断**：
- **隔离的真价值只有两条**：①COM 原生崩溃不击穿 MCP server（你实测过一次 EXIT=127）②解耦 Python 版本（外部 Python 3.13 vs Origin 内嵌 3.9）。
- 但**成本是部署复杂度翻倍**：多进程、端口、鉴权、WSL 防火墙、Origin 关时清理、用户排障难度陡增。对一个"给研究生用"的插件，这是**负收益**。
- **更划算的替代**：你已有 release/reconnect + 看门狗 + autokill，等价于"崩溃后可恢复"。真正缺的是 **Origin App 形态**——它解决的是"怎么启动"，而不是"崩不崩"。
- **触发条件**（满足任一再考虑隔离）：①看门狗第二次救不回的原生崩溃 ②需要支持多 Python/多 Origin 版本共存 ③出现企业级多用户并发需求。

**建议**：
- P1：做 **Origin App（.opx）**——点按钮启动/停止 + 自检（见 D4）
- P2：**健康检查自愈**（`origin_status` 检测到 COM 无响应时自动重连一次，无需模型介入）
- 不做：进程隔离（除非触发条件命中）

---

### D2 性能层：速度到底慢在哪（用户痛点，专项见第 5 节）

**实测数据（你的项目）**：
- COM 调用本身：**毫秒级**（connect_ms=1，release 后重连也 <100ms）
- 793 页堆积时：`list_pages` **30.9s**、单次 `close` **59.2s**（差点撞 90s 看门狗）
- 导出：1200px PNG 约 1-3s/张（走 expGraph 三级回退链 + 文件头校验）
- 一轮"画一张图"的调用链：connect → write_data → plot → verify → export → delivery ≈ **6-10 次工具调用**

**★ 我的判断**：**用户感知的"慢"，80% 是模型决策轮次，不是执行耗时。**
每次工具调用 = 一次模型推理（读 schema → 选工具 → 填参数 → 读结果 → 决定下一步），**每次 1-3 秒**。10 轮就是 10-30 秒，而真正的 Origin 执行可能只有 3-5 秒。**优化 COM 是优化那 20%，优化流程是优化那 80%。**

**建议（按性价比排序）**：
1. ★ **端到端粗粒度工具**（`origin_figure`：数据+意图 → 图+导出+交付，一次调用）——**单项收益最大**
2. ★ **默认参数化**：让 90% 调用只需传数据（style_mode/fmt/width 全部有默认值，SKILL 里明确"不要为了调参多问一轮"）
3. **预热保活**：Origin 未启动时首次 attach 最慢（冷启动 3-15s）——提供 `origin_warmup` 或在 App 启动时预连
4. **页堆积治理**：`list_pages` 超阈值（如 200 页）主动提示/自动 closeAll（实测拖慢 30 倍）
5. **导出降级默认**：1200px → 800px（评审足够），高分辨率显式要
6. **verify 合并进 delivery**：少一轮往返

---

### D3 新手利用层：他们根本不会碰 API

**用户画像**（研究生/实验员/高校老师）：
- 会开 Origin、会点菜单、会粘 Excel 数据
- **不会**配 Python 环境、**不会**编辑 mcp.json、**不知道** MCP 是什么
- 他们的"AI 使用"可能只是网页版对话框

**竞品给的答案是**：Origin App（装 .opx → Apps Gallery 出现按钮 → 点一下）。

**★ 我的判断**：新手层你有**两个缺口**，且第二个比第一个更重要：
1. **Origin App 形态**（.opx）——把"启动 bridge"变成按钮（竞品已验证可行）
2. ★ **"零安装脚本模式"**——这是我没在任何竞品身上看到的空白点：
   - 提供一个**纯 SKILL**：教 AI 生成 **Origin Python/LabTalk 脚本**，用户复制粘贴到 Origin 的 Script Window 里跑
   - **零依赖、零安装、零市场、任何 agent 都能用**（只要能读一个 .md）
   - 脚本模板库从你已验证的通道纪律生成（避免生成会静默失败的 LabTalk）
   - 风险：脚本正确性 → 用你的 COMPATIBILITY.md 做"禁写清单"
3. 附带：Origin 内置"自检报告"按钮（跑 diagnose，输出人类可读的一页纸）

---

### D4 软件适配层：skills vs MCP 的部署摩擦（用户重点）

**事实**：民用 agent（DSH、Kimi、豆包、元宝、各类桌面助手）中，**装 skill = 放一个 md；装 MCP = 配环境 + 改配置 + 重启**。摩擦差一个数量级。

**你的现状**：MCP stdio（通用）+ DSH bundle（原生）+ `install.py` 一键写 7 个客户端配置。**已经比竞品强**。

**★ 我的判断与建议**：
- **不要为了"适配民用 agent"去弱化 MCP**——MCP 是你能力的载体，skill 只是入口形态。
- **三层入口**应该并存：
  | 层 | 形态 | 目标用户 |
  |---|---|---|
  | L1 零安装 | **纯 SKILL（生成脚本，用户粘进 Origin）** | 任何 agent 用户 |
  | L2 一键 | **Origin App .opx + 自动写配置（install.py 已有）** | 有 Origin 的人 |
  | L3 原生 | MCP stdio / HTTP / DSH bundle | 开发者、DSH 用户 |
- ★ **补 HTTP transport**：erannave 证明 HTTP 的价值——**多个 agent 客户端共享同一个 Origin 实例**（stdio 每客户端一个进程，抢 COM）。这对"民用 agent 多开"是刚需。
- **profile 收敛**：`ORIGIN_MCP_PROFILE=compact` 已有，建议**默认 compact**（~10 个粗粒度工具），full 才展开 62 个。这是"降低模型选择负担"的正解（不是拆更多 skill）。

---

### D5 分发与可达层

**现状**：GitHub + npm（latest 2.6.2）+ DSH 1024Store（PR #474 待合并）+ dshfind（待重探）+ awesome 列表（PR #5246）。

**★ 我的判断**：
- **npm 契约是硬门槛**：刚踩过 runtime-range 的坑——`@deepseek-ai/dsh*` 依赖**永远不要精确 pin**（写进 CONTRIBUTING 和发布清单）
- **PyPI 发布**该做（pyproject.toml 已就绪）：Python 用户的第一入口是 pip 不是 npm
- **市场可见性依赖第三方**：应有"发布自检清单"（npm pack 白名单 → 契约模拟 → 版本 bump → tag → Release → catalog PR）
- **★ 建议补**：发布前跑一遍 **market 契约模拟脚本**（本地复现七项复核，避免又一次"发完才发现搜不到"）

---

### D6 协议与互操作层

**已落地**：FigureSpec（v2.5.0）、MCP Resources（只读快照）、矩阵、mpl 桥、PPT。

**★ 差距与建议**：
- ★ **缺"用户模板资产"**（Ge-Shun 有）：`origin_template_save`（把当前图存为 .otpu 用户模板）+ `origin_template_apply`（按图型匹配复用）。**这是"组里统一风格"的刚需**，比再堆 10 个绘图工具值钱
- **FigureSpec 该支持"只预览不落盘"**（对方建议的 `render_spec`）——低成本，可加
- **SVG/mxGraph 互操作**：AutoFigure 输出 mxGraph（draw.io）——提供 **"Origin 图 → draw.io 可编辑"** 的出口是差异化（你已有 SVG/EPS/PDF）
- **A2A / 多 agent**：暂不必（生态未定）

---

### D7 可信与可复现层（**你最强的一层，要继续加固**）

**你有的**：读回验证（applied/applied_unverified/rejected）、NaN 防护、导出文件头校验、视觉基准 dHash（12 张）、FigureSpec + plan_hash + PLAN_STALE、稳定错误码 + 恢复映射。

**★ 竞品给的启发（Yike-Ye）**：
- 他们的 **213 项 option 表从 `oPlotIDs.h` 提取**（安装目录里的头文件，不是文档）——你也可以做：**从本机 Origin 安装提取真实 option/plot type 表**，生成 `origin_capabilities` 的权威清单，避免文档与实现脱节
- 他们的 **"readback is never proof"** ——你的 `applied_unverified` 已表达，但可以更显性：在返回值里加 `proof_level: verified | readback_only | unverified` 字段
- 他们的 **known gaps 公开列出** ——你的 COMPATIBILITY.md 已做 ✓（这本文件是你的差异化资产，继续保持）

**建议**：
- P1：`origin_capabilities` 从 `oPlotIDs.h` 等安装文件提取真实 option 表（替代硬编码）
- P1：返回结构统一 `proof_level` 字段
- P2：视觉基准扩展到 20+ 张 + 接入 CI（每次 PR 自动 check）

---

### D8 能力覆盖层

| 能力 | 你 | Ge-Shun | erannave | Yike-Ye | 判断 |
|---|---|---|---|---|---|
| 2D 绘图 | ★★★ | ★★★ | ★ | ★★ | 你最强（模板+排版+样式） |
| 3D/等高线/矩阵 | ★★ | ★★★ | ✗ | ★ | 你有矩阵工具，但缺图像/Data Connector |
| 统计分析 | ★★（自研+confidence_note） | ★★★（Origin 原生） | ✗ | ★ | ★ 建议改走 Origin 原生分析（见下） |
| 拟合 | ★★（初值/固定/加权） | ★★★（Peak Analyzer） | ✗ | ★ | Peak Analyzer 是缺口 |
| 编辑粒度 | ★★★（62 工具） | ★★ | ★ | ★★★ | 你领先 |
| 模板资产 | ✗ | ★★★ | ✗ | ★ | **明确缺口** |
| 数据导入 | ★★★（CJK/编码） | ★★（Connector） | ★ | ★ | 你领先 |
| 交付 | ★★★（OPJU+PPT+delivery） | ★★ | ★ | ★★ | 你领先 |

**★ 我的判断**：
- **统计批建议改用 Origin 原生**（`origin_stats` 目前是 numpy 自研带 confidence_note）——用户既然有正版 Origin，就应该用 Origin 的统计引擎（可信度+可复现性都更高），自研版本退化为"Origin 不可用时的兜底"
- **Peak Analyzer 值得做**（分峰是化学/材料高频需求，你有 peak_fit 但不如原生强大）

---

### D9 生态位与威胁评估

**★ AutoFigure 的真实威胁：低**（不是谦虚，是事实）
- 它做**方法框图/架构图**（"transformer 训练流程"），你做**实验数据图**（XRD/拉曼/循环伏安）
- 重叠场景：论文的"示意图"部分——但那部分用户从不用 Origin 画
- **但它揭示了一个真问题**：用户有时只要"一张能用的矢量图"，不要 OPJU。你的 SVG/PDF/EPS 出口早已具备，**只是没被强调** → README 定位声明已补 ✓

**★ 真正的威胁是 Ge-Shun/origin-mcp**：同赛道、MIT、已上 PyPI、有模板库、有 App 形态、适配 2026/2026b。**它的弱项正是你的强项**：无读回验证体系、无错误码恢复映射、无视觉基准、无 FigureSpec、无中文场景打磨（CJK 路径/编码）。

**★ 你的差异化应该围绕"可信"打，不是"功能多"打**：
> 别人能驱动 Origin，但只有你能**证明**驱动成功了。

---

### D10 治理与工程层

**已有**：pytest（15 项，Origin-aware skip）、offline-test、selftest、chem v2（15 案例）、fine_edit（25 断言）、repro（3 缺陷）、visual_diff（12 张）、SKILL 一致性校验（零幽灵）。

**★ 建议**：
- **CI 化**：GitHub Actions 跑 offline + pytest + 一致性校验（无 Origin 也能跑的那一半）
- **发布清单脚本化**：`scripts/release_check.py`（版本号一致 → npm pack 白名单 → 契约模拟 → pyproject 同步 → CHANGELOG 条目存在）
- **工具数自动化核对**：SKILL/catalog/README 里的工具数由脚本校验（今天 README 还写着 50 就是教训）

---

### D11 安全与权限层

**已有**：LabTalk 破坏命令门禁（confirm）、`DSH_ORIGIN_ALLOWED_ROOTS` 白名单、看门狗。

**建议**：
- 白名单**默认建议值**（文档里给一个典型配置，而不是只说"可配"）
- **审计日志**：把带 `confirm=true` 的破坏性操作与白名单拒绝事件落到本地日志（用户可查"AI 动过什么"）——这是科研场景的加分项

---

### D12 商业化/可持续性

**★ 我的判断**：这是个"名气项目"不是"赚钱项目"（Origin 用户盘子小、且需正版授权）。
- 可持续性的正确姿势：**降低维护成本** > 增加功能
- 建议：把 62 工具收敛（D4）、把测试 CI 化（D10）、把发布脚本化（D10）——**这三件事做完，维护成本降一半**
- 名气变现路径：作为 DSH/国产 agent 生态的"科研垂直标杆案例" → 带动其它项目/合作，而不是直接收费

---

## 3. 改进清单（按性价比排序）

| 优先级 | 项 | 预期收益 | 成本 | 验证方式 |
|---|---|---|---|---|
| **P0** | 端到端粗粒度工具 `origin_figure`（数据+意图→图+导出+交付） | 轮次 8→2，**感知速度提升 50%+** | 中 | 同样任务计时对比 |
| **P0** | 默认参数化 + SKILL 明确"别为调参多问一轮" | 每轮省 1-2 次往返 | 低 | 会话日志统计 |
| **P0** | README/文档工具数等"易腐数字"脚本校验 | 防又一次 50 vs 62 的尴尬 | 低 | CI 断言 |
| **P1** | **Origin App（.opx）形态**：点按钮启动/停止 + 自检 | 新手层从 0 到 1 | 中高 | 在 Origin 2026 装一次跑通 |
| **P1** | **零安装脚本 SKILL**（AI 生成 Origin 脚本，用户粘进 Script Window） | 覆盖所有 agent 用户 | 中 | 3 个真实场景验证脚本可执行 |
| **P1** | **用户模板资产**（save/apply .otpu + 按图型匹配） | 组里统一风格刚需 | 中 | 同数据套模板复现 |
| **P1** | HTTP transport（多客户端共享 Origin） | 多 agent 场景 | 中 | 两个客户端同时连 |
| **P1** | 页堆积治理（超阈值提示/自动清理） | 防 30-59s 的卡顿 | 低 | 793 页场景复测 |
| **P1** | 发布清单脚本 + market 契约模拟 | 防"发完搜不到" | 低 | 本地复现七项复核 |
| **P2** | PyPI 发布 | Python 用户入口 | 低 | `pip install dsh-origin-plugin` |
| **P2** | `origin_capabilities` 从 `oPlotIDs.h` 提取真实 option 表 | 消灭文档/实现脱节 | 中 | 与硬编码表 diff |
| **P2** | 统计批改走 Origin 原生（自研兜底） | 可信度+可复现 | 中 | 与 R/SPSS 对照 |
| **P2** | Peak Analyzer 接入 | 化学/材料高频 | 中 | 分峰案例 |
| **P2** | `proof_level` 统一字段 | 可信度显式化 | 低 | 返回值断言 |
| **P2** | 视觉基准扩到 20+ 张 + CI | 外观回归防线 | 低 | PR 自动 check |
| **不做** | 进程隔离 bridge | — | 高 | 见 D1 触发条件 |
| **不做** | 拆更多 skill | — | — | 见 D4（应收敛而非拆分） |

---

## 4. 速度优化专项：skill 到底能加速多少

### 4.1 先拆时间（一图胜千言）

```
一次"画图并导出"的典型耗时
├─ 模型决策（读 schema/选工具/填参/读结果）  ← 60~80%  ★ 主战场
├─ MCP 往返（JSON-RPC + 进程调度）           ← 5~10%
├─ Origin 执行（COM 调用，毫秒级）           ← 5~10%
├─ 导出 IO（1200px PNG + 文件头校验）        ← 5~15%
└─ Origin 冷启动（仅首次）                   ← 一次性 3~15s
```

### 4.2 ★ skill 能加速的边界（重要，别误判）

| 手段 | 能省什么 | 不能省什么 | 预期 |
|---|---|---|---|
| **SKILL 决策流**（已有） | 模型在"选哪个工具"上的思考 | 实际调用次数、COM 耗时 | 10~20% |
| **默认参数**（SKILL 明说） | "要不要调这个参数"的往返 | — | 10~20% |
| **端到端粗粒度工具** | **调用轮次本身** | — | **30~50%** ★★ |
| **预热/保活** | 冷启动 | — | 首次省 3~15s |
| **降默认分辨率** | 导出 IO | — | 每张省 0.5~1s |
| **页堆积治理** | 枚举耗时 | — | 极端场景省 30~50s |
| 优化 COM / 进程隔离 | 那 5~10% | — | <10%，且成本高 |

**★ 结论**：**只写 SKILL 不改工具，速度提升上限约 30%；SKILL + 端到端工具 + 默认参数，才能到 50-70%。** 用户问的"能不能通过 skill 优化流程加速"——**能，但必须配合工具收敛，单靠提示词工程是伪优化**。

### 4.3 具体可落地的"快路径"设计

1. **`origin_figure`（一个工具做完）**：传数据 + `intent`（如 "journal line, 2 series"）→ 内部完成 write→plot→style→verify→export→delivery，返回交付目录
2. **SKILL 顶层写死"快路径"**：前 20 行只给这一条路径，专家路径折叠到附录（今天的 SKILL 主循环图已经是这个思路，但可以把"最短路径"提到最前并标注"90% 场景用这个"）
3. **禁止重复 inspect**：SKILL 明确"同一图页 inspect 一次即可，不要每次编辑前都 inspect"
4. **verify 合并进 delivery**：省一轮
5. **预热**：App 启动/首次 connect 后保持 Origin 常驻；提供 `origin_status` 的 `warmup` 参数

---

## 5. 未来路线（三阶段，带触发条件）

### 阶段 A（1-2 周）：提速与收敛 —— 直接回应用户痛点
- `origin_figure` 端到端工具 + 默认参数
- compact profile 默认化（10 个粗粒度工具在前）
- 页堆积治理 + 预热
- 文档数字脚本校验 + 发布清单脚本

### 阶段 B（1-2 月）：入口形态 —— 解决"进不了用户的手"
- **Origin App（.opx）**：点按钮启动/停止/自检
- **零安装脚本 SKILL**
- HTTP transport
- 用户模板资产（save/apply）
- PyPI 发布

### 阶段 C（3-6 月）：深度可信 —— 加固护城河
- `oPlotIDs.h` 真实能力表
- 统计改 Origin 原生 + Peak Analyzer
- 视觉基准 20+ 张 + CI
- 审计日志
- FigureSpec 生态（render_spec、模板库互操作）

### 明确不做（写下来防摇摆）
- 进程隔离（等触发条件）
- 文生图/示意插图（AutoFigure 赛道）
- 拆更多 skill（应收敛）
- 跨平台（Origin 只在 Windows）

---

## 6. 附：我的独立判断（8 条，敢下结论）

1. ★ **"工具多"不是卖点，"工具可信"才是**。62 个工具对外是噪音，对内是资产——把对外收敛到 10 个，能力一点不丢，速度快一倍。
2. ★ **进程隔离被高估了**。真正影响用户体验的是"崩了能不能恢复"（你已解决），不是"会不会崩"。
3. ★ **AutoFigure 威胁被高估**。它是示意图赛道；你真正该盯的是 Ge-Shun/origin-mcp。
4. ★ **速度问题的 80% 在 AI 轮次**。先砍轮次，再谈执行优化。
5. ★ **新手层的答案是按钮和脚本，不是 API**。.opx + 可粘贴脚本 > 任何 MCP 配置教程。
6. ★ **模板资产是当前最大功能缺口**。组里统一风格是科研刚需，且竞品已有。
7. ★ **"读回"要分级表达**（verified / readback_only / unverified）。Yike-Ye 那句 "readback is never proof" 值得印在 README 上。
8. ★ **你的 COMPATIBILITY.md 是被低估的资产**——15 条实测失败场景，没有任何竞品有。这是"可信"最好的广告，应该主动对外讲（README 前置、发文、issue 引用）。

---

*本文基于 2026-09-17 的公开信息与本项目 v2.6.2 状态撰写。竞品数据来自其公开 README/文档，可能与其最新实现有出入，立项前建议复核对端仓库。*
